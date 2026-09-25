"""API-level tests for TERRAIN-X package import. Packages are built
on-the-fly in each test (a real small GeoTIFF via rasterio + a manifest),
never committed as opaque binary fixtures — keeps the exchange contract
self-documenting in the test itself.
"""

import json
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _make_dem_bytes(tmp_path: Path, *, crs: str = "EPSG:4326") -> bytes:
    path = tmp_path / "_tmp_dem.tif"
    transform = from_origin(77.0, 13.0, 0.01, 0.01)
    data = np.arange(16, dtype="float32").reshape(4, 4)
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1,
        dtype=data.dtype, crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(data, 1)
    return path.read_bytes()


def _build_package_zip(
    tmp_path: Path,
    name: str,
    manifest: dict,
    files: dict[str, bytes],
) -> Path:
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for filename, content in files.items():
            zf.writestr(filename, content)
    return zip_path


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Terrain Package Test Project"}).json()["id"]


def _import(client: TestClient, project_id: str, zip_path: Path):
    with zip_path.open("rb") as f:
        return client.post(
            f"/projects/{project_id}/terrain-packages",
            files={"file": (zip_path.name, f, "application/zip")},
        )


def test_import_valid_package_creates_package_and_datasets(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "package_id": "run-001",
        "source": {"system": "TERRAIN-X", "version": "0.3.1", "model": "depth-anything-v2"},
        "assets": [
            {
                "asset_id": "dsm_main",
                "role": "dsm",
                "file": "dsm.tif",
                "format": "geotiff",
                "vertical_reference": "metric_calibrated",
                "crs": "EPSG:4326",
            }
        ],
    }
    zip_path = _build_package_zip(tmp_path, "pkg.zip", manifest, {"dsm.tif": dem_bytes})

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["package"]["contract_version"] == "1.0"
    assert body["package"]["source_system"] == "TERRAIN-X"
    assert body["package"]["status"] == "imported"
    assert len(body["datasets"]) == 1

    dataset = body["datasets"][0]
    assert dataset["dataset_type"] == "dsm"
    assert dataset["origin"] == "terrain_x_import"
    assert dataset["terrain_x_package_id"] == body["package"]["id"]
    assert dataset["status"] == "validated"
    assert dataset["crs"] == "EPSG:4326"
    assert dataset["provenance"]["vertical_reference"] == "metric_calibrated"
    assert dataset["provenance"]["crs_mismatch"] is False


def test_manifest_claimed_crs_mismatch_is_flagged_but_file_wins(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path, crs="EPSG:4326")
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_main",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                "vertical_reference": "metric_calibrated",
                "crs": "EPSG:32643",  # deliberately wrong vs the actual file
            }
        ],
    }
    zip_path = _build_package_zip(tmp_path, "mismatch.zip", manifest, {"dem.tif": dem_bytes})

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 201
    dataset = resp.json()["datasets"][0]

    assert dataset["crs"] == "EPSG:4326"  # the actual file's own CRS wins
    assert dataset["provenance"]["manifest_claimed_crs"] == "EPSG:32643"
    assert dataset["provenance"]["crs_mismatch"] is True


def test_relative_dem_imports_but_cannot_be_derived_from(client: TestClient, tmp_path: Path) -> None:
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
    zip_path = _build_package_zip(tmp_path, "relative.zip", manifest, {"dem.tif": dem_bytes})

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 201
    dataset = resp.json()["datasets"][0]
    assert dataset["status"] == "validated"  # cataloged fine — it's a valid file

    derive_resp = client.post(f"/datasets/{dataset['id']}/derive", json={"product": "slope"})
    assert derive_resp.status_code == 400
    assert "relative" in derive_resp.json()["detail"].lower()


def test_metric_calibrated_terrain_x_dem_can_be_derived_from(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_calibrated",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                "vertical_reference": "metric_calibrated",
            }
        ],
    }
    zip_path = _build_package_zip(tmp_path, "calibrated.zip", manifest, {"dem.tif": dem_bytes})

    dataset = _import(client, project_id, zip_path).json()["datasets"][0]

    derive_resp = client.post(f"/datasets/{dataset['id']}/derive", json={"product": "slope"})
    assert derive_resp.status_code == 201
    assert derive_resp.json()["dataset_type"] == "slope"


def test_dem_missing_vertical_reference_is_invalid_but_siblings_still_import(
    client: TestClient, tmp_path: Path
) -> None:
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_bad",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                # vertical_reference omitted entirely — must fail closed
            },
            {
                "asset_id": "flood_layer",
                "role": "flood_screening",
                "file": "flood.tif",
                "format": "geotiff",
            },
        ],
    }
    zip_path = _build_package_zip(
        tmp_path, "partial.zip", manifest, {"dem.tif": dem_bytes, "flood.tif": dem_bytes}
    )

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 201
    body = resp.json()

    assert body["package"]["status"] == "partially_invalid"
    by_asset_id = {d["provenance"]["asset_id"]: d for d in body["datasets"]}
    assert by_asset_id["dem_bad"]["status"] == "invalid"
    assert "vertical_reference" in by_asset_id["dem_bad"]["validation_message"]
    assert by_asset_id["flood_layer"]["status"] == "validated"
    assert by_asset_id["flood_layer"]["dataset_type"] == "flood_screening"


def test_shapefile_asset_is_deferred_not_silently_broken(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {"asset_id": "roads", "role": "other", "file": "roads.shp", "format": "shapefile"}
        ],
    }
    zip_path = _build_package_zip(tmp_path, "shp.zip", manifest, {"roads.shp": b"not a real shapefile"})

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 201
    dataset = resp.json()["datasets"][0]
    assert dataset["status"] == "invalid"
    assert "shapefile" in dataset["validation_message"].lower()


def test_missing_manifest_rejects_whole_package(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    zip_path = tmp_path / "no_manifest.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dem.tif", b"whatever")

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 400

    assert client.get(f"/projects/{project_id}/terrain-packages").json() == []


def test_bad_zip_rejects_whole_package(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    zip_path = tmp_path / "garbage.zip"
    zip_path.write_bytes(b"not a zip at all")

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 400
    assert client.get(f"/projects/{project_id}/terrain-packages").json() == []


def test_unsupported_contract_version_rejects_whole_package(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    manifest = {
        "contract_version": "99.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [{"asset_id": "x", "role": "other", "file": "x.csv", "format": "csv"}],
    }
    zip_path = _build_package_zip(tmp_path, "badversion.zip", manifest, {"x.csv": b"a,b\n1,2\n"})

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 400
    assert client.get(f"/projects/{project_id}/terrain-packages").json() == []


def test_path_traversal_in_asset_file_is_rejected_at_archive_level(
    client: TestClient, tmp_path: Path
) -> None:
    project_id = _create_project(client)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [{"asset_id": "x", "role": "other", "file": "../evil.csv", "format": "csv"}],
    }
    zip_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("../evil.csv", b"a,b\n1,2\n")

    resp = _import(client, project_id, zip_path)
    assert resp.status_code == 400
    assert client.get(f"/projects/{project_id}/terrain-packages").json() == []


def test_package_and_asset_listing_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_main",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                "vertical_reference": "metric_calibrated",
            }
        ],
    }
    zip_path = _build_package_zip(tmp_path, "listing.zip", manifest, {"dem.tif": dem_bytes})
    imported = _import(client, project_id, zip_path).json()
    package_id = imported["package"]["id"]

    resp = client.get(f"/projects/{project_id}/terrain-packages")
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()] == [package_id]

    resp = client.get(f"/terrain-packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == package_id

    resp = client.get(f"/terrain-packages/{package_id}/manifest")
    assert resp.status_code == 200
    assert resp.json()["contract_version"] == "1.0"

    resp = client.get(f"/terrain-packages/{package_id}/datasets")
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()] == [imported["datasets"][0]["id"]]


def test_import_to_missing_project_returns_404(client: TestClient, tmp_path: Path) -> None:
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [{"asset_id": "x", "role": "other", "file": "x.csv", "format": "csv"}],
    }
    zip_path = _build_package_zip(tmp_path, "any.zip", manifest, {"x.csv": b"a,b\n1,2\n"})

    resp = _import(client, "does-not-exist", zip_path)
    assert resp.status_code == 404


def test_terrain_package_not_found_404s(client: TestClient) -> None:
    assert client.get("/terrain-packages/does-not-exist").status_code == 404
    assert client.get("/terrain-packages/does-not-exist/manifest").status_code == 404
    assert client.get("/terrain-packages/does-not-exist/datasets").status_code == 404


def test_deleting_source_terrain_x_dataset_sets_package_link_intact_via_dataset_delete(
    client: TestClient, tmp_path: Path
) -> None:
    # Deleting the *package* is out of scope for Phase 3, but deleting an
    # imported *dataset* should behave exactly like any other dataset delete.
    project_id = _create_project(client)
    dem_bytes = _make_dem_bytes(tmp_path)
    manifest = {
        "contract_version": "1.0",
        "source": {"system": "TERRAIN-X"},
        "assets": [
            {
                "asset_id": "dem_main",
                "role": "dem",
                "file": "dem.tif",
                "format": "geotiff",
                "vertical_reference": "metric_calibrated",
            }
        ],
    }
    zip_path = _build_package_zip(tmp_path, "del.zip", manifest, {"dem.tif": dem_bytes})
    dataset = _import(client, project_id, zip_path).json()["datasets"][0]

    resp = client.delete(f"/datasets/{dataset['id']}")
    assert resp.status_code == 204
    assert client.get(f"/datasets/{dataset['id']}").status_code == 404


def test_uploaded_and_derived_datasets_get_correct_origin(client: TestClient, fixtures_dir: Path) -> None:
    project_id = _create_project(client)
    with (fixtures_dir / "sample.tif").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "dem"},
            files={"file": ("sample.tif", f, "image/tiff")},
        )
    dem = resp.json()
    assert dem["origin"] == "uploaded"

    derived = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"}).json()
    assert derived["origin"] == "crisisx_derived"
