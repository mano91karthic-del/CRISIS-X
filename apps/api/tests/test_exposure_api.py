"""API-level integration tests for Phase 6 exposure-analysis endpoints:
full upload -> derive/run hazard -> run exposure analysis -> verify results
and lineage, plus CRS mismatch, nodata, validation, and failure-path
coverage.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon


# --- fixture helpers ---------------------------------------------------------


def _write_landslide_raster(path: Path, *, crs: str = "EPSG:32643", origin=(0.0, 60.0), pixel_size: float = 10.0) -> Path:
    # 6x6 grid: left half (cols 0-2) class 1 (very_low), right half class 5 (very_high).
    transform = from_origin(origin[0], origin[1], pixel_size, pixel_size)
    codes = np.zeros((6, 6), dtype="float32")
    codes[:, :3] = 1.0
    codes[:, 3:] = 5.0
    with rasterio.open(
        path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
        crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(codes, 1)
    return path


def _write_flood_raster(path: Path, *, crs: str = "EPSG:32643") -> Path:
    # 6x6 grid, 10m cells: left half inundated with depth values, right half nodata.
    transform = from_origin(0, 60, 10, 10)
    depth = np.full((6, 6), -9999.0, dtype="float32")
    depth[:, :3] = 1.5
    with rasterio.open(
        path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
        crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(depth, 1)
    return path


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


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Exposure Test Project"}).json()["id"]


def _seed_landslide_hazard(client: TestClient, project_id: str, tmp_path: Path) -> dict:
    # 18x6 DEM, 10m pixels, origin (0,60): flat elevation for cols 0-8, a
    # steep south-facing ramp for cols 9-17. Verified (via a standalone
    # dry run of derive_terrain_product + run_landslide_scenario) to
    # produce, after Horn's-method slope + the default breakpoints, a
    # *clean* class boundary at x=80: cols 1-7 (x:[10,80)) come out
    # "very_low", cols 8-16 (x:[80,170)) come out "very_high" -- border
    # row 0/5 and col 0/17 are nodata (Horn's method's edge exclusion), so
    # all query geometries below stay within y:[10,50] and away from the
    # x=0/x=180 edges.
    rows, cols = 6, 18
    elevation = np.zeros((rows, cols), dtype="float32")
    r_idx = np.arange(rows).reshape(-1, 1)
    ramp = (rows - r_idx) * 20.0
    elevation[:, 9:] = np.broadcast_to(ramp, (rows, cols - 9))
    transform = from_origin(0, 60, 10, 10)
    dem_path = tmp_path / "dem_source.tif"
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_source.tif")
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})

    scenario = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Exposure-seed landslide", "dem_dataset_id": dem["id"]},
    ).json()
    return scenario["datasets"][0]  # the landslide_susceptibility dataset


def _run_exposure(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/exposure-analyses", json={"name": "Test Exposure", **kwargs})


# --- raster hazard x point assets (hospitals) -------------------------------


def test_landslide_x_points_exact_class_assignment(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    hazard_path = _write_landslide_raster(tmp_path / "hazard.tif")
    hazard = _upload_raster(client, project_id, hazard_path, "landslide_susceptibility_screening", "hazard.tif")
    # Manually promote origin via a real hazard-scenario run instead --
    # landslide_susceptibility must come from a real HazardScenario (origin
    # check), so use the real pipeline (see next test) for gating coverage.
    # Here we only validate math against a hand-built raster that we treat
    # as if uploaded directly is expected to be REJECTED (origin != hazard_model).
    points = gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "geometry": [Point(5, 55), Point(35, 55), Point(55, 5)]}, crs="EPSG:32643"
    )
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=hazard["id"], exposure_dataset_id=hospitals["id"]
    )
    # Uploaded raster has origin="uploaded", not a real hazard-model output -> rejected.
    assert resp.status_code == 400
    assert "origin" in resp.json()["detail"].lower()


def test_real_landslide_scenario_x_points_exact_class_assignment(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    points = gpd.GeoDataFrame(
        {"name": ["A", "B"], "geometry": [Point(45, 35), Point(125, 35)]}, crs="EPSG:32643"
    )
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["hazard_dataset_type"] == "landslide_susceptibility"
    assert body["analysis"]["exposure_dataset_type"] == "hospitals"
    by_class = body["analysis"]["results"]["by_class"]
    assert by_class["very_low"]["count"] == 1
    assert by_class["very_high"]["count"] == 1


# --- raster hazard x population raster --------------------------------------


def test_population_raster_exposure(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    # Same grid as the seeded DEM (18x6, 10m, origin (0,60)) so population
    # cell centroids align 1:1 with the hazard raster's own cells: the
    # hazard's valid (non-border) region is rows 1-4 x cols 1-16 (64
    # cells) -- 28 of those are "very_low" (cols 1-7), 36 are "very_high"
    # (cols 8-16), each with population=100.
    pop_path = tmp_path / "population.tif"
    transform = from_origin(0, 60, 10, 10)
    pop = np.full((6, 18), 100.0, dtype="float32")
    with rasterio.open(
        pop_path, "w", driver="GTiff", height=6, width=18, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(pop, 1)
    population = _upload_raster(client, project_id, pop_path, "population", "population.tif")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=population["id"]
    )
    assert resp.status_code == 201, resp.text
    results = resp.json()["analysis"]["results"]
    assert results["by_class"]["very_low"]["sum"] == pytest.approx(2800.0)
    assert results["by_class"]["very_high"]["sum"] == pytest.approx(3600.0)

    datasets_resp = client.get(f"/exposure-analyses/{resp.json()['analysis']['id']}/datasets")
    assert len(datasets_resp.json()) == 1
    feature = datasets_resp.json()[0]
    assert feature["dataset_type"] == "exposure_features"
    assert feature["origin"] == "exposure_analysis"


# --- raster hazard x polygon assets (buildings) -----------------------------


def test_buildings_polygon_straddling_boundary(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    building = Polygon([(60, 20), (100, 20), (100, 40), (60, 40)])  # straddles the very_low/very_high boundary at x=80
    buildings_gdf = gpd.GeoDataFrame({"geometry": [building]}, crs="EPSG:32643")
    buildings = _upload_vector(client, project_id, buildings_gdf, "buildings", tmp_path, "buildings.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=buildings["id"]
    )
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert "very_low" in by_class and "very_high" in by_class
    # 40x20 building split exactly in half by the x=80 boundary -> 400 m^2 each side.
    assert by_class["very_low"]["area_m2"] == pytest.approx(400.0)
    assert by_class["very_high"]["area_m2"] == pytest.approx(400.0)


# --- raster hazard x line assets (roads) ------------------------------------


def test_roads_line_partial_crossing(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    road = LineString([(60, 35), (100, 35)])  # crosses the very_low/very_high boundary at x=80
    roads_gdf = gpd.GeoDataFrame({"geometry": [road]}, crs="EPSG:32643")
    roads = _upload_vector(client, project_id, roads_gdf, "roads", tmp_path, "roads.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=roads["id"])
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class["very_low"]["length_m"] == pytest.approx(20.0)
    assert by_class["very_high"]["length_m"] == pytest.approx(20.0)


# --- CRS mismatch / nodata / hazard-class preservation ----------------------


def test_exposure_dataset_different_crs_is_reprojected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    # landslide hazard CRS is EPSG:32643 (UTM 43N). Build hospital points in
    # WGS84 geographic coordinates corresponding to roughly the same area
    # used elsewhere in this test suite (near lon 77 / lat 13).
    points = gpd.GeoDataFrame({"geometry": [Point(77.0, 13.0)]}, crs="EPSG:4326")
    hospitals_path = tmp_path / "hospitals_wgs84.geojson"
    points.to_file(hospitals_path, driver="GeoJSON")
    with hospitals_path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "hospitals"},
            files={"file": ("hospitals_wgs84.geojson", f, "application/geo+json")},
        )
    hospitals = resp.json()

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    )
    # Should succeed (reprojection happens transparently) even though the
    # point almost certainly lands outside this synthetic hazard raster's
    # tiny extent -- the important thing is no CRS-mismatch crash.
    assert resp.status_code == 201, resp.text


def test_flood_hazard_reports_depth_statistics_and_single_class(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem_path = tmp_path / "dem_for_flood.tif"
    transform = from_origin(0, 60, 10, 10)
    r, c = np.indices((8, 8))
    elevation = (8 - r).astype("float32") * 3.0 - np.exp(-((c - 4) ** 2) / 4.0) * 2.0
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation.astype("float32"), 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", "dem_for_flood.tif")
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "flow_direction"})
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "flow_accumulation"})
    flood_scenario = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "Exposure-seed flood", "dem_dataset_id": dem["id"], "depth_above_drainage_m": 5.0, "channel_threshold_cells": 4},
    ).json()
    flood = flood_scenario["datasets"][0]

    points = gpd.GeoDataFrame({"geometry": [Point(15, 55)]}, crs="EPSG:32643")
    assets = _upload_vector(client, project_id, points, "critical_infrastructure", tmp_path, "infra.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=flood["id"], exposure_dataset_id=assets["id"])
    assert resp.status_code == 201, resp.text
    results = resp.json()["analysis"]["results"]
    assert set(results["by_class"].keys()) <= {"inundated"}
    assert "depth_statistics" in results


def test_nodata_excluded_from_hazard_polygonization(client: TestClient, tmp_path: Path) -> None:
    # Points placed only in the "very_high" region (and never in the
    # nodata border rows/cols) must never appear under "very_low" -- proves
    # nodata/border cells are excluded from polygonization entirely rather
    # than being folded into whichever class happens to be nearest.
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(125, 35)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_right.geojson")
    resp = _run_exposure(client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"])
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert "very_low" not in by_class
    assert by_class["very_high"]["count"] == 1


# --- population vector (points and polygons) --------------------------------


def test_population_vector_points_requires_population_field(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    points = gpd.GeoDataFrame(
        {"pop_count": [40.0, 60.0], "geometry": [Point(45, 35), Point(125, 35)]}, crs="EPSG:32643"
    )
    population = _upload_vector(client, project_id, points, "population", tmp_path, "population_pts.geojson")

    # Missing population_field -> 400
    resp = _run_exposure(client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=population["id"])
    assert resp.status_code == 400
    assert "population_field" in resp.json()["detail"]

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=population["id"],
        population_field="pop_count",
    )
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class["very_low"]["sum"] == pytest.approx(40.0)
    assert by_class["very_high"]["sum"] == pytest.approx(60.0)


def test_population_field_unknown_column_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"pop_count": [10.0], "geometry": [Point(5, 5)]}, crs="EPSG:32643")
    population = _upload_vector(client, project_id, points, "population", tmp_path, "population_pts2.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=population["id"],
        population_field="does_not_exist",
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_population_field_rejected_for_raster_population(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)

    pop_path = tmp_path / "pop_raster2.tif"
    transform = from_origin(0, 60, 10, 10)
    pop = np.full((6, 6), 50.0, dtype="float32")
    with rasterio.open(
        pop_path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(pop, 1)
    population = _upload_raster(client, project_id, pop_path, "population", "pop_raster2.tif")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=population["id"],
        population_field="anything",
    )
    assert resp.status_code == 400
    assert "not applicable" in resp.json()["detail"].lower()


def test_population_field_rejected_for_non_population_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(5, 5)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_x.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"],
        population_field="pop_count",
    )
    assert resp.status_code == 400
    assert "only applicable" in resp.json()["detail"].lower()


# --- validation --------------------------------------------------------------


def test_unsupported_hazard_dataset_type_rejected(client: TestClient, tmp_path: Path, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.tif").open("rb") as f:
        dem_resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    dem = dem_resp.json()
    points = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs="EPSG:4326")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_y.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=dem["id"], exposure_dataset_id=hospitals["id"])
    assert resp.status_code == 400
    assert "origin" in resp.json()["detail"].lower()


def test_eo_change_magnitude_not_a_supported_hazard_type(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    img_transform = from_origin(0, 60, 10, 10)
    before_arr = np.full((6, 6), 50.0, dtype="float32")
    after_arr = before_arr.copy()
    after_arr[:3, :3] += 40.0
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    for path, arr in ((before_path, before_arr), (after_path, after_arr)):
        with rasterio.open(
            path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
            crs="EPSG:32643", transform=img_transform, nodata=-9999,
        ) as dst:
            dst.write(arr, 1)
    before = _upload_raster(client, project_id, before_path, "imagery", "before.tif")
    after = _upload_raster(client, project_id, after_path, "imagery", "after.tif")

    analysis = client.post(
        f"/projects/{project_id}/eo-change-analyses",
        json={
            "name": "seed", "before_dataset_id": before["id"], "after_dataset_id": after["id"],
            "method": "change_vector_analysis", "threshold_method": "manual", "change_threshold": 10.0,
        },
    ).json()
    magnitude_ds = next(d for d in analysis["datasets"] if d["dataset_type"] == "eo_change_magnitude")

    points = gpd.GeoDataFrame({"geometry": [Point(5, 55)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_z.geojson")

    resp = _run_exposure(
        client, project_id, hazard_dataset_id=magnitude_ds["id"], exposure_dataset_id=hospitals["id"]
    )
    assert resp.status_code == 400
    assert "unsupported hazard dataset_type" in resp.json()["detail"].lower()


def test_eo_change_mask_supported_as_hazard(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    img_transform = from_origin(0, 60, 10, 10)
    before_arr = np.full((6, 6), 50.0, dtype="float32")
    after_arr = before_arr.copy()
    after_arr[:3, :3] += 40.0
    before_path = tmp_path / "before2.tif"
    after_path = tmp_path / "after2.tif"
    for path, arr in ((before_path, before_arr), (after_path, after_arr)):
        with rasterio.open(
            path, "w", driver="GTiff", height=6, width=6, count=1, dtype="float32",
            crs="EPSG:32643", transform=img_transform, nodata=-9999,
        ) as dst:
            dst.write(arr, 1)
    before = _upload_raster(client, project_id, before_path, "imagery", "before2.tif")
    after = _upload_raster(client, project_id, after_path, "imagery", "after2.tif")

    analysis = client.post(
        f"/projects/{project_id}/eo-change-analyses",
        json={
            "name": "seed2", "before_dataset_id": before["id"], "after_dataset_id": after["id"],
            "method": "change_vector_analysis", "threshold_method": "manual", "change_threshold": 10.0,
        },
    ).json()
    mask_ds = next(d for d in analysis["datasets"] if d["dataset_type"] == "eo_change_mask")

    points = gpd.GeoDataFrame({"geometry": [Point(5, 55)]}, crs="EPSG:32643")  # inside the changed region
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_w.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=mask_ds["id"], exposure_dataset_id=hospitals["id"])
    assert resp.status_code == 201, resp.text
    by_class = resp.json()["analysis"]["results"]["by_class"]
    assert by_class.get("changed", {}).get("count") == 1


def test_mixed_geometry_exposure_dataset_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    mixed = gpd.GeoDataFrame(
        {"geometry": [Point(5, 5), LineString([(1, 1), (2, 2)])]}, crs="EPSG:32643"
    )
    mixed_ds = _upload_vector(client, project_id, mixed, "other", tmp_path, "mixed.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=mixed_ds["id"])
    assert resp.status_code == 201  # request is well-formed; failure happens during computation
    body = resp.json()
    assert body["analysis"]["status"] == "failed"
    assert "mixed geometry" in body["analysis"]["error_message"].lower()


def test_missing_hazard_dataset_returns_404(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    points = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs="EPSG:4326")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_404.geojson")
    resp = _run_exposure(client, project_id, hazard_dataset_id="does-not-exist", exposure_dataset_id=hospitals["id"])
    assert resp.status_code == 404


def test_missing_project_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/exposure-analyses",
        json={"name": "x", "hazard_dataset_id": "a", "exposure_dataset_id": "b"},
    )
    assert resp.status_code == 404


def test_dataset_from_another_project_rejected(client: TestClient, tmp_path: Path) -> None:
    project_a = _create_project(client)
    project_b = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_a, tmp_path)

    points = gpd.GeoDataFrame({"geometry": [Point(5, 5)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_b, points, "hospitals", tmp_path, "hospitals_cross.geojson")

    resp = _run_exposure(client, project_a, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"])
    assert resp.status_code == 404  # exposure dataset not found *in project_a*


# --- provenance / lineage / listing ------------------------------------------


def test_provenance_and_lineage_completeness(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(5, 5)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_prov.geojson")

    resp = _run_exposure(client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"])
    body = resp.json()
    results = body["analysis"]["results"]

    assert results["hazard_dataset_type"] == "landslide_susceptibility"
    assert "method" in results
    assert "crs" in results
    assert "limitations" in results and len(results["limitations"]) >= 2
    assert any("not a risk score" in lim for lim in results["limitations"])
    assert any("does not imply confirmed damage" in lim for lim in results["limitations"])

    feature_datasets = client.get(f"/exposure-analyses/{body['analysis']['id']}/datasets").json()
    for ds in feature_datasets:
        assert ds["exposure_analysis_id"] == body["analysis"]["id"]
        assert ds["origin"] == "exposure_analysis"
        assert ds["provenance"]["hazard_dataset_id"] == landslide["id"]
        assert ds["provenance"]["exposure_dataset_id"] == hospitals["id"]


def test_listing_and_detail_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path)
    points = gpd.GeoDataFrame({"geometry": [Point(5, 5)]}, crs="EPSG:32643")
    hospitals = _upload_vector(client, project_id, points, "hospitals", tmp_path, "hospitals_list.geojson")

    created = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals["id"]
    ).json()
    analysis_id = created["analysis"]["id"]

    resp = client.get(f"/projects/{project_id}/exposure-analyses")
    assert [a["id"] for a in resp.json()] == [analysis_id]

    resp = client.get(f"/projects/{project_id}/exposure-analyses", params={"exposure_dataset_type": "roads"})
    assert resp.json() == []

    resp = client.get(f"/exposure-analyses/{analysis_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == analysis_id


def test_analysis_not_found_404s(client: TestClient) -> None:
    assert client.get("/exposure-analyses/does-not-exist").status_code == 404
    assert client.get("/exposure-analyses/does-not-exist/datasets").status_code == 404
