"""API-level integration tests for the Phase 4 hazard-scenario endpoints:
full upload -> derive prerequisites -> run scenario -> verify output
dataset fields/provenance, plus validation/atomicity/failure-path coverage.
"""

from pathlib import Path

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _make_dem_bytes(tmp_path: Path, name: str = "_tmp_dem.tif", size: int = 8) -> bytes:
    # A larger, gently-sloped synthetic DEM (bigger than the 4x4 Phase 1
    # fixture) so flow direction/accumulation/HAND have enough cells to be
    # meaningful, and a real outlet/channel emerges.
    path = tmp_path / name
    transform = from_origin(77.0, 13.0, 0.001, 0.001)
    rows, cols = size, size
    # South-facing ramp with a slight central channel (lower along the
    # middle column) so flow concentrates predictably.
    r, c = np.indices((rows, cols))
    center = cols // 2
    data = (rows - r).astype("float32") * 2.0 - np.exp(-((c - center) ** 2) / 4.0) * 1.5
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(data.astype("float32"), 1)
    return path.read_bytes()


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Hazard Test Project"}).json()["id"]


def _upload_dem(client: TestClient, project_id: str, tmp_path: Path, dataset_type: str = "dem") -> dict:
    dem_bytes = _make_dem_bytes(tmp_path)
    with (tmp_path / "upload_dem.tif").open("wb") as f:
        f.write(dem_bytes)
    with (tmp_path / "upload_dem.tif").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": ("dem.tif", f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _derive(client: TestClient, dataset_id: str, product: str) -> dict:
    resp = client.post(f"/datasets/{dataset_id}/derive", json={"product": product})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- flood ------------------------------------------------------------


def test_flood_scenario_end_to_end(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_direction")
    _derive(client, dem["id"], "flow_accumulation")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={
            "name": "Test Flood Scenario",
            "dem_dataset_id": dem["id"],
            "depth_above_drainage_m": 3.0,
            "channel_threshold_cells": 4,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["scenario"]["hazard_type"] == "flood"
    assert body["scenario"]["status"] == "completed"
    assert body["scenario"]["parameters"]["depth_above_drainage_m"] == 3.0

    dataset = body["datasets"][0]
    assert dataset["dataset_type"] == "flood_inundation"
    assert dataset["origin"] == "hazard_model"
    assert dataset["status"] == "validated"
    assert dataset["hazard_scenario_id"] == body["scenario"]["id"]
    assert dataset["provenance"]["hazard_type"] == "flood"
    assert dataset["provenance"]["parameters"]["depth_above_drainage_m"] == 3.0
    assert "not a hydraulic simulation" in " ".join(dataset["metadata_json"]["limitations"])
    assert any(
        "not derived from rainfall" in limitation for limitation in dataset["metadata_json"]["limitations"]
    )


def test_flood_scenario_requires_flow_direction_derivative(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_accumulation")  # flow_direction missing

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "x", "dem_dataset_id": dem["id"], "depth_above_drainage_m": 1.0},
    )
    assert resp.status_code == 400
    assert "flow_direction" in resp.json()["detail"]
    assert client.get(f"/projects/{project_id}/hazard-scenarios").json() == []


def test_flood_scenario_requires_flow_accumulation_derivative(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_direction")  # flow_accumulation missing

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "x", "dem_dataset_id": dem["id"], "depth_above_drainage_m": 1.0},
    )
    assert resp.status_code == 400
    assert "flow_accumulation" in resp.json()["detail"]


def test_flood_scenario_rejects_negative_depth(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_direction")
    _derive(client, dem["id"], "flow_accumulation")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "x", "dem_dataset_id": dem["id"], "depth_above_drainage_m": -1.0},
    )
    assert resp.status_code == 422
    assert client.get(f"/projects/{project_id}/hazard-scenarios").json() == []


def test_flood_scenario_dem_not_found_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/hazard-scenarios/flood",
        json={"name": "x", "dem_dataset_id": "also-missing", "depth_above_drainage_m": 1.0},
    )
    assert resp.status_code == 404


def test_flood_scenario_rejects_non_dem_source(client: TestClient, tmp_path: Path, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.geojson").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "buildings"},
            files={"file": ("sample.geojson", f, "application/geo+json")},
        )
    buildings = resp.json()

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "x", "dem_dataset_id": buildings["id"], "depth_above_drainage_m": 1.0},
    )
    assert resp.status_code == 400
    assert "dem" in resp.json()["detail"].lower() or "dsm" in resp.json()["detail"].lower()


def test_flood_scenario_invalid_rainfall_reference_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_direction")
    _derive(client, dem["id"], "flow_accumulation")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={
            "name": "x",
            "dem_dataset_id": dem["id"],
            "depth_above_drainage_m": 1.0,
            "rainfall_dataset_id": "does-not-exist",
        },
    )
    assert resp.status_code == 400


def test_flood_scenario_valid_rainfall_reference_recorded_but_inert(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "flow_direction")
    _derive(client, dem["id"], "flow_accumulation")

    with (tmp_path / "rain.csv").open("w") as f:
        f.write("date,mm\n2026-01-01,50\n")
    with (tmp_path / "rain.csv").open("rb") as f:
        rainfall = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "rainfall"},
            files={"file": ("rain.csv", f, "text/csv")},
        ).json()

    resp_a = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={"name": "a", "dem_dataset_id": dem["id"], "depth_above_drainage_m": 2.0},
    )
    resp_b = client.post(
        f"/projects/{project_id}/hazard-scenarios/flood",
        json={
            "name": "b",
            "dem_dataset_id": dem["id"],
            "depth_above_drainage_m": 2.0,
            "rainfall_dataset_id": rainfall["id"],
        },
    )
    assert resp_a.status_code == 201 and resp_b.status_code == 201
    a_meta = resp_a.json()["datasets"][0]["metadata_json"]
    b_meta = resp_b.json()["datasets"][0]["metadata_json"]
    # Same depth parameter -> identical inundation result regardless of
    # whether a rainfall reference was attached (proves it's inert).
    assert a_meta["inundated_cell_count"] == b_meta["inundated_cell_count"]
    assert resp_b.json()["scenario"]["rainfall_dataset_id"] == rainfall["id"]


# --- landslide ----------------------------------------------------------


def test_landslide_scenario_end_to_end(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "slope")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Test Landslide Scenario", "dem_dataset_id": dem["id"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["scenario"]["hazard_type"] == "landslide"
    dataset = body["datasets"][0]
    assert dataset["dataset_type"] == "landslide_susceptibility"
    assert dataset["origin"] == "hazard_model"
    assert dataset["metadata_json"]["slope_breakpoints_deg"] == [5.0, 15.0, 25.0, 35.0]
    assert dataset["metadata_json"]["class_legend"]["1"] == "very_low"
    assert any("susceptibility screening" in lim for lim in dataset["metadata_json"]["limitations"])


def test_landslide_scenario_requires_slope_derivative(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "x", "dem_dataset_id": dem["id"]},
    )
    assert resp.status_code == 400
    assert "slope" in resp.json()["detail"]


def test_landslide_scenario_custom_breakpoints_used_and_recorded(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "slope")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={
            "name": "x",
            "dem_dataset_id": dem["id"],
            "slope_breakpoints_deg": [10.0, 20.0, 30.0, 40.0],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["datasets"][0]["metadata_json"]["slope_breakpoints_deg"] == [10.0, 20.0, 30.0, 40.0]


def test_landslide_scenario_rejects_non_ascending_breakpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "slope")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={
            "name": "x",
            "dem_dataset_id": dem["id"],
            "slope_breakpoints_deg": [10.0, 5.0, 30.0, 40.0],
        },
    )
    assert resp.status_code == 422
    assert client.get(f"/projects/{project_id}/hazard-scenarios").json() == []


def test_landslide_rainfall_context_recorded_but_does_not_change_class(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "slope")

    resp_dry = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "dry", "dem_dataset_id": dem["id"], "rainfall_context": "dry"},
    )
    resp_saturated = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "saturated", "dem_dataset_id": dem["id"], "rainfall_context": "saturated"},
    )
    assert resp_dry.status_code == 201 and resp_saturated.status_code == 201

    dry_counts = resp_dry.json()["datasets"][0]["metadata_json"]["class_cell_counts"]
    saturated_counts = resp_saturated.json()["datasets"][0]["metadata_json"]["class_cell_counts"]
    assert dry_counts == saturated_counts  # proves rainfall_context has zero scoring effect

    assert resp_dry.json()["datasets"][0]["metadata_json"]["rainfall_context"] == "dry"
    assert resp_saturated.json()["datasets"][0]["metadata_json"]["rainfall_context"] == "saturated"


def test_landslide_scenario_terrain_x_relative_dem_rejected(client: TestClient, tmp_path: Path) -> None:
    import json
    import zipfile

    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_relative",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                "vertical_reference": "relative",
            }
        ],
    }
    zip_path = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("dem.tif", dem_bytes)

    with zip_path.open("rb") as f:
        imported = client.post(
            f"/projects/{project_id}/terrain-packages",
            files={"file": ("pkg.zip", f, "application/zip")},
        ).json()
    dem = imported["datasets"][0]

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "x", "dem_dataset_id": dem["id"]},
    )
    assert resp.status_code == 400
    assert "relative" in resp.json()["detail"].lower()


# --- listing / detail endpoints -----------------------------------------


def test_hazard_scenario_listing_and_detail_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    _derive(client, dem["id"], "slope")

    created = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "x", "dem_dataset_id": dem["id"]},
    ).json()
    scenario_id = created["scenario"]["id"]

    resp = client.get(f"/projects/{project_id}/hazard-scenarios")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()] == [scenario_id]

    resp = client.get(f"/projects/{project_id}/hazard-scenarios", params={"hazard_type": "flood"})
    assert resp.json() == []

    resp = client.get(f"/hazard-scenarios/{scenario_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == scenario_id

    resp = client.get(f"/hazard-scenarios/{scenario_id}/datasets")
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()] == [created["datasets"][0]["id"]]


def test_hazard_scenario_not_found_404s(client: TestClient) -> None:
    assert client.get("/hazard-scenarios/does-not-exist").status_code == 404
    assert client.get("/hazard-scenarios/does-not-exist/datasets").status_code == 404


def test_hazard_scenario_missing_input_file_returns_410(client: TestClient, tmp_path: Path) -> None:
    # Pre-flight file-existence check: the derivative is cataloged but its
    # file has since been removed from disk.
    from app.core.config import get_settings

    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    slope = _derive(client, dem["id"], "slope")

    settings = get_settings()
    candidates = list(Path(settings.data_storage_root).rglob(f"{slope['id']}/*.tif"))
    assert candidates, "expected the derived slope raster to exist on disk"
    candidates[0].unlink()

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "x", "dem_dataset_id": dem["id"]},
    )
    assert resp.status_code == 410


def test_hazard_scenario_runtime_failure_is_persisted_not_500(client: TestClient, tmp_path: Path) -> None:
    # File exists (passes the pre-flight check) but is corrupted, so the
    # failure happens inside compute() -- exercising _run_hazard_scenario's
    # try/except path: the scenario and a diagnosable output dataset must
    # still be persisted, never a raw 500.
    from app.core.config import get_settings

    project_id = _create_project(client)
    dem = _upload_dem(client, project_id, tmp_path)
    slope = _derive(client, dem["id"], "slope")

    settings = get_settings()
    candidates = list(Path(settings.data_storage_root).rglob(f"{slope['id']}/*.tif"))
    assert candidates
    candidates[0].write_bytes(b"not a real geotiff")

    resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "x", "dem_dataset_id": dem["id"]},
    )
    assert resp.status_code == 201, resp.text  # request itself succeeds; failure is recorded inside
    body = resp.json()
    assert body["scenario"]["status"] == "failed"
    assert body["scenario"]["error_message"]
    assert body["datasets"][0]["status"] == "invalid"
    assert body["datasets"][0]["validation_message"]
