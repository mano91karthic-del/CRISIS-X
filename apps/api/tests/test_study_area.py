"""API-level tests for ADR 0013's canonical study-area concept: uploading
a STUDY_AREA dataset, clipping another vector dataset to it
(/datasets/{id}/clip), and setting/clearing a Digital Twin's
study_area_dataset_id (/digital-twins/{id}/study-area).
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _create_project(client: TestClient, name: str = "Study Area Test Project") -> str:
    return client.post("/projects", json={"name": name}).json()["id"]


def _write_geojson(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def _upload_vector(client: TestClient, project_id: str, path: Path, dataset_type: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (path.name, f, "application/geo+json")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


_AOI_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[80.0, 13.0], [80.02, 13.0], [80.02, 13.02], [80.0, 13.02], [80.0, 13.0]]],
}


def _upload_aoi(client: TestClient, project_id: str, tmp_path: Path) -> dict:
    path = tmp_path / "aoi.geojson"
    _write_geojson(path, [{"type": "Feature", "geometry": _AOI_GEOMETRY, "properties": {}}])
    return _upload_vector(client, project_id, path, "study_area")


def _upload_buildings(client: TestClient, project_id: str, tmp_path: Path) -> dict:
    path = tmp_path / "buildings.geojson"
    inside = {"type": "Point", "coordinates": [80.01, 13.01]}
    outside = {"type": "Point", "coordinates": [90.0, 20.0]}
    _write_geojson(
        path,
        [
            {"type": "Feature", "geometry": inside, "properties": {"id": 1}},
            {"type": "Feature", "geometry": outside, "properties": {"id": 2}},
        ],
    )
    return _upload_vector(client, project_id, path, "buildings")


def test_clip_dataset_keeps_only_features_inside_the_aoi(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    aoi = _upload_aoi(client, project_id, tmp_path)
    buildings = _upload_buildings(client, project_id, tmp_path)

    resp = client.post(f"/datasets/{buildings['id']}/clip", json={"aoi_dataset_id": aoi["id"]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "validated"
    assert body["dataset_type"] == "buildings"  # clipped output keeps the source's own dataset_type
    assert body["metadata_json"]["kept_feature_count"] == 1
    assert body["metadata_json"]["dropped_feature_count"] == 1
    assert body["provenance"]["aoi_dataset_id"] == aoi["id"]
    assert body["provenance"]["source_dataset_id"] == buildings["id"]


def test_clip_dataset_rejects_a_non_study_area_aoi(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    buildings = _upload_buildings(client, project_id, tmp_path)
    not_an_aoi = _upload_buildings(client, project_id, tmp_path)  # any non-study_area dataset

    resp = client.post(f"/datasets/{buildings['id']}/clip", json={"aoi_dataset_id": not_an_aoi["id"]})
    assert resp.status_code == 400
    assert "study_area" in resp.json()["detail"]


def test_clip_dataset_rejects_a_raster_source(client: TestClient, tmp_path: Path, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    aoi = _upload_aoi(client, project_id, tmp_path)
    with (fixtures_dir / "sample.tif").open("rb") as f:
        raster_resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    raster = raster_resp.json()

    resp = client.post(f"/datasets/{raster['id']}/clip", json={"aoi_dataset_id": aoi["id"]})
    assert resp.status_code == 400
    assert "vector" in resp.json()["detail"].lower()


def test_clip_dataset_with_zero_overlap_returns_invalid_not_empty_success(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    aoi = _upload_aoi(client, project_id, tmp_path)
    path = tmp_path / "far_away.geojson"
    _write_geojson(path, [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}, "properties": {}}])
    far_away = _upload_vector(client, project_id, path, "buildings")

    resp = client.post(f"/datasets/{far_away['id']}/clip", json={"aoi_dataset_id": aoi["id"]})
    assert resp.status_code == 201  # dataset row is created either way, like /derive
    body = resp.json()
    assert body["status"] == "invalid"
    assert "do not spatially overlap" in body["validation_message"]


def test_set_and_get_study_area_on_a_twin(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    aoi = _upload_aoi(client, project_id, tmp_path)
    twin = client.post(f"/projects/{project_id}/digital-twin", json={"name": "Twin"}).json()
    assert twin["study_area_dataset_id"] is None

    resp = client.put(f"/digital-twins/{twin['id']}/study-area", json={"dataset_id": aoi["id"]})
    assert resp.status_code == 200
    assert resp.json()["study_area_dataset_id"] == aoi["id"]

    refetched = client.get(f"/digital-twins/{twin['id']}").json()
    assert refetched["study_area_dataset_id"] == aoi["id"]

    # Clearing it back to None must also work.
    cleared = client.put(f"/digital-twins/{twin['id']}/study-area", json={"dataset_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["study_area_dataset_id"] is None


def test_set_study_area_rejects_a_non_study_area_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    buildings = _upload_buildings(client, project_id, tmp_path)
    twin = client.post(f"/projects/{project_id}/digital-twin", json={"name": "Twin"}).json()

    resp = client.put(f"/digital-twins/{twin['id']}/study-area", json={"dataset_id": buildings["id"]})
    assert resp.status_code == 400
    assert "study_area" in resp.json()["detail"]


def test_set_study_area_rejects_a_dataset_from_another_project(client: TestClient, tmp_path: Path) -> None:
    project_a = _create_project(client, "Project A")
    project_b = _create_project(client, "Project B")
    aoi_in_b = _upload_aoi(client, project_b, tmp_path)
    twin_a = client.post(f"/projects/{project_a}/digital-twin", json={"name": "Twin A"}).json()

    resp = client.put(f"/digital-twins/{twin_a['id']}/study-area", json={"dataset_id": aoi_in_b["id"]})
    assert resp.status_code == 404
