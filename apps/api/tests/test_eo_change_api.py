"""API-level integration tests for Phase 5 EO change-detection endpoints:
full upload -> run analysis -> verify output dataset fields/provenance,
plus alignment, threshold, nodata, overlap, and failure-path coverage.
"""

from pathlib import Path

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _write_geotiff(
    path: Path,
    data: np.ndarray,
    *,
    origin_lon: float = 77.0,
    origin_lat: float = 13.0,
    pixel_size: float = 0.001,
    crs: str = "EPSG:4326",
    nodata: float = -9999.0,
) -> Path:
    bands, rows, cols = data.shape
    transform = from_origin(origin_lon, origin_lat, pixel_size, pixel_size)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=bands,
        dtype="float32", crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data.astype("float32"))
    return path


def _make_image(rows: int = 8, cols: int = 8, bands: int = 2, base: float = 100.0) -> np.ndarray:
    return np.stack([np.full((rows, cols), base + i * 10, dtype="float32") for i in range(bands)])


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "EO Change Test Project"}).json()["id"]


def _upload_imagery(client: TestClient, project_id: str, tmp_path: Path, data: np.ndarray, name: str, **kwargs) -> dict:
    path = _write_geotiff(tmp_path / name, data, **kwargs)
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "imagery"},
            files={"file": (name, f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_analysis(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/eo-change-analyses", json={"name": "Test Analysis", **kwargs})


# --- end-to-end happy paths ------------------------------------------------


def test_identical_images_produce_no_change(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id,
        before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=1.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["analysis"]["status"] == "completed"
    magnitude, mask = body["datasets"]
    assert magnitude["dataset_type"] == "eo_change_magnitude"
    assert mask["dataset_type"] == "eo_change_mask"
    assert magnitude["origin"] == "eo_analysis"
    assert magnitude["metadata_json"]["changed_cell_count"] == 0


def test_known_pixel_change_is_detected_exactly(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = _make_image(rows=6, cols=6, bands=1, base=50.0)
    after_arr = before_arr.copy()
    after_arr[0, 2:4, 2:4] += 40.0  # 4-cell localized change

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif")

    resp = _run_analysis(
        client, project_id,
        before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=20.0,
    )
    assert resp.status_code == 201
    magnitude = resp.json()["datasets"][0]
    assert magnitude["metadata_json"]["changed_cell_count"] == 4


def test_threshold_behavior_is_monotonic(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = _make_image(rows=6, cols=6, bands=1, base=50.0)
    after_arr = before_arr.copy()
    after_arr[0, 0, 0] += 5.0
    after_arr[0, 1, 1] += 15.0
    after_arr[0, 2, 2] += 25.0

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif")

    low = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=3.0,
    ).json()["datasets"][0]["metadata_json"]["changed_cell_count"]
    high = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=20.0,
    ).json()["datasets"][0]["metadata_json"]["changed_cell_count"]

    assert low == 3
    assert high == 1
    assert high <= low


def test_normalized_difference_method(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    # band 1 = "red"-like, band 2 = "nir"-like
    before_arr = np.stack(
        [np.full((5, 5), 0.2, dtype="float32"), np.full((5, 5), 0.2, dtype="float32")]
    )
    after_arr = before_arr.copy()
    after_arr[1, :, :] = 0.6  # NIR rises sharply everywhere -> NDVI-style index rises

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif")

    resp = _run_analysis(
        client, project_id,
        before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="normalized_difference", band_a_index=2, band_b_index=1, index_name="NDVI",
        threshold_method="manual", change_threshold=0.1,
    )
    assert resp.status_code == 201
    magnitude = resp.json()["datasets"][0]
    assert magnitude["metadata_json"]["index_name"] == "NDVI"
    assert magnitude["metadata_json"]["changed_cell_count"] == 25


def test_otsu_threshold_is_recorded_and_used(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = np.full((1, 10, 10), 50.0, dtype="float32")
    after_arr = before_arr.copy()
    after_arr[0, :5, :] += 40.0  # half the image clearly changes -> bimodal magnitude

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif")

    resp = _run_analysis(
        client, project_id,
        before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="otsu",
    )
    assert resp.status_code == 201, resp.text
    magnitude = resp.json()["datasets"][0]
    assert magnitude["metadata_json"]["threshold_method"] == "otsu"
    assert 0 < magnitude["metadata_json"]["threshold_value"] < 40
    assert magnitude["metadata_json"]["changed_cell_count"] == 50
    assert any("otsu" in lim.lower() for lim in magnitude["metadata_json"]["limitations"])
    assert any("not deep learning" in lim.lower() or "not a trained" in lim.lower()
               for lim in magnitude["metadata_json"]["limitations"])


# --- nodata / alignment -----------------------------------------------------


def test_nodata_region_excluded_from_change(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = np.full((1, 6, 6), 50.0, dtype="float32")
    after_arr = before_arr.copy()
    after_arr[0, :, :] += 30.0
    before_arr[0, 0, 0] = -9999.0  # nodata in before at (0,0)

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=10.0,
    )
    assert resp.status_code == 201
    magnitude = resp.json()["datasets"][0]
    # 36 cells total, 1 excluded as nodata -> 35 valid
    assert magnitude["metadata_json"]["valid_cell_count"] == 35


def test_mismatched_crs_triggers_controlled_reprojection(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = _make_image(rows=8, cols=8, bands=1, base=50.0)
    after_arr = before_arr.copy()
    after_arr[0, 3:5, 3:5] += 20.0

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif", crs="EPSG:4326")
    # UTM 43N coordinates for the same real-world area as lon=77.0/lat=13.0
    # (EPSG:4326) -- must genuinely overlap for this test to exercise
    # reprojection rather than the (correct) overlap rejection.
    after = _upload_imagery(
        client, project_id, tmp_path, after_arr, "after.tif",
        crs="EPSG:32643", origin_lon=717000, origin_lat=1437500, pixel_size=100,
    )

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=5.0,
    )
    assert resp.status_code == 201, resp.text
    magnitude = resp.json()["datasets"][0]
    assert magnitude["crs"] == before["crs"]  # before's CRS is the reference grid
    assert magnitude["metadata_json"]["alignment_method"] == "resampled_to_before_grid"
    assert magnitude["metadata_json"]["resampling"] == "bilinear"


def test_mismatched_resolution_triggers_resampling(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    before_arr = _make_image(rows=10, cols=10, bands=1, base=50.0)
    after_arr = _make_image(rows=5, cols=5, bands=1, base=50.0)  # coarser resolution, same AOI-ish

    before = _upload_imagery(client, project_id, tmp_path, before_arr, "before.tif", pixel_size=0.001)
    after = _upload_imagery(client, project_id, tmp_path, after_arr, "after.tif", pixel_size=0.002)

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=1.0,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["datasets"][0]["metadata_json"]["alignment_method"] == "resampled_to_before_grid"


def test_non_overlapping_images_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif", origin_lon=77.0, origin_lat=13.0)
    after = _upload_imagery(
        client, project_id, tmp_path, image.copy(), "after.tif", origin_lon=200.0, origin_lat=13.0
    )

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=1.0,
    )
    assert resp.status_code == 400
    assert "overlap" in resp.json()["detail"].lower()
    assert client.get(f"/projects/{project_id}/eo-change-analyses").json() == []


# --- validation --------------------------------------------------------------


def test_missing_band_a_index_for_normalized_difference_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="normalized_difference", threshold_method="manual", change_threshold=0.1,
    )
    assert resp.status_code == 422


def test_band_indices_supplied_with_cva_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", band_a_index=1, band_b_index=2,
        threshold_method="manual", change_threshold=0.1,
    )
    assert resp.status_code == 422


def test_threshold_missing_for_manual_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual",
    )
    assert resp.status_code == 422


def test_threshold_supplied_for_otsu_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="otsu", change_threshold=5.0,
    )
    assert resp.status_code == 422


def test_out_of_range_band_index_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image(bands=2)
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="normalized_difference", band_a_index=1, band_b_index=99,
        threshold_method="manual", change_threshold=0.1,
    )
    assert resp.status_code == 201  # request itself is well-formed
    body = resp.json()
    assert body["analysis"]["status"] == "failed"
    assert body["datasets"][0]["status"] == "invalid"


def test_non_imagery_dataset_type_rejected(client: TestClient, tmp_path: Path, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    after = _upload_imagery(client, project_id, tmp_path, image, "after.tif")

    with (fixtures_dir / "sample.tif").open("rb") as f:
        dem_resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    dem = dem_resp.json()

    resp = _run_analysis(
        client, project_id, before_dataset_id=dem["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=1.0,
    )
    assert resp.status_code == 400
    assert "imagery" in resp.json()["detail"].lower()


def test_missing_dataset_returns_404(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image()
    after = _upload_imagery(client, project_id, tmp_path, image, "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id="does-not-exist", after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=1.0,
    )
    assert resp.status_code == 404


def test_missing_project_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/eo-change-analyses",
        json={
            "name": "x", "before_dataset_id": "a", "after_dataset_id": "b",
            "method": "change_vector_analysis", "threshold_method": "manual", "change_threshold": 1.0,
        },
    )
    assert resp.status_code == 404


# --- provenance / lineage ----------------------------------------------------


def test_provenance_completeness_and_lineage(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image(rows=6, cols=6, bands=1)
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    resp = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=5.0,
        description="a test run",
    )
    body = resp.json()
    analysis_id = body["analysis"]["id"]
    magnitude, mask = body["datasets"]

    for dataset in (magnitude, mask):
        assert dataset["eo_change_analysis_id"] == analysis_id
        assert dataset["origin"] == "eo_analysis"
        prov = dataset["provenance"]
        assert prov["before_dataset_id"] == before["id"]
        assert prov["after_dataset_id"] == after["id"]
        assert prov["before_acquisition_date"] is None  # unknown -> explicitly null, never fabricated
        assert prov["after_acquisition_date"] is None
        assert "method" in prov and "threshold_value" in prov and "limitations" in prov

    resp = client.get(f"/eo-change-analyses/{analysis_id}/datasets")
    assert {d["id"] for d in resp.json()} == {magnitude["id"], mask["id"]}


def test_listing_and_detail_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    image = _make_image(rows=6, cols=6, bands=1)
    before = _upload_imagery(client, project_id, tmp_path, image, "before.tif")
    after = _upload_imagery(client, project_id, tmp_path, image.copy(), "after.tif")

    created = _run_analysis(
        client, project_id, before_dataset_id=before["id"], after_dataset_id=after["id"],
        method="change_vector_analysis", threshold_method="manual", change_threshold=5.0,
    ).json()
    analysis_id = created["analysis"]["id"]

    resp = client.get(f"/projects/{project_id}/eo-change-analyses")
    assert [a["id"] for a in resp.json()] == [analysis_id]

    resp = client.get(f"/projects/{project_id}/eo-change-analyses", params={"method": "normalized_difference"})
    assert resp.json() == []

    resp = client.get(f"/eo-change-analyses/{analysis_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == analysis_id


def test_analysis_not_found_404s(client: TestClient) -> None:
    assert client.get("/eo-change-analyses/does-not-exist").status_code == 404
    assert client.get("/eo-change-analyses/does-not-exist/datasets").status_code == 404


# --- acquisition date -------------------------------------------------------


def test_upload_with_explicit_acquisition_date(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    path = _write_geotiff(tmp_path / "dated.tif", _make_image(bands=1))
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "imagery", "acquisition_date": "2026-05-01T00:00:00"},
            files={"file": ("dated.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["acquisition_date"] is not None
    assert resp.json()["acquisition_date"].startswith("2026-05-01")


def test_upload_without_acquisition_date_stays_null(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    path = _write_geotiff(tmp_path / "undated.tif", _make_image(bands=1))
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "imagery"},
            files={"file": ("undated.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201
    assert resp.json()["acquisition_date"] is None
