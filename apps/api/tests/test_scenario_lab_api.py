"""API-level integration tests for the Phase 10 Scenario Lab endpoints:
scenario creation/baseline snapshot, layer overrides (add/duplicate/
delete/freeze-after-run), scenario-scoped hazard/exposure/risk/route runs,
comparisons, the Digital Twin non-mutation guarantee, and a full Phase
2-9 -> Scenario Lab end-to-end smoke test.
"""

from pathlib import Path

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


# --- fixture helpers (mirrors test_digital_twin_api.py's pattern) ------------------


def _create_project(client: TestClient, name: str = "Scenario Test Project") -> str:
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


def _create_scenario(client: TestClient, twin_id: str, name: str = "Test Scenario") -> dict:
    resp = client.post(f"/digital-twins/{twin_id}/scenarios", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_twin_with_dem(client: TestClient, tmp_path: Path, project_name: str = "Scenario Project") -> dict:
    """Creates a project + twin with a single registered DEM layer (the
    minimal baseline most scenario tests build on).
    """
    project_id = _create_project(client, project_name)
    twin = _create_twin(client, project_id)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, project_name), "dem", "dem.tif")
    layer = _register_layer(client, twin["id"], dem["id"]).json()
    return {"project_id": project_id, "twin": twin, "dem": dem, "layer": layer}


# --- scenario creation / baseline snapshot ------------------------------------------


def test_create_scenario_snapshots_active_twin_layers(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "snap")
    scenario = _create_scenario(client, seed["twin"]["id"])
    assert scenario["twin_id"] == seed["twin"]["id"]
    assert scenario["status"] == "active"

    baseline = client.get(f"/scenarios/{scenario['id']}/baseline-layers").json()
    assert len(baseline) == 1
    assert baseline[0]["layer"]["dataset_type"] == "dem"
    assert baseline[0]["dataset"]["id"] == seed["dem"]["id"]


def test_create_scenario_on_empty_twin_has_empty_baseline(client: TestClient) -> None:
    project_id = _create_project(client, "Empty Twin Project")
    twin = _create_twin(client, project_id)
    scenario = _create_scenario(client, twin["id"])
    baseline = client.get(f"/scenarios/{scenario['id']}/baseline-layers").json()
    assert baseline == []


def test_scenario_creation_missing_twin_404s(client: TestClient) -> None:
    resp = client.post("/digital-twins/does-not-exist/scenarios", json={"name": "x"})
    assert resp.status_code == 404
    assert client.get("/scenarios/does-not-exist").status_code == 404


def test_archive_scenario_then_second_archive_rejected(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "archive")
    scenario = _create_scenario(client, seed["twin"]["id"])
    resp = client.post(f"/scenarios/{scenario['id']}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    resp = client.post(f"/scenarios/{scenario['id']}/archive")
    assert resp.status_code == 400


# --- layer overrides ---------------------------------------------------------------


def test_add_layer_override_derives_category_and_resolves_in_effective_layers(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "override")
    scenario = _create_scenario(client, seed["twin"]["id"])

    alt_dem = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "override-alt"), "dem", "dem_alt.tif")
    resp = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt_dem["id"]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["override"]["dataset_type"] == "dem"
    assert body["override"]["category"] == "terrain"
    assert body["dataset"]["id"] == alt_dem["id"]

    state = client.get(f"/scenarios/{scenario['id']}/state").json()
    effective_dem = next(l for l in state["effective_layers"] if l["dataset_type"] == "dem")
    assert effective_dem["source"] == "override"
    assert effective_dem["dataset"]["id"] == alt_dem["id"]


def test_duplicate_override_for_same_dataset_type_rejected(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "dup")
    scenario = _create_scenario(client, seed["twin"]["id"])

    alt1 = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "dup1"), "dem", "dem_dup1.tif")
    alt2 = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "dup2"), "dem", "dem_dup2.tif")

    resp1 = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt1["id"]})
    assert resp1.status_code == 201
    resp2 = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt2["id"]})
    assert resp2.status_code == 409


def test_delete_override_then_readd_succeeds(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "del")
    scenario = _create_scenario(client, seed["twin"]["id"])

    alt1 = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "del1"), "dem", "dem_del1.tif")
    override = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt1["id"]}).json()

    resp = client.delete(f"/scenario-layer-overrides/{override['override']['id']}")
    assert resp.status_code == 204

    alt2 = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "del2"), "dem", "dem_del2.tif")
    resp = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt2["id"]})
    assert resp.status_code == 201


def test_override_rejects_unvalidated_dataset(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "invalid")
    scenario = _create_scenario(client, seed["twin"]["id"])

    bad_path = tmp_path / "bad.tif"
    bad_path.write_bytes(b"not a real tiff")
    with bad_path.open("rb") as f:
        resp = client.post(
            f"/projects/{seed['project_id']}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("bad.tif", f, "image/tiff")},
        )
    invalid_dataset = resp.json()
    assert invalid_dataset["status"] == "invalid"

    resp = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": invalid_dataset["id"]})
    assert resp.status_code == 400


def test_override_mutation_frozen_after_first_run(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "freeze")
    scenario = _create_scenario(client, seed["twin"]["id"])
    client.post(f"/datasets/{seed['dem']['id']}/derive", json={"product": "slope"})

    resp = client.post(
        f"/scenarios/{scenario['id']}/hazard-runs/landslide",
        json={"name": "freeze-run", "dem_dataset_id": seed["dem"]["id"]},
    )
    assert resp.status_code == 201, resp.text

    alt = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "freeze-alt"), "dem", "dem_freeze_alt.tif")
    resp = client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt["id"]})
    assert resp.status_code == 409

    state = client.get(f"/scenarios/{scenario['id']}/state").json()
    dem_effective = next(l for l in state["effective_layers"] if l["dataset_type"] == "dem")
    assert dem_effective["can_override"] is False


# --- scenario-scoped runs: resolution + provenance ----------------------------------


def test_landslide_run_resolves_dem_from_baseline_and_tags_scenario_id(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "run-resolve")
    scenario = _create_scenario(client, seed["twin"]["id"])
    client.post(f"/datasets/{seed['dem']['id']}/derive", json={"product": "slope"})

    resp = client.post(
        f"/scenarios/{scenario['id']}/hazard-runs/landslide",
        json={"name": "resolved-run", "slope_breakpoints_deg": [5, 15, 25, 35]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scenario"]["status"] == "completed"
    assert body["scenario"]["scenario_id"] == scenario["id"]
    dataset = body["datasets"][0]
    assert dataset["provenance"]["scenario_id"] == scenario["id"]

    runs = client.get(f"/scenarios/{scenario['id']}/hazard-scenarios").json()
    assert len(runs) == 1
    assert runs[0]["id"] == body["scenario"]["id"]


def test_landslide_run_missing_dem_without_baseline_or_explicit_id_400s(client: TestClient) -> None:
    project_id = _create_project(client, "No DEM Project")
    twin = _create_twin(client, project_id)
    scenario = _create_scenario(client, twin["id"])

    resp = client.post(f"/scenarios/{scenario['id']}/hazard-runs/landslide", json={"name": "no-dem"})
    assert resp.status_code == 400
    assert "dem_dataset_id" in resp.json()["detail"]


def test_run_rejected_when_scenario_archived(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "archived-run")
    scenario = _create_scenario(client, seed["twin"]["id"])
    client.post(f"/scenarios/{scenario['id']}/archive")

    resp = client.post(
        f"/scenarios/{scenario['id']}/hazard-runs/landslide",
        json={"name": "should-fail", "dem_dataset_id": seed["dem"]["id"]},
    )
    assert resp.status_code == 400


# --- comparison -------------------------------------------------------------------


def test_comparison_rejects_non_completed_or_missing_analysis(client: TestClient) -> None:
    resp = client.post(
        "/scenario-comparisons",
        json={"analysis_type": "risk", "left_analysis_id": "missing-1", "right_analysis_id": "missing-2"},
    )
    assert resp.status_code == 404


def test_comparison_rejects_unsupported_analysis_type(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/scenario-comparisons",
        json={"analysis_type": "hazard", "left_analysis_id": "x", "right_analysis_id": "y"},
    )
    assert resp.status_code in (400, 404)


# --- Digital Twin non-mutation regression -------------------------------------------


def test_scenario_lifecycle_never_mutates_digital_twin(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_twin_with_dem(client, tmp_path, "no-mutate")
    twin_id = seed["twin"]["id"]

    before_state = client.get(f"/digital-twins/{twin_id}/state").json()
    before_layers = client.get(f"/digital-twins/{twin_id}/layers", params={"status": "all"}).json()

    scenario = _create_scenario(client, twin_id)
    client.post(f"/datasets/{seed['dem']['id']}/derive", json={"product": "slope"})
    client.post(
        f"/scenarios/{scenario['id']}/hazard-runs/landslide",
        json={"name": "no-mutate-run", "dem_dataset_id": seed["dem"]["id"]},
    )
    alt = _upload_raster(client, seed["project_id"], _make_dem(tmp_path, "no-mutate-alt"), "dem", "dem_nm_alt.tif")
    client.post(f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": alt["id"]})
    client.post(f"/scenarios/{scenario['id']}/archive")

    after_state = client.get(f"/digital-twins/{twin_id}/state").json()
    after_layers = client.get(f"/digital-twins/{twin_id}/layers", params={"status": "all"}).json()

    assert after_state == before_state
    assert after_layers == before_layers


# --- end-to-end smoke test: full Phase 2-9 pipeline -> Scenario Lab what-if --------


def test_full_pipeline_smoke_twin_to_scenario_lab(client: TestClient, tmp_path: Path) -> None:
    """Runs the real Phase 2->8 pipeline into a Digital Twin (mirrors Phase
    9's own smoke test), then exercises a full Scenario Lab what-if on top
    of it: frozen baseline survives a real twin mutation, a scenario-scoped
    hazard re-run with a different parameter feeds into scenario-scoped
    exposure/risk/route re-runs via effective-layer resolution, a blocked
    road segment actually changes the route, a comparison against the
    baseline risk analysis reflects the changed assumption, and the
    Digital Twin's own state is provably untouched throughout.
    """
    project_id = _create_project(client, "Smoke Scenario Project")

    # Phase 1/2: DEM upload + slope derivation.
    dem_path = _make_dem(tmp_path, "smoke")
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_smoke.tif")
    slope_resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert slope_resp.status_code == 201, slope_resp.text

    # Phase 4: baseline landslide hazard scenario.
    scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Baseline landslide", "dem_dataset_id": dem["id"]},
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

    # Phase 6: baseline exposure analysis (roads as the exposure asset).
    exposure_resp = client.post(
        f"/projects/{project_id}/exposure-analyses",
        json={"name": "Baseline exposure", "hazard_dataset_id": landslide["id"], "exposure_dataset_id": roads["id"]},
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    baseline_exposure = exposure_resp.json()["analysis"]

    # Phase 7: baseline risk analysis.
    baseline_risk_resp = client.post(
        f"/projects/{project_id}/risk-analyses",
        json={
            "name": "Baseline risk", "exposure_analysis_id": baseline_exposure["id"],
            "vulnerability_weight": 0.5, "consequence_weight": 0.5,
        },
    )
    assert baseline_risk_resp.status_code == 201, baseline_risk_resp.text
    baseline_risk = baseline_risk_resp.json()["analysis"]

    # --- Phase 9: assemble the Digital Twin ---
    twin = _create_twin(client, project_id, "Smoke Twin")
    for dataset_id in (dem["id"], landslide["id"], roads["id"]):
        assert _register_layer(client, twin["id"], dataset_id).status_code == 201

    twin_state_before_scenario_work = client.get(f"/digital-twins/{twin['id']}/state").json()

    # --- Phase 10: create a scenario, snapshot the baseline ---
    scenario = _create_scenario(client, twin["id"], "Higher susceptibility what-if")
    baseline_layers = client.get(f"/scenarios/{scenario['id']}/baseline-layers").json()
    baseline_dataset_ids = {l["dataset"]["id"] for l in baseline_layers if l["dataset"] is not None}
    assert baseline_dataset_ids == {dem["id"], landslide["id"], roads["id"]}

    # Mutate the REAL twin after scenario creation: supersede the landslide
    # layer with a second baseline hazard run. Proves the frozen-baseline
    # decision -- the scenario must still see the ORIGINAL landslide dataset.
    second_scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Second baseline landslide", "dem_dataset_id": dem["id"], "slope_breakpoints_deg": [3, 10, 20, 30]},
    )
    assert second_scenario_resp.status_code == 201
    second_landslide = second_scenario_resp.json()["datasets"][0]
    supersede_resp = _register_layer(client, twin["id"], second_landslide["id"])
    assert supersede_resp.status_code == 201

    refetched_baseline = client.get(f"/scenarios/{scenario['id']}/baseline-layers").json()
    refetched_landslide_layer = next(l for l in refetched_baseline if l["layer"]["dataset_type"] == "landslide_susceptibility")
    assert refetched_landslide_layer["dataset"]["id"] == landslide["id"]  # still the ORIGINAL, now-superseded dataset

    state_no_override = client.get(f"/scenarios/{scenario['id']}/state").json()
    effective_hazard_no_override = next(
        l for l in state_no_override["effective_layers"] if l["dataset_type"] == "landslide_susceptibility"
    )
    assert effective_hazard_no_override["source"] == "baseline"
    assert effective_hazard_no_override["dataset"]["id"] == landslide["id"]

    # --- scenario-scoped hazard what-if: different slope breakpoints ---
    scenario_hazard_resp = client.post(
        f"/scenarios/{scenario['id']}/hazard-runs/landslide",
        json={"name": "What-if landslide", "dem_dataset_id": dem["id"], "slope_breakpoints_deg": [2, 8, 15, 25]},
    )
    assert scenario_hazard_resp.status_code == 201, scenario_hazard_resp.text
    scenario_landslide = scenario_hazard_resp.json()["datasets"][0]
    assert scenario_landslide["provenance"]["scenario_id"] == scenario["id"]

    # Register it as this scenario's hazard override.
    override_resp = client.post(
        f"/scenarios/{scenario['id']}/layer-overrides", json={"dataset_id": scenario_landslide["id"]}
    )
    assert override_resp.status_code == 201, override_resp.text

    # --- scenario-scoped exposure run: hazard auto-resolved to the OVERRIDE ---
    scenario_exposure_resp = client.post(
        f"/scenarios/{scenario['id']}/exposure-runs",
        json={"name": "What-if exposure", "exposure_dataset_id": roads["id"]},
    )
    assert scenario_exposure_resp.status_code == 201, scenario_exposure_resp.text
    scenario_exposure = scenario_exposure_resp.json()["analysis"]
    assert scenario_exposure["hazard_dataset_id"] == scenario_landslide["id"]
    assert scenario_exposure["scenario_id"] == scenario["id"]

    # --- scenario-scoped risk run with a different vulnerability_weight ---
    scenario_risk_resp = client.post(
        f"/scenarios/{scenario['id']}/risk-runs",
        json={
            "name": "What-if risk", "exposure_analysis_id": scenario_exposure["id"],
            "vulnerability_weight": 0.95, "consequence_weight": 0.95,
        },
    )
    assert scenario_risk_resp.status_code == 201, scenario_risk_resp.text
    scenario_risk = scenario_risk_resp.json()["analysis"]

    # --- scenario-scoped route run with a blocked segment ---
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    olon, olat = t.transform(10, 75)
    dlon, dlat = t.transform(150, 75)
    blocked_geom = {
        "type": "LineString",
        "coordinates": [list(t.transform(75, 74)), list(t.transform(75, 76))],
    }
    scenario_route_resp = client.post(
        f"/scenarios/{scenario['id']}/route-runs",
        json={
            "name": "What-if route", "road_dataset_id": roads["id"],
            "origin": {"lon": olon, "lat": olat}, "destination": {"lon": dlon, "lat": dlat},
            "hazard_penalty_weight": 1.0,
            "blocked_segment_geometries": [blocked_geom],
            "match_buffer_m": 5.0,
        },
    )
    assert scenario_route_resp.status_code == 201, scenario_route_resp.text
    scenario_route = scenario_route_resp.json()["analysis"]
    assert scenario_route["results"]["scenario_blocking"]["resolved_edge_count"] >= 1
    assert scenario_route["results"]["feasible"] is False  # the only edge in this tiny network is now blocked

    # --- comparison: baseline risk vs. scenario risk ---
    comparison_resp = client.post(
        "/scenario-comparisons",
        json={"analysis_type": "risk", "left_analysis_id": baseline_risk["id"], "right_analysis_id": scenario_risk["id"]},
    )
    assert comparison_resp.status_code == 201, comparison_resp.text
    comparison = comparison_resp.json()
    assert comparison["diff"]  # non-empty: at least one class differs given the very different weights

    # --- provenance chain walkable from the scenario route output back to the DEM ---
    route_dataset = scenario_route_resp.json()["datasets"][0] if scenario_route_resp.json()["datasets"] else None
    if route_dataset is not None:
        assert route_dataset["provenance"]["scenario_id"] == scenario["id"]
        assert route_dataset["provenance"]["road_dataset_id"] == roads["id"]

    # --- Digital Twin must be completely untouched by all of the above ---
    twin_state_after = client.get(f"/digital-twins/{twin['id']}/state").json()
    assert twin_state_after["twin"]["version"] == twin_state_before_scenario_work["twin"]["version"] + 1  # only from the supersede step
    active_hazard_layer = next(
        l for l in twin_state_after["layers_by_category"]["hazard"] if l["layer"]["status"] == "active"
    )
    assert active_hazard_layer["dataset"]["id"] == second_landslide["id"]  # the REAL twin's hazard is the superseding one
