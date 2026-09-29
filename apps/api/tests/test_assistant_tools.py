"""Phase 12: unit tests for every read-only assistant tool
(app/services/assistant/tools.py) -- each tool is exercised against a
real, fully-seeded Phase 2-9 pipeline (same sequence
test_digital_twin_api.py's own full-pipeline smoke test uses) plus the
not-found path, proving tools never raise and always surface the
underlying limitations/provenance verbatim.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import LineString
from sqlalchemy.orm import Session

from app.services.assistant import tools


def _create_project(client: TestClient, name: str = "Assistant Test Project") -> str:
    return client.post("/projects", json={"name": name}).json()["id"]


def _make_dem(tmp_path: Path, name: str, crs: str = "EPSG:32643") -> Path:
    rows, cols = 16, 16
    transform = from_origin(0, 160, 10, 10)
    elevation = np.zeros((rows, cols), dtype="float32")
    r_idx = np.arange(rows).reshape(-1, 1)
    elevation[:, 9:] = np.broadcast_to((rows - r_idx) * 20.0, (rows, cols - 9))
    path = tmp_path / f"dem_{name}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    return path


def _seed_full_pipeline(client: TestClient, tmp_path: Path) -> dict:
    """Real Phase 2->9 chain via the live HTTP API (mirrors
    test_digital_twin_api.py::test_full_pipeline_smoke_terrain_to_twin),
    returning every id an assistant tool test needs.
    """
    project_id = _create_project(client)

    dem_path = _make_dem(tmp_path, "assistant")
    dem = client.post(
        f"/projects/{project_id}/datasets",
        data={"dataset_type": "dem"},
        files={"file": ("dem.tif", dem_path.open("rb"), "image/tiff")},
    ).json()
    slope = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"}).json()
    assert slope["status"] == "validated"

    hazard_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Assistant landslide", "dem_dataset_id": dem["id"]},
    )
    assert hazard_resp.status_code == 201, hazard_resp.text
    hazard_body = hazard_resp.json()
    hazard_scenario = hazard_body["scenario"]
    landslide = hazard_body["datasets"][0]

    roads_gdf = gpd.GeoDataFrame({"geometry": [LineString([(10, 75), (150, 75)])]}, crs="EPSG:32643")
    roads_path = tmp_path / "assistant_roads.geojson"
    roads_gdf.to_file(roads_path, driver="GeoJSON")
    roads = client.post(
        f"/projects/{project_id}/datasets",
        data={"dataset_type": "roads"},
        files={"file": ("roads.geojson", roads_path.open("rb"), "application/geo+json")},
    ).json()

    exposure_resp = client.post(
        f"/projects/{project_id}/exposure-analyses",
        json={"name": "Assistant exposure", "hazard_dataset_id": landslide["id"], "exposure_dataset_id": roads["id"]},
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    exposure = exposure_resp.json()["analysis"]

    risk_resp = client.post(
        f"/projects/{project_id}/risk-analyses",
        json={
            "name": "Assistant risk", "exposure_analysis_id": exposure["id"],
            "vulnerability_weight": 0.6, "consequence_weight": 0.9,
        },
    )
    assert risk_resp.status_code == 201, risk_resp.text
    risk = risk_resp.json()["analysis"]

    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    olon, olat = t.transform(10, 75)
    dlon, dlat = t.transform(150, 75)
    route_resp = client.post(
        f"/projects/{project_id}/route-analyses",
        json={
            "name": "Assistant route", "road_dataset_id": roads["id"], "hazard_dataset_id": landslide["id"],
            "origin": {"lon": olon, "lat": olat}, "destination": {"lon": dlon, "lat": dlat},
            "hazard_penalty_weight": 1.0,
        },
    )
    assert route_resp.status_code == 201, route_resp.text
    route = route_resp.json()["analysis"]

    twin = client.post(f"/projects/{project_id}/digital-twin", json={"name": "Assistant Twin"}).json()
    for dataset_id in (dem["id"], landslide["id"], roads["id"]):
        reg = client.post(f"/digital-twins/{twin['id']}/layers", json={"dataset_id": dataset_id})
        assert reg.status_code == 201, reg.text

    scenario = client.post(f"/digital-twins/{twin['id']}/scenarios", json={"name": "Assistant Scenario"}).json()

    return {
        "project_id": project_id,
        "dem": dem,
        "roads": roads,
        "hazard_scenario": hazard_scenario,
        "landslide_dataset": landslide,
        "exposure": exposure,
        "risk": risk,
        "route": route,
        "twin": twin,
        "scenario": scenario,
    }


# --- Digital Twin -----------------------------------------------------------------


def test_get_twin_state_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_twin_state(db_session, seed["twin"]["id"])
    assert evidence.status == "success"
    assert evidence.tool == "get_twin_state"
    assert "terrain" in evidence.data["layers_by_category"]
    assert evidence.limitations  # DIGITAL_TWIN_LIMITATIONS is always non-empty


def test_get_twin_state_unavailable_for_missing_twin(db_session: Session) -> None:
    evidence = tools.get_twin_state(db_session, "does-not-exist")
    assert evidence.status == "unavailable"
    assert evidence.data is None
    assert evidence.detail is not None


# --- Scenario Lab ------------------------------------------------------------------


def test_get_scenario_state_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_scenario_state(db_session, seed["scenario"]["id"])
    assert evidence.status == "success"
    assert "effective_layers" in evidence.data
    assert "derived_analyses" in evidence.data


def test_get_scenario_state_unavailable_for_missing_scenario(db_session: Session) -> None:
    evidence = tools.get_scenario_state(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


def test_compare_scenario_analyses_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    # A second risk analysis (different weights) to compare against the first.
    risk2_resp = client.post(
        f"/projects/{seed['project_id']}/risk-analyses",
        json={
            "name": "Assistant risk 2", "exposure_analysis_id": seed["exposure"]["id"],
            "vulnerability_weight": 0.9, "consequence_weight": 0.9,
        },
    )
    assert risk2_resp.status_code == 201, risk2_resp.text
    risk2 = risk2_resp.json()["analysis"]

    evidence = tools.compare_scenario_analyses(db_session, "risk", seed["risk"]["id"], risk2["id"])
    assert evidence.status == "success"
    assert "diff" in evidence.data
    assert isinstance(evidence.limitations, list)  # comparability_warnings, possibly empty


def test_compare_scenario_analyses_unavailable_for_unsupported_type(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.compare_scenario_analyses(db_session, "not-a-type", seed["risk"]["id"], seed["risk"]["id"])
    assert evidence.status == "unavailable"


# --- Hazard ------------------------------------------------------------------------


def test_list_hazard_scenarios_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.list_hazard_scenarios(db_session, seed["project_id"])
    assert evidence.status == "success"
    assert evidence.data["total_count"] == 1
    assert evidence.data["items"][0]["hazard_type"] == "landslide"


def test_get_hazard_scenario_success_includes_dataset_limitations(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_hazard_scenario(db_session, seed["hazard_scenario"]["id"])
    assert evidence.status == "success"
    assert evidence.data["scenario"]["hazard_type"] == "landslide"
    assert evidence.data["datasets"][0]["dataset_type"] == "landslide_susceptibility"
    # limitations come from the output Dataset's provenance, not the scenario row itself.
    assert evidence.limitations
    assert any("susceptibility" in text.lower() for text in evidence.limitations)


def test_get_hazard_scenario_unavailable_for_missing_id(db_session: Session) -> None:
    evidence = tools.get_hazard_scenario(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


# --- Exposure ------------------------------------------------------------------------


def test_list_exposure_analyses_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.list_exposure_analyses(db_session, seed["project_id"])
    assert evidence.status == "success"
    assert evidence.data["total_count"] == 1


def test_get_exposure_analysis_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_exposure_analysis(db_session, seed["exposure"]["id"])
    assert evidence.status == "success"
    assert "by_class" in evidence.data["results"]
    assert evidence.limitations  # EXPOSURE_LIMITATIONS is always non-empty


def test_get_exposure_analysis_unavailable_for_missing_id(db_session: Session) -> None:
    evidence = tools.get_exposure_analysis(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


# --- Risk --------------------------------------------------------------------------


def test_list_risk_analyses_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.list_risk_analyses(db_session, seed["project_id"])
    assert evidence.status == "success"
    assert evidence.data["total_count"] == 1


def test_get_risk_analysis_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_risk_analysis(db_session, seed["risk"]["id"])
    assert evidence.status == "success"
    assert "by_class" in evidence.data["results"]
    assert evidence.limitations


def test_find_highest_risk_classes_ranks_by_risk_score_descending(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.find_highest_risk_classes(db_session, seed["risk"]["id"], top_n=5)
    assert evidence.status == "success"
    ranked = evidence.data["ranked_classes"]
    scores = [entry["risk_score"] for entry in ranked]
    assert scores == sorted(scores, reverse=True)
    assert evidence.limitations  # RISK_LIMITATIONS carried through


def test_find_highest_risk_classes_unavailable_for_missing_id(db_session: Session) -> None:
    evidence = tools.find_highest_risk_classes(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


# --- Routing -------------------------------------------------------------------------


def test_list_route_analyses_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.list_route_analyses(db_session, seed["project_id"])
    assert evidence.status == "success"
    assert evidence.data["total_count"] == 1


def test_get_route_analysis_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_route_analysis(db_session, seed["route"]["id"])
    assert evidence.status == "success"
    assert "feasible" in evidence.data["results"]
    assert evidence.limitations  # ROUTING_LIMITATIONS is always non-empty


def test_get_route_analysis_unavailable_for_missing_id(db_session: Session) -> None:
    evidence = tools.get_route_analysis(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


# --- Dataset ------------------------------------------------------------------------


def test_get_dataset_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_dataset(db_session, seed["dem"]["id"])
    assert evidence.status == "success"
    assert evidence.data["dataset_type"] == "dem"


def test_get_dataset_unavailable_for_missing_id(db_session: Session) -> None:
    evidence = tools.get_dataset(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


def test_get_dataset_geojson_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_dataset_geojson(db_session, seed["roads"]["id"])
    assert evidence.status == "success"
    assert evidence.data["type"] == "FeatureCollection"
    assert len(evidence.data["features"]) >= 1


def test_get_dataset_geojson_unavailable_for_raster_dataset(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.get_dataset_geojson(db_session, seed["dem"]["id"])
    assert evidence.status == "unavailable"


def test_list_project_datasets_success(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    evidence = tools.list_project_datasets(db_session, seed["project_id"])
    assert evidence.status == "success"
    assert evidence.data["total_count"] >= 2  # dem + roads, at minimum
    dataset_types = {item["dataset_type"] for item in evidence.data["items"]}
    assert "dem" in dataset_types
    assert "roads" in dataset_types


def test_list_project_datasets_unavailable_for_missing_project(db_session: Session) -> None:
    evidence = tools.list_project_datasets(db_session, "does-not-exist")
    assert evidence.status == "unavailable"


# --- Cross-cutting: tools never raise ------------------------------------------------


def test_every_tool_returns_unavailable_not_an_exception_for_a_bogus_id(db_session: Session) -> None:
    bogus = "totally-bogus-id"
    results = [
        tools.get_twin_state(db_session, bogus),
        tools.get_scenario_state(db_session, bogus),
        tools.get_hazard_scenario(db_session, bogus),
        tools.get_exposure_analysis(db_session, bogus),
        tools.get_risk_analysis(db_session, bogus),
        tools.find_highest_risk_classes(db_session, bogus),
        tools.get_route_analysis(db_session, bogus),
        tools.get_dataset(db_session, bogus),
        tools.get_dataset_geojson(db_session, bogus),
        tools.compare_scenario_analyses(db_session, "risk", bogus, bogus),
    ]
    assert all(r.status in ("unavailable", "error") for r in results)
    assert all(r.data is None for r in results)
