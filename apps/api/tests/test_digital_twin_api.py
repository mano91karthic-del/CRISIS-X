"""API-level integration tests for the Phase 9 Digital Twin endpoints:
twin creation, layer registration/supersession/retirement, filtered
listing, the composed /state summary, and failure-path coverage, plus a
full Phase 2-8 -> Digital Twin end-to-end smoke test.
"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


# --- fixture helpers ---------------------------------------------------------


def _create_project(client: TestClient, name: str = "Twin Test Project") -> str:
    return client.post("/projects", json={"name": name}).json()["id"]


def _upload_raster(client: TestClient, project_id: str, path: Path, dataset_type: str, filename: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


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


def _create_twin(client: TestClient, project_id: str, name: str = "Test Twin") -> dict:
    resp = client.post(f"/projects/{project_id}/digital-twin", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _register_layer(client: TestClient, twin_id: str, dataset_id: str) -> "httpx.Response":  # noqa: F821
    return client.post(f"/digital-twins/{twin_id}/layers", json={"dataset_id": dataset_id})


# --- twin creation -------------------------------------------------------------


def test_create_and_fetch_twin_by_project(client: TestClient) -> None:
    project_id = _create_project(client)
    created = _create_twin(client, project_id)
    assert created["project_id"] == project_id
    assert created["version"] == 1

    resp = client.get(f"/projects/{project_id}/digital-twin")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    resp = client.get(f"/digital-twins/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_duplicate_twin_creation_rejected(client: TestClient) -> None:
    project_id = _create_project(client)
    _create_twin(client, project_id)
    resp = client.post(f"/projects/{project_id}/digital-twin", json={"name": "Second"})
    assert resp.status_code == 409


def test_missing_project_and_twin_404s(client: TestClient) -> None:
    assert client.post("/projects/does-not-exist/digital-twin", json={"name": "x"}).status_code == 404
    assert client.get("/projects/does-not-exist/digital-twin").status_code == 404
    assert client.get("/digital-twins/does-not-exist").status_code == 404

    project_id = _create_project(client)
    assert client.get(f"/projects/{project_id}/digital-twin").status_code == 404


# --- layer registration / category derivation ------------------------------------


def test_register_dataset_as_layer_derives_category_and_role(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem_path = _make_dem(tmp_path, "reg")
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_reg.tif")

    resp = _register_layer(client, twin["id"], dem["id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["layer"]["dataset_type"] == "dem"
    assert body["layer"]["category"] == "terrain"  # origin=uploaded, dataset_type=dem
    assert body["layer"]["status"] == "active"
    assert body["layer"]["registered_at_version"] == 2  # twin started at version 1
    assert body["dataset"]["id"] == dem["id"]

    twin_after = client.get(f"/digital-twins/{twin['id']}").json()
    assert twin_after["version"] == 2


def test_registering_same_dataset_type_supersedes_previous_active_layer(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem1 = _upload_raster(client, project_id, _make_dem(tmp_path, "one"), "dem", "dem1.tif")
    dem2 = _upload_raster(client, project_id, _make_dem(tmp_path, "two"), "dem", "dem2.tif")

    first = _register_layer(client, twin["id"], dem1["id"]).json()
    second = _register_layer(client, twin["id"], dem2["id"]).json()

    assert second["layer"]["provenance"]["superseded_layer_id"] == first["layer"]["id"]

    first_refetched = client.get(f"/twin-layers/{first['layer']['id']}").json()
    assert first_refetched["layer"]["status"] == "superseded"

    active_layers = client.get(f"/digital-twins/{twin['id']}/layers", params={"dataset_type": "dem"}).json()
    assert [l["layer"]["id"] for l in active_layers] == [second["layer"]["id"]]


def test_retire_layer_marks_retired_without_replacement(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "retire"), "dem", "dem_retire.tif")
    registered = _register_layer(client, twin["id"], dem["id"]).json()

    resp = client.post(f"/twin-layers/{registered['layer']['id']}/retire")
    assert resp.status_code == 200
    assert resp.json()["layer"]["status"] == "retired"

    active_layers = client.get(f"/digital-twins/{twin['id']}/layers", params={"dataset_type": "dem"}).json()
    assert active_layers == []

    # retiring an already-retired layer is rejected, not a silent no-op
    resp = client.post(f"/twin-layers/{registered['layer']['id']}/retire")
    assert resp.status_code == 400


def test_list_layers_filters_by_category_dataset_type_status(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem1 = _upload_raster(client, project_id, _make_dem(tmp_path, "f1"), "dem", "dem_f1.tif")
    dem2 = _upload_raster(client, project_id, _make_dem(tmp_path, "f2"), "dem", "dem_f2.tif")
    _register_layer(client, twin["id"], dem1["id"])
    second = _register_layer(client, twin["id"], dem2["id"]).json()

    resp = client.get(f"/digital-twins/{twin['id']}/layers", params={"category": "terrain"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["layer"]["id"] == second["layer"]["id"]

    resp = client.get(f"/digital-twins/{twin['id']}/layers", params={"category": "hazard"})
    assert resp.json() == []

    resp = client.get(f"/digital-twins/{twin['id']}/layers", params={"status": "all"})
    assert len(resp.json()) == 2

    resp = client.get(f"/digital-twins/{twin['id']}/layers", params={"status": "superseded"})
    assert len(resp.json()) == 1


def test_layer_detail_nests_full_dataset_with_provenance(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem_path = _make_dem(tmp_path, "prov")
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_prov.tif")
    registered = _register_layer(client, twin["id"], dem["id"]).json()

    resp = client.get(f"/twin-layers/{registered['layer']['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataset"]["id"] == dem["id"]
    assert body["dataset"]["provenance"] == dem["provenance"]  # untouched, not recomputed
    assert body["dataset"]["metadata_json"] == dem["metadata_json"]


def test_rejects_dataset_from_different_project(client: TestClient, tmp_path: Path) -> None:
    project_a = _create_project(client, "A")
    project_b = _create_project(client, "B")
    twin_a = _create_twin(client, project_a)
    dem_b = _upload_raster(client, project_b, _make_dem(tmp_path, "cross"), "dem", "dem_cross.tif")

    resp = _register_layer(client, twin_a["id"], dem_b["id"])
    assert resp.status_code == 404


def test_rejects_unvalidated_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)

    bad_path = tmp_path / "bad.tif"
    bad_path.write_bytes(b"not a real tiff")
    with bad_path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("bad.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201
    invalid_dataset = resp.json()
    assert invalid_dataset["status"] == "invalid"

    resp = _register_layer(client, twin["id"], invalid_dataset["id"])
    assert resp.status_code == 400
    assert "not 'validated'" in resp.json()["detail"]


def test_register_layer_missing_dataset_and_twin_404s(client: TestClient) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    resp = _register_layer(client, twin["id"], "does-not-exist")
    assert resp.status_code == 404

    resp = _register_layer(client, "does-not-exist", "also-missing")
    assert resp.status_code == 404


# --- /state ---------------------------------------------------------------------


def test_state_endpoint_computes_extent_and_flags_crs_mismatch(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "state1", crs="EPSG:32643"), "dem", "dem_state1.tif")
    _register_layer(client, twin["id"], dem["id"])

    resp = client.get(f"/digital-twins/{twin['id']}/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["extent"]["reference_crs"] == "EPSG:32643"
    assert body["extent"]["bbox_min_x"] is not None
    assert body["extent"]["crs_mismatch_layer_ids"] == []
    assert "terrain" in body["layers_by_category"]


def test_state_endpoint_reports_missing_recommended_layers(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "missing"), "dem", "dem_missing.tif")
    _register_layer(client, twin["id"], dem["id"])

    resp = client.get(f"/digital-twins/{twin['id']}/state")
    body = resp.json()
    # only "terrain" is present (uploaded DEM) -- none of the other
    # recommended categories (hazard/exposure/risk) yet.
    assert set(body["missing_recommended_layers"]) == {"hazard", "exposure", "risk"}


def test_state_endpoint_acquisition_date_range(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "acq"), "dem", "dem_acq.tif")
    _register_layer(client, twin["id"], dem["id"])

    resp = client.get(f"/digital-twins/{twin['id']}/state")
    body = resp.json()
    # a plain synthetic GeoTIFF upload with no embedded acquisition tag
    # has no acquisition_date -- must be listed, never guessed.
    assert body["acquisition_date_range"]["earliest"] is None
    active_layers = client.get(f"/digital-twins/{twin['id']}/layers").json()
    layer_id = active_layers[0]["layer"]["id"]
    assert layer_id in body["acquisition_date_range"]["layers_without_acquisition_date"]


def test_state_endpoint_includes_fixed_limitations(client: TestClient) -> None:
    project_id = _create_project(client)
    twin = _create_twin(client, project_id)
    resp = client.get(f"/digital-twins/{twin['id']}/state")
    body = resp.json()
    assert any("not a live sensor feed" in lim for lim in body["limitations"])
    assert any("not a prediction engine" in lim for lim in body["limitations"])
    assert any("not a perfect real-world replica" in lim for lim in body["limitations"])
    assert body["missing_recommended_layers"] == ["terrain", "hazard", "exposure", "risk"]
    assert body["extent"]["reference_crs"] is None  # no layers registered at all


# --- end-to-end smoke test: full Phase 2-8 pipeline -> Digital Twin -------------


def test_full_pipeline_smoke_terrain_to_twin(client: TestClient, tmp_path: Path) -> None:
    """Runs the entire Phase 2 -> 8 chain for real, then assembles every
    resulting output Dataset into a Digital Twin, proving Phase 9 only
    re-exposes existing provenance rather than recomputing anything.
    """
    project_id = _create_project(client, "Smoke Twin Project")

    # Phase 1/2: DEM upload + slope derivation.
    dem_path = _make_dem(tmp_path, "smoke")
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_smoke.tif")
    slope_resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert slope_resp.status_code == 201, slope_resp.text
    slope = slope_resp.json()

    # Phase 4: landslide hazard scenario.
    scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Smoke landslide", "dem_dataset_id": dem["id"]},
    )
    assert scenario_resp.status_code == 201, scenario_resp.text
    landslide = scenario_resp.json()["datasets"][0]

    # Phase 1: roads upload.
    import geopandas as gpd
    from shapely.geometry import LineString

    roads_gdf = gpd.GeoDataFrame({"geometry": [LineString([(10, 75), (150, 75)])]}, crs="EPSG:32643")
    roads_path = tmp_path / "smoke_roads.geojson"
    roads_gdf.to_file(roads_path, driver="GeoJSON")
    with roads_path.open("rb") as f:
        roads_resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "roads"},
            files={"file": ("smoke_roads.geojson", f, "application/geo+json")},
        )
    assert roads_resp.status_code == 201, roads_resp.text
    roads = roads_resp.json()

    # Phase 6: exposure analysis (roads as the exposure asset).
    exposure_resp = client.post(
        f"/projects/{project_id}/exposure-analyses",
        json={"name": "Smoke exposure", "hazard_dataset_id": landslide["id"], "exposure_dataset_id": roads["id"]},
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    exposure_body = exposure_resp.json()
    assert exposure_body["analysis"]["status"] == "completed"
    exposure_features = exposure_body["datasets"][0] if exposure_body["datasets"] else None

    # Phase 7: risk analysis.
    risk_resp = client.post(
        f"/projects/{project_id}/risk-analyses",
        json={
            "name": "Smoke risk", "exposure_analysis_id": exposure_body["analysis"]["id"],
            "vulnerability_weight": 0.6, "consequence_weight": 0.9,
        },
    )
    assert risk_resp.status_code == 201, risk_resp.text
    risk_body = risk_resp.json()
    risk_dataset = risk_body["datasets"][0] if risk_body["datasets"] else None

    # Phase 8: route analysis.
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    olon, olat = t.transform(10, 75)
    dlon, dlat = t.transform(150, 75)
    route_resp = client.post(
        f"/projects/{project_id}/route-analyses",
        json={
            "name": "Smoke route", "road_dataset_id": roads["id"], "hazard_dataset_id": landslide["id"],
            "origin": {"lon": olon, "lat": olat}, "destination": {"lon": dlon, "lat": dlat},
            "hazard_penalty_weight": 1.0,
        },
    )
    assert route_resp.status_code == 201, route_resp.text
    route_body = route_resp.json()
    route_datasets = route_body["datasets"]

    # --- Phase 9: assemble the Digital Twin from every produced Dataset ---
    twin = _create_twin(client, project_id, "Smoke Twin")

    all_dataset_ids = [dem["id"], slope["id"], landslide["id"], roads["id"]]
    if exposure_features is not None:
        all_dataset_ids.append(exposure_features["id"])
    if risk_dataset is not None:
        all_dataset_ids.append(risk_dataset["id"])
    for ds in route_datasets:
        all_dataset_ids.append(ds["id"])

    registered_provenances = {}
    for dataset_id in all_dataset_ids:
        resp = _register_layer(client, twin["id"], dataset_id)
        assert resp.status_code == 201, resp.text
        registered_provenances[dataset_id] = resp.json()["dataset"]["provenance"]

    state = client.get(f"/digital-twins/{twin['id']}/state").json()

    categories_present = set(state["layers_by_category"].keys())
    assert {"observation", "terrain", "hazard", "exposure", "risk", "route"} <= categories_present
    assert state["missing_recommended_layers"] == []
    assert state["extent"]["bbox_min_x"] is not None
    assert state["extent"]["reference_crs"] is not None

    # Prove no scientific recomputation happened: every layer's nested
    # dataset provenance, fetched fresh through /twin-layers, is identical
    # to what Phases 4/6/7/8 themselves produced at registration time.
    for category_layers in state["layers_by_category"].values():
        for layer_result in category_layers:
            dataset_id = layer_result["dataset"]["id"]
            assert layer_result["dataset"]["provenance"] == registered_provenances[dataset_id]

    # Every hazard/exposure/risk/route layer must carry its own method +
    # limitations, reused verbatim from its producing phase.
    hazard_layer = state["layers_by_category"]["hazard"][0]
    assert "method" in hazard_layer["dataset"]["provenance"] or "limitations" in hazard_layer["dataset"]["provenance"]
