"""Regression test reproducing a reported Phase 1/6 inconsistency: a
newly uploaded dataset appeared correctly in `GET /projects/{id}/datasets`
(with and without a `dataset_type` filter) but `GET /datasets/{id}` and
`POST /projects/{id}/exposure-analyses` (which looks the dataset up the
same way) both returned 404 "not found" for the exact same dataset id.

Reproduces the exact reported sequence: upload -> list (unfiltered) ->
list (dataset_type filtered) -> get-by-id -> use as an exposure dataset.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import Polygon


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Dataset Lookup Regression Project"}).json()["id"]


def _make_large_buildings_gdf(feature_count: int) -> "gpd.GeoDataFrame":
    """A large, realistic-shape buildings polygon layer -- small square
    footprints on a grid, matching the scale class (tens of thousands of
    features) of the real-world dataset that triggered this bug report
    (a Chennai buildings footprint export).
    """
    polygons = []
    side = 8.0
    per_row = 200
    for i in range(feature_count):
        col = i % per_row
        row = i // per_row
        x0 = col * 20.0
        y0 = row * 20.0
        polygons.append(Polygon([(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]))
    return gpd.GeoDataFrame({"building_id": range(feature_count)}, geometry=polygons, crs="EPSG:32643")


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


def _seed_landslide_hazard(client: TestClient, project_id: str, tmp_path: Path) -> dict:
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
    with dem_path.open("rb") as f:
        dem_resp = client.post(
            f"/projects/{project_id}/datasets", data={"dataset_type": "dem"}, files={"file": ("dem_source.tif", f, "image/tiff")}
        )
    assert dem_resp.status_code == 201, dem_resp.text
    dem = dem_resp.json()

    slope_resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert slope_resp.status_code == 201, slope_resp.text

    scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Regression landslide", "dem_dataset_id": dem["id"]},
    )
    assert scenario_resp.status_code == 201, scenario_resp.text
    return scenario_resp.json()["datasets"][0]


def test_large_buildings_dataset_lookup_and_exposure_analysis_sequence(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)

    # 1. upload a large real-scale buildings dataset
    gdf = _make_large_buildings_gdf(2500)
    uploaded = _upload_vector(client, project_id, gdf, "buildings", tmp_path, "TNGIS_GCC_Chennai_buildings.geojson")
    assert uploaded["status"] == "validated"
    dataset_id = uploaded["id"]

    # 2. list project datasets (unfiltered) -- must include it
    unfiltered = client.get(f"/projects/{project_id}/datasets")
    assert unfiltered.status_code == 200
    assert dataset_id in {d["id"] for d in unfiltered.json()}

    # 2b. list project datasets filtered by dataset_type=buildings -- must include it
    filtered = client.get(f"/projects/{project_id}/datasets", params={"dataset_type": "buildings"})
    assert filtered.status_code == 200
    assert dataset_id in {d["id"] for d in filtered.json()}

    # 3. get dataset by id -- must succeed, not 404
    by_id = client.get(f"/datasets/{dataset_id}")
    assert by_id.status_code == 200, by_id.text
    assert by_id.json()["id"] == dataset_id

    # 4. use it as an exposure-analysis exposure dataset -- must succeed, not 404
    hazard = _seed_landslide_hazard(client, project_id, tmp_path)
    exposure_resp = client.post(
        f"/projects/{project_id}/exposure-analyses",
        json={"name": "Regression exposure", "hazard_dataset_id": hazard["id"], "exposure_dataset_id": dataset_id},
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    assert exposure_resp.json()["analysis"]["status"] == "completed"
