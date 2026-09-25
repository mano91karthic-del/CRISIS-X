"""The TERRAIN-X package manifest contract (v1). See
docs/data-contracts/terrainx-package-v1.md for the human-readable spec this
mirrors.

`role` and `format` are intentionally loose (`str`, not `Literal`) at the
parse layer: an unrecognized value must not fail the whole manifest — it's
handled asset-by-asset in app/services/terrain_package_import.py, per the
Phase 3 atomicity rule (structurally valid manifest => package always
persists; individual assets may still be marked invalid).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.dataset import DatasetRead

SUPPORTED_CONTRACT_VERSIONS = {"1.0"}


class ManifestSource(BaseModel):
    system: str
    version: str | None = None
    model: str | None = None
    notes: str | None = None


class ManifestAreaOfInterest(BaseModel):
    name: str | None = None


class ManifestAsset(BaseModel):
    asset_id: str
    role: str
    file: str
    format: str
    vertical_reference: str | None = None
    crs: str | None = None
    checksum_sha256: str | None = None
    description: str | None = None
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TerrainPackageManifest(BaseModel):
    contract_version: str
    package_id: str | None = None
    generated_at: str | None = None
    source: ManifestSource
    area_of_interest: ManifestAreaOfInterest | None = None
    assets: list[ManifestAsset]

    @field_validator("assets")
    @classmethod
    def _must_have_at_least_one_asset(cls, v: list[ManifestAsset]) -> list[ManifestAsset]:
        if not v:
            raise ValueError("manifest must declare at least one asset")
        return v


class TerrainPackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    contract_version: str
    package_reference: str | None
    source_system: str
    source_version: str | None
    source_model: str | None
    manifest_json: dict[str, Any]
    storage_path: str
    status: str
    imported_at: datetime


class TerrainPackageImportSummary(BaseModel):
    package: TerrainPackageRead
    datasets: list[DatasetRead]
