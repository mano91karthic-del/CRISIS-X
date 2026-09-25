"""API-level integration tests for the Phase 7 risk-engine endpoints:
seed a real Phase 4 hazard scenario -> Phase 6 exposure analysis -> Phase 7
risk analysis, and verify results shape, lineage, provenance, and
failure-path coverage.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon


# --- fixture helpers ---------------------------------------------------------


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Risk Test Project"}).json()["id"]


def _upload_raster(client: TestClient, project_id: str, path: Path, dataset_type: str, filename: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_vector(client: TestClient, project_id: str, gdf: "gpd.GeoDataFrame", dataset_type: str, tmp_path: Path, filename: str) -> dict:
    path = tmp_path / filename
    gdf.to_file(path, driver="GeoJSON")
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "application/geo+json")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_landslide_hazard(client: TestClient, project_id: str, tmp_path: Path, name: str = "seed") -> dict:
    # Same construction as test_exposure_api.py's _seed_landslide_hazard:
    # 18x6 DEM, 10m pixels, origin (0,60) -> after Horn's-method slope +
    # default breakpoints, a clean class boundary at x=80: cols 1-7
    # ("very_low"), cols 8-16 ("very_high"). Query geometries stay within
    # y:[10,50] and away from the x=0/x=180 edges.
    rows, cols = 6, 18
    elevation = np.zeros((rows, cols), dtype="float32")
    r_idx = np.arange(rows).reshape(-1, 1)
    ramp = (rows - r_idx) * 20.0
    elevation[:, 9:] = np.broadcast_to(ramp, (rows, cols - 9))
    transform = from_origin(0, 60, 10, 10)
    dem_path = tmp_path / f"dem_{name}.tif"
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", f"dem_{name}.tif")
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})

    scenario = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": f"Risk-seed landslide {name}", "dem_dataset_id": dem["id"]},
    ).json()
    return scenario["datasets"][0]  # the landslide_susceptibility dataset


def _seed_flood_hazard(client: TestClient, project_id: str, tmp_path: Path, name: str = "flood") -> dict:
    dem_path = tmp_path / f"dem_{name}.tif"
    transform = from_origin(0, 60, 10, 10)
    r, c = np.indices((8, 8))
    elevation = (8 - r).astype("float32") * 3.0 - np.exp(-((c - 4) ** 2) / 4.0) * 2.0
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation.astype("float32"), 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", f"dem_{name}.tif")
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "flow_direction"})
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "flow_accumulation"})
    scenario = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={
            "name": f"Risk-seed flood {name}", "dem_dataset_id": dem["id"],
            "depth_above_drainage_m": 5.0, "channel_threshold_cells": 4,
        },
    ).json()
    return scenario["datasets"][0]  # the flood_inundation dataset


def _seed_eo_change_mask(client: TestClient, project_id: str, tmp_path: Path, name: str = "eo") -> dict:
    img_transform = from_origin(0, 60, 10, 10)
    before_arr = np.full((6, 6), 50.0, dtype="float32")
    after_arr = before_arr.copy()
    after_arr[:3, :3] += 40.0
    before_path = tmp_path / f"before_{name}.tif"
    after_path = tmp_path / f"after_{name}.tif"
    for path, arr in ((before_path, before_arr), (after_path, after_arr)):
        with rasterio.open(
            path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
            crs="EPSG:32643", transform=img_transform, nodata=-9999,
        ) as dst:
            dst.write(arr, 1)
    before = _upload_raster(client, project_id, before_path, "imagery", f"before_{name}.tif")
    after = _upload_raster(client, project_id, after_path, "imagery", f"after_{name}.tif")
    analysis = client.post(
        f"/projects/{project_id}/eo-change-analyses",
        json={
            "name": f"seed_{name}", "before_dataset_id": before["id"], "after_dataset_id": after["id"],
            "method": "change_vector_analysis", "threshold_method": "manual", "change_threshold": 10.0,
        },
    ).json()
    return next(d for d in analysis["datasets"] if d["dataset_type"] == "eo_change_mask")


def _run_exposure(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/exposure-analyses", json={"name": "Test Exposure", **kwargs})


def _run_risk(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/risk-analyses", json={"name": "Test Risk", **kwargs})


# --- landslide risk end to end ------------------------------------------------


def test_landslide_risk_end_to_end(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    points = gpd.GeoDataFrame(
        {"name": ["A", "B"], "geometry": [Point(45, 35), Point(125, 35)]}, crs="EPSG:32643"
    )
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals.geojson")

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    resp = _run_risk(
        client, project_id,
        exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=0.5, consequence_weight=1.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["hazard_dataset_type"] == "landslide_susceptibility"

    by_class = body["analysis"]["results"]["by_class"]
    assert by_class["very_low"]["count"] == 1  # exposure quantity preserved
    assert by_class["very_high"]["count"] == 1
    assert by_class["very_low"]["risk_score"] == pytest.approx(0.2 * 0.5 * 1.0)
    assert by_class["very_high"]["risk_score"] == pytest.approx(1.0 * 0.5 * 1.0)
    # risk_score = 1.0 * 0.5 * 1.0 = 0.5 -> falls in [0.4, 0.6) -> "moderate".
    assert by_class["very_high"]["risk_class"] == "moderate"

    assert len(body["datasets"]) == 1
    risk_ds = body["datasets"][0]
    assert risk_ds["dataset_type"] == "risk_classification"
    assert risk_ds["origin"] == "risk_analysis"
    assert risk_ds["risk_analysis_id"] == body["analysis"]["id"]


# --- flood: single-class hazard always gets full hazard weight --------------


def test_flood_risk_uses_full_hazard_intensity_weight(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    flood = _seed_flood_hazard(client, project_id, tmp_path)
    # Verified (via a standalone dry run of run_flood_scenario against this
    # exact DEM/params) that rows 2-7 (y <= 40) are inundated with depth
    # 5.0m; row 0-1 (y > 40) are nodata (HAND undefined near the ridge).
    points = gpd.GeoDataFrame({"geometry": [Point(15, 15)]}, crs="EPSG:32643")
    assets = _upload_vector(client, project_id, points, "critical_infrastructure", tmp_path, "infra.geojson")

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=flood["id"], exposure_dataset_id=assets["id"]
    ).json()
    resp = _run_risk(
        client, project_id,
        exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=0.7, consequence_weight=0.6,
    )
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class["inundated"]["hazard_intensity_weight"] == pytest.approx(1.0)
    assert by_class["inundated"]["risk_score"] == pytest.approx(0.7 * 0.6)


# --- eo_change_mask: no_change gets zero risk weight -------------------------


def test_eo_change_no_change_class_gets_zero_hazard_weight(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    mask = _seed_eo_change_mask(client, project_id, tmp_path)

    # One point inside the changed region, one clearly outside it but still
    # within the raster extent so it lands in the "no_change" polygon.
    points = gpd.GeoDataFrame(
        {"name": ["changed_pt", "unchanged_pt"], "geometry": [Point(5, 55), Point(55, 5)]},
        crs="EPSG:32643",
    )
    assets = _upload_vector(client, project_id, points, "buildings", tmp_path, "buildings_eo.geojson")

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=mask["id"], exposure_dataset_id=assets["id"]
    ).json()
    by_class_exposure = exposure["analysis"]["results"]["by_class"]
    assert "changed" in by_class_exposure

    resp = _run_risk(
        client, project_id,
        exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class["changed"]["risk_score"] == pytest.approx(1.0)
    if "no_change" in by_class:
        assert by_class["no_change"]["risk_score"] == pytest.approx(0.0)
        assert by_class["no_change"]["risk_class"] == "very_low"


# --- exposure quantity is never folded into risk_score -----------------------


def test_risk_score_unaffected_by_exposure_quantity(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    # Many points in the very_high region vs. a single point elsewhere --
    # both should get the identical very_high risk_score, since risk_score
    # is not scaled by count.
    many_points = gpd.GeoDataFrame(
        {"geometry": [Point(125 + i, 35) for i in range(5)]}, crs="EPSG:32643"
    )
    hospitals = _upload_vector(client, project_id, many_points, "hospitals", tmp_path, "many_hospitals.geojson")

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()
    assert exposure["analysis"]["results"]["by_class"]["very_high"]["count"] == 5

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class["very_high"]["count"] == 5  # exposure quantity untouched
    assert by_class["very_high"]["risk_score"] == pytest.approx(1.0)  # not scaled by count=5


# --- validation / failure paths ------------------------------------------------


def test_risk_rejects_failed_exposure_analysis(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    mixed = gpd.GeoDataFrame(
        {"geometry": [Point(5, 5)] + [Polygon([(0, 0), (1, 0), (1, 1)])]}, crs="EPSG:32643"
    )
    mixed_ds = _upload_vector(client, project_id, mixed, "other", tmp_path, "mixed.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=mixed_ds["id"]
    ).json()
    assert exposure["analysis"]["status"] == "failed"

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    assert resp.status_code == 400
    assert "not 'completed'" in resp.json()["detail"]


def test_risk_rejects_out_of_range_weights(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(45, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_oob.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.5, consequence_weight=1.0,
    )
    assert resp.status_code == 422

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=-0.1,
    )
    assert resp.status_code == 422


def test_risk_rejects_malformed_breakpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(45, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_bp.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
        risk_breakpoints=[0.6, 0.4, 0.8, 0.2],
    )
    assert resp.status_code == 422

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
        risk_breakpoints=[0.0, 0.4, 0.6, 0.8],
    )
    assert resp.status_code == 422


def test_risk_not_found_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/risk-analyses",
        json={"name": "x", "exposure_analysis_id": "a", "vulnerability_weight": 1.0, "consequence_weight": 1.0},
    )
    assert resp.status_code == 404

    project_id = _create_project(client)
    resp = _run_risk(
        client, project_id, exposure_analysis_id="does-not-exist",
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    assert resp.status_code == 404


def test_risk_rejects_cross_project_exposure_analysis(client: TestClient, tmp_path: Path) -> None:
    project_a = _create_project(client)
    project_b = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_a, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(45, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_a, points, "hospitals", tmp_path, "hospitals_cross.geojson")
    exposure = _run_exposure(
        client, project_a, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    resp = _run_risk(
        client, project_b, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    assert resp.status_code == 404


def test_risk_empty_overlap_completes_with_empty_by_class(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    # Point far outside the seeded DEM's extent -> no overlap at all.
    points = gpd.GeoDataFrame({"geometry": [Point(-9000, -9000)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_far.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()
    assert exposure["analysis"]["results"]["by_class"] == {}

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["results"]["by_class"] == {}
    assert body["datasets"] == []


# --- provenance / lineage / listing ------------------------------------------


def test_provenance_and_lineage_completeness(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(45, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_prov.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=0.5, consequence_weight=0.5,
    )
    body = resp.json()
    results = body["analysis"]["results"]

    assert results["hazard_dataset_type"] == "landslide_susceptibility"
    assert results["method"] == "hazard_vulnerability_consequence_product"
    assert results["vulnerability_weight"] == pytest.approx(0.5)
    assert results["consequence_weight"] == pytest.approx(0.5)
    assert results["risk_breakpoints"] == [0.2, 0.4, 0.6, 0.8]
    assert "limitations" in results
    assert any("NOT weighted, scaled, or normalized by exposure quantity" in lim for lim in results["limitations"])
    assert any("not a deterministic prediction" in lim for lim in results["limitations"])

    risk_datasets = client.get(f"/risk-analyses/{body['analysis']['id']}/datasets").json()
    assert len(risk_datasets) == 1
    ds = risk_datasets[0]
    assert ds["risk_analysis_id"] == body["analysis"]["id"]
    assert ds["origin"] == "risk_analysis"
    assert ds["provenance"]["risk_analysis_id"] == body["analysis"]["id"]
    assert ds["provenance"]["exposure_analysis_id"] == exposure["analysis"]["id"]
    assert ds["provenance"]["hazard_dataset_id"] == landslide["id"]
    assert ds["provenance"]["exposure_dataset_id"] == hospitals["id"]
    assert ds["provenance"]["hazard_dataset_name"] == landslide["name"]
    assert ds["provenance"]["exposure_dataset_name"] == hospitals["name"]


def test_listing_and_detail_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(45, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_list.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()

    created = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=1.0, consequence_weight=1.0,
    ).json()
    analysis_id = created["analysis"]["id"]

    resp = client.get(f"/projects/{project_id}/risk-analyses")
    assert [a["id"] for a in resp.json()] == [analysis_id]

    resp = client.get(f"/projects/{project_id}/risk-analyses", params={"hazard_dataset_type": "flood_inundation"})
    assert resp.json() == []

    resp = client.get(f"/risk-analyses/{analysis_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == analysis_id


def test_risk_analysis_not_found_404s(client: TestClient) -> None:
    assert client.get("/risk-analyses/does-not-exist").status_code == 404
    assert client.get("/risk-analyses/does-not-exist/datasets").status_code == 404


# --- end-to-end smoke test: full DEM -> hazard -> exposure -> risk pipeline --


def test_full_pipeline_smoke_dem_to_risk(client: TestClient, tmp_path: Path) -> None:
    """Exercises the entire Phase 2 -> Phase 4 -> Phase 6 -> Phase 7 chain
    in one function and walks the resulting lineage purely from response
    ids, to catch any cross-phase wiring break that per-scenario tests
    might miss.
    """
    project_id = _create_project(client)

    # Phase 1/2: upload DEM, derive slope.
    rows, cols = 6, 18
    elevation = np.zeros((rows, cols), dtype="float32")
    r_idx = np.arange(rows).reshape(-1, 1)
    ramp = (rows - r_idx) * 20.0
    elevation[:, 9:] = np.broadcast_to(ramp, (rows, cols - 9))
    transform = from_origin(0, 60, 10, 10)
    dem_path = tmp_path / "smoke_dem.tif"
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", "smoke_dem.tif")
    derive_resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert derive_resp.status_code == 201, derive_resp.text

    # Phase 4: landslide hazard scenario.
    scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Smoke landslide", "dem_dataset_id": dem["id"]},
    )
    assert scenario_resp.status_code == 201, scenario_resp.text
    scenario_body = scenario_resp.json()
    landslide_dataset = scenario_body["datasets"][0]
    assert landslide_dataset["hazard_scenario_id"] == scenario_body["scenario"]["id"]
    assert scenario_body["scenario"]["input_datasets"]["dem"] == dem["id"]

    # Phase 1: upload buildings exposure asset.
    buildings_gdf = gpd.GeoDataFrame(
        {"geometry": [Polygon([(60, 20), (100, 20), (100, 40), (60, 40)])]}, crs="EPSG:32643"
    )
    buildings = _upload_vector(client, project_id, buildings_gdf, "buildings", tmp_path, "smoke_buildings.geojson")

    # Phase 6: exposure analysis.
    exposure_resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide_dataset["id"], exposure_dataset_id=buildings["id"]
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    exposure_body = exposure_resp.json()
    assert exposure_body["analysis"]["status"] == "completed"
    assert exposure_body["analysis"]["hazard_dataset_id"] == landslide_dataset["id"]

    # Phase 7: risk analysis.
    risk_resp = _run_risk(
        client, project_id,
        exposure_analysis_id=exposure_body["analysis"]["id"],
        vulnerability_weight=0.6, consequence_weight=0.9,
    )
    assert risk_resp.status_code == 201, risk_resp.text
    risk_body = risk_resp.json()
    assert risk_body["analysis"]["status"] == "completed"
    assert len(risk_body["datasets"]) == 1
    risk_dataset = risk_body["datasets"][0]

    # Walk the full lineage chain purely from ids returned by the API.
    assert risk_dataset["risk_analysis_id"] == risk_body["analysis"]["id"]
    risk_analysis = client.get(f"/risk-analyses/{risk_body['analysis']['id']}").json()
    assert risk_analysis["exposure_analysis_id"] == exposure_body["analysis"]["id"]

    exposure_analysis = client.get(f"/exposure-analyses/{risk_analysis['exposure_analysis_id']}").json()
    assert exposure_analysis["hazard_dataset_id"] == landslide_dataset["id"]

    hazard_json = client.get(f"/datasets/{landslide_dataset['id']}").json()
    assert hazard_json["hazard_scenario_id"] == scenario_body["scenario"]["id"]

    hazard_scenario = client.get(f"/hazard-scenarios/{scenario_body['scenario']['id']}").json()
    assert hazard_scenario["input_datasets"]["dem"] == dem["id"]

    # Risk score is present and bounded, and exposure quantity survives
    # untouched alongside it (the clarification this plan was approved on).
    by_class = risk_analysis["results"]["by_class"]
    assert by_class  # non-empty: the building overlaps a hazard class
    for entry in by_class.values():
        assert 0.0 <= entry["risk_score"] <= 1.0
        assert "area_m2" in entry  # Phase 6's polygon-overlay exposure quantity, untouched
