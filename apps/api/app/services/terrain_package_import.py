"""Phase 3: manifest parsing and per-asset declaration checks for TERRAIN-X
package imports. Actual DB/storage orchestration lives in
app/api/terrain_packages.py (mirroring how Phase 1's upload_dataset and
Phase 2's derive_dataset keep DB/storage orchestration in the API layer and
delegate pure logic to services).

Atomicity: parse_manifest raises PackageRejected for anything that means the
*whole package* should be rejected (bad JSON, contract violation, unsupported
contract_version) — callers must persist nothing in that case. Once a
manifest parses successfully, every asset is handled independently via
validate_asset_declaration; a bad asset never blocks its siblings.

CRISIS-X never trusts the manifest's claims (CRS, checksum) as ground truth
— see app/api/terrain_packages.py, which re-derives them from the actual
file via the existing app/services/validation.py, exactly like Phase 1
uploads.
"""

import json

from pydantic import ValidationError

from app.models.dataset import DatasetType
from app.schemas.terrain_package import SUPPORTED_CONTRACT_VERSIONS, ManifestAsset, TerrainPackageManifest

ALLOWED_ASSET_FORMATS = {"geotiff", "geojson", "gpkg", "csv"}
VALID_VERTICAL_REFERENCES = {"relative", "metric_calibrated", "unknown"}
ELEVATION_ROLES = {"dem", "dsm"}

# Roles the manifest may declare -> the DatasetType this system catalogs them
# as. An unrecognized role falls back to DatasetType.OTHER (forward-
# compatible: TERRAIN-X adding a new role later doesn't reject the package),
# with the raw role string preserved in the dataset's provenance.
ROLE_TO_DATASET_TYPE = {
    "dem": DatasetType.DEM.value,
    "dsm": DatasetType.DSM.value,
    "slope": "slope",  # same string Phase 2's /derive uses for its own output
    "aspect": "aspect",
    "flood_screening": DatasetType.FLOOD_SCREENING.value,
    "landslide_screening": DatasetType.LANDSLIDE_SUSCEPTIBILITY_SCREENING.value,
}


class PackageRejected(Exception):
    """Whole-package structural failure. Callers must persist nothing."""


def parse_manifest(raw_bytes: bytes) -> tuple[TerrainPackageManifest, dict]:
    """Parses and structurally validates manifest.json bytes.

    Returns (manifest, raw_dict). Raises PackageRejected for anything that
    should reject the entire package: invalid JSON, a shape that doesn't
    match the contract, or an unsupported contract_version.
    """
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise PackageRejected(f"manifest.json is not valid JSON: {exc}") from exc

    try:
        manifest = TerrainPackageManifest.model_validate(raw)
    except ValidationError as exc:
        raise PackageRejected(f"manifest.json does not match the terrain package contract: {exc}") from exc

    if manifest.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise PackageRejected(
            f"Unsupported contract_version '{manifest.contract_version}'. "
            f"Supported: {sorted(SUPPORTED_CONTRACT_VERSIONS)}."
        )

    return manifest, raw


def validate_asset_declaration(asset: ManifestAsset) -> str | None:
    """Pre-flight checks on what the manifest declares about one asset,
    before its file is even opened. Returns an error message (the asset
    should be marked invalid, package import continues with its siblings),
    or None if OK to proceed to opening the file.
    """
    fmt = (asset.format or "").lower()

    if fmt == "shapefile" or asset.file.lower().endswith(".shp"):
        return (
            "Shapefile assets are not supported by terrain package contract v1 "
            "(a Shapefile is multiple sidecar files; this contract version only "
            "accepts single-file assets). Use GeoJSON or GeoPackage instead."
        )

    if fmt not in ALLOWED_ASSET_FORMATS:
        return f"Unsupported asset format '{asset.format}'. Supported: {sorted(ALLOWED_ASSET_FORMATS)}."

    if asset.role in ELEVATION_ROLES:
        if asset.vertical_reference is None:
            return (
                "vertical_reference is required for dem/dsm assets and was not declared. "
                "Refusing to assume whether this is metric elevation or relative depth."
            )
        if asset.vertical_reference not in VALID_VERTICAL_REFERENCES:
            return (
                f"Unrecognized vertical_reference '{asset.vertical_reference}'. "
                f"Expected one of {sorted(VALID_VERTICAL_REFERENCES)}."
            )

    return None
