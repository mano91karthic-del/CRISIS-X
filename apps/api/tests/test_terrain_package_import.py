"""Manifest-parsing unit tests (no files, no DB) for the Phase 3 TERRAIN-X
package contract.
"""

import json

import pytest

from app.services.terrain_package_import import (
    PackageRejected,
    parse_manifest,
    validate_asset_declaration,
)
from app.schemas.terrain_package import ManifestAsset

VALID_MANIFEST = {
    "contract_version": "1.0",
    "package_id": "run-123",
    "source": {"system": "TERRAIN-X", "version": "0.3.1"},
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


def test_parses_a_valid_manifest() -> None:
    manifest, raw = parse_manifest(json.dumps(VALID_MANIFEST).encode())
    assert manifest.contract_version == "1.0"
    assert manifest.source.system == "TERRAIN-X"
    assert len(manifest.assets) == 1
    assert raw == VALID_MANIFEST


def test_rejects_invalid_json() -> None:
    with pytest.raises(PackageRejected):
        parse_manifest(b"{not valid json")


def test_rejects_missing_required_fields() -> None:
    bad = {"contract_version": "1.0", "assets": []}  # missing `source`
    with pytest.raises(PackageRejected):
        parse_manifest(json.dumps(bad).encode())


def test_rejects_empty_assets_list() -> None:
    bad = {**VALID_MANIFEST, "assets": []}
    with pytest.raises(PackageRejected):
        parse_manifest(json.dumps(bad).encode())


def test_rejects_unsupported_contract_version() -> None:
    bad = {**VALID_MANIFEST, "contract_version": "99.0"}
    with pytest.raises(PackageRejected):
        parse_manifest(json.dumps(bad).encode())


def test_unknown_role_parses_fine_at_manifest_level() -> None:
    # Graceful degradation happens at import time (falls back to "other"),
    # not at parse time — an unrecognized role must not reject the manifest.
    variant = {**VALID_MANIFEST}
    variant["assets"] = [{**VALID_MANIFEST["assets"][0], "role": "some_future_role"}]
    manifest, _ = parse_manifest(json.dumps(variant).encode())
    assert manifest.assets[0].role == "some_future_role"


def _asset(**overrides) -> ManifestAsset:
    base = dict(
        asset_id="a1",
        role="dsm",
        file="dsm.tif",
        format="geotiff",
        vertical_reference="metric_calibrated",
        crs="EPSG:4326",
    )
    base.update(overrides)
    return ManifestAsset(**base)


def test_declaration_ok_for_valid_dem_asset() -> None:
    assert validate_asset_declaration(_asset()) is None


def test_declaration_rejects_missing_vertical_reference_for_dem() -> None:
    error = validate_asset_declaration(_asset(role="dem", vertical_reference=None))
    assert error is not None
    assert "vertical_reference" in error


def test_declaration_rejects_bad_vertical_reference_value() -> None:
    error = validate_asset_declaration(_asset(vertical_reference="definitely_real_trust_me"))
    assert error is not None


def test_declaration_allows_relative_and_unknown_vertical_reference() -> None:
    assert validate_asset_declaration(_asset(vertical_reference="relative")) is None
    assert validate_asset_declaration(_asset(vertical_reference="unknown")) is None


def test_declaration_does_not_require_vertical_reference_for_non_elevation_roles() -> None:
    error = validate_asset_declaration(
        _asset(role="flood_screening", format="geojson", vertical_reference=None)
    )
    assert error is None


def test_declaration_rejects_shapefile_by_format() -> None:
    error = validate_asset_declaration(_asset(role="other", format="shapefile", file="roads.shp"))
    assert error is not None
    assert "shapefile" in error.lower()


def test_declaration_rejects_shp_extension_even_with_other_format_label() -> None:
    error = validate_asset_declaration(_asset(role="other", format="geojson", file="roads.shp"))
    assert error is not None
    assert "shapefile" in error.lower()


def test_declaration_rejects_unsupported_format() -> None:
    error = validate_asset_declaration(_asset(role="other", format="netcdf", file="thing.nc"))
    assert error is not None
    assert "unsupported asset format" in error.lower()
