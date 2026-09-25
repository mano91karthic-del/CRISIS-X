from pathlib import Path

from fastapi.testclient import TestClient


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Derive Test Project"}).json()["id"]


def _upload(client: TestClient, project_id: str, path: Path, dataset_type: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (path.name, f, "application/octet-stream")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_derive_slope_from_dem(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")
    assert dem["status"] == "validated"

    resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["status"] == "validated"
    assert body["dataset_type"] == "slope"
    assert body["source_dataset_id"] == dem["id"]
    assert body["crs"] is not None
    assert body["bbox_min_x"] is not None
    assert body["metadata_json"]["product"] == "slope"
    assert body["metadata_json"]["method"] == "horn"
    assert body["provenance"]["source_dataset_id"] == dem["id"]


def test_derive_aspect_from_dsm(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    dsm = _upload(client, project_id, fixtures_dir / "sample.tif", "dsm")

    resp = client.post(f"/datasets/{dsm['id']}/derive", json={"product": "aspect"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dataset_type"] == "aspect"
    assert body["status"] == "validated"


def test_derived_dataset_is_reprojected_from_geographic_source(
    client: TestClient, fixtures_dir: Path
) -> None:
    # sample.tif is EPSG:4326 (geographic); the derived product must be in a
    # projected (metric) CRS, not degrees.
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")

    resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    body = resp.json()
    assert "4326" not in body["crs"]
    assert body["metadata_json"]["reprojected"] is True


def test_derive_appears_in_derivatives_list_and_dataset_listing(
    client: TestClient, fixtures_dir: Path
) -> None:
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")
    derived = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"}).json()

    resp = client.get(f"/datasets/{dem['id']}/derivatives")
    assert resp.status_code == 200
    ids = [d["id"] for d in resp.json()]
    assert derived["id"] in ids

    resp = client.get(f"/projects/{project_id}/datasets", params={"dataset_type": "slope"})
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()] == [derived["id"]]


def test_derive_rejects_non_dem_source(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    buildings = _upload(client, project_id, fixtures_dir / "sample.geojson", "buildings")

    resp = client.post(f"/datasets/{buildings['id']}/derive", json={"product": "slope"})
    assert resp.status_code == 400
    assert "dem" in resp.json()["detail"].lower() or "dsm" in resp.json()["detail"].lower()


def test_derive_rejects_invalid_status_source(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    bad_tif = tmp_path / "broken.tif"
    bad_tif.write_bytes(b"not a real tiff")
    bad = _upload(client, project_id, bad_tif, "dem")
    assert bad["status"] == "invalid"

    resp = client.post(f"/datasets/{bad['id']}/derive", json={"product": "slope"})
    assert resp.status_code == 400


def test_derive_rejects_unknown_source_dataset(client: TestClient) -> None:
    resp = client.post("/datasets/does-not-exist/derive", json={"product": "slope"})
    assert resp.status_code == 404


def test_derive_rejects_invalid_product_name(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")

    resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "curvature"})
    assert resp.status_code == 422


def test_derivatives_list_empty_for_unrelated_dataset(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")

    resp = client.get(f"/datasets/{dem['id']}/derivatives")
    assert resp.status_code == 200
    assert resp.json() == []


def test_derivatives_list_404_for_unknown_dataset(client: TestClient) -> None:
    resp = client.get("/datasets/does-not-exist/derivatives")
    assert resp.status_code == 404


def test_deleting_source_dataset_sets_derived_source_id_null(
    client: TestClient, fixtures_dir: Path
) -> None:
    project_id = _create_project(client)
    dem = _upload(client, project_id, fixtures_dir / "sample.tif", "dem")
    derived = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"}).json()

    resp = client.delete(f"/datasets/{dem['id']}")
    assert resp.status_code == 204

    resp = client.get(f"/datasets/{derived['id']}")
    assert resp.status_code == 200
    assert resp.json()["source_dataset_id"] is None
