from pathlib import Path

from fastapi.testclient import TestClient


def _create_project(client: TestClient) -> str:
    resp = client.post("/projects", json={"name": "Dataset Test Project"})
    return resp.json()["id"]


def test_upload_raster_dataset_extracts_metadata(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.tif").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "validated"
    assert body["file_format"] == "geotiff"
    assert body["crs"] is not None
    assert body["bbox_min_x"] is not None
    assert body["metadata_json"]["band_count"] == 1
    assert body["checksum_sha256"]


def test_upload_vector_dataset_extracts_metadata(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.geojson").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "buildings"},
            files={"file": ("sample.geojson", f, "application/geo+json")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "validated"
    assert body["crs"] is not None
    assert body["metadata_json"]["feature_count"] == 1


def test_upload_unsupported_extension_is_invalid(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("just some notes")
    with bad_file.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "other"},
            files={"file": ("notes.txt", f, "text/plain")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "invalid"
    assert "Unsupported file extension" in body["validation_message"]


def test_upload_corrupt_raster_is_invalid(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    bad_tif = tmp_path / "broken.tif"
    bad_tif.write_bytes(b"not a real tiff")
    with bad_tif.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("broken.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["validation_message"]


def test_upload_to_missing_project_returns_404(client: TestClient, fixtures_dir: Path) -> None:
    with (fixtures_dir / "sample.tif").open("rb") as f:
        resp = client.post(
            "/projects/does-not-exist/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    assert resp.status_code == 404


def test_list_filter_download_and_delete_dataset(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.tif").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    dataset_id = resp.json()["id"]

    resp = client.get(f"/projects/{project_id}/datasets", params={"dataset_type": "dem"})
    assert len(resp.json()) == 1

    resp = client.get(f"/projects/{project_id}/datasets", params={"dataset_type": "buildings"})
    assert len(resp.json()) == 0

    resp = client.get(f"/datasets/{dataset_id}/download")
    assert resp.status_code == 200

    resp = client.delete(f"/datasets/{dataset_id}")
    assert resp.status_code == 204

    resp = client.get(f"/datasets/{dataset_id}")
    assert resp.status_code == 404
