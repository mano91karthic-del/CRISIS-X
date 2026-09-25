import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus, DatasetType
from app.models.project import Project
from app.models.terrain_package import TerrainPackageStatus, TerrainXPackage
from app.schemas.dataset import DatasetRead
from app.schemas.terrain_package import TerrainPackageImportSummary, TerrainPackageRead
from app.services.archive_safety import ArchiveSecurityError, safe_extract_zip
from app.services.storage import checksum_and_copy, terrain_package_storage_dir
from app.services.terrain_package_import import (
    PackageRejected,
    ROLE_TO_DATASET_TYPE,
    parse_manifest,
    validate_asset_declaration,
)
from app.services.validation import validate_dataset_file

router = APIRouter(tags=["terrain-packages"])

MANIFEST_FILENAME = "manifest.json"


@router.post(
    "/projects/{project_id}/terrain-packages",
    response_model=TerrainPackageImportSummary,
    status_code=201,
)
async def import_terrain_package(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> TerrainPackageImportSummary:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    with tempfile.TemporaryDirectory(prefix="terrainx-import-") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "upload.zip"
        with zip_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        staging_dir = tmp_dir / "staged"
        staging_dir.mkdir()

        # --- whole-package structural validation: atomicity rule #1 ---
        # Nothing is persisted until this section passes.
        try:
            safe_extract_zip(zip_path, staging_dir)
        except ArchiveSecurityError as exc:
            raise HTTPException(status_code=400, detail=f"Rejected archive: {exc}") from exc

        manifest_path = staging_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise HTTPException(
                status_code=400, detail=f"Package is missing {MANIFEST_FILENAME} at its root."
            )

        try:
            manifest, manifest_raw = parse_manifest(manifest_path.read_bytes())
        except PackageRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # --- structurally valid: persist package + assets (atomicity rule #2) ---
        # From here on, per-asset failures are recorded, not raised — a bad
        # asset must not block its valid siblings.
        package = TerrainXPackage(
            project_id=project_id,
            contract_version=manifest.contract_version,
            package_reference=manifest.package_id,
            source_system=manifest.source.system,
            source_version=manifest.source.version,
            source_model=manifest.source.model,
            manifest_json=manifest_raw,
            storage_path="",
            status=TerrainPackageStatus.IMPORTED.value,
        )
        db.add(package)
        db.flush()  # assigns package.id

        package_dir = terrain_package_storage_dir(project_id, package.id)
        permanent_zip_path = package_dir / "package.zip"
        shutil.copy(zip_path, permanent_zip_path)
        package.storage_path = str(permanent_zip_path)

        created_datasets: list[Dataset] = []
        any_invalid = False
        staging_root = staging_dir.resolve()

        for asset in manifest.assets:
            dataset_type = ROLE_TO_DATASET_TYPE.get(asset.role, DatasetType.OTHER.value)
            dataset = Dataset(
                project_id=project_id,
                name=f"{manifest.source.system}: {asset.asset_id}",
                dataset_type=dataset_type,
                origin=DatasetOrigin.TERRAIN_X_IMPORT.value,
                terrain_x_package_id=package.id,
                source_filename=asset.file,
                storage_path="",
                file_size_bytes=0,
                checksum_sha256="",
                status=DatasetStatus.UPLOADED.value,
            )
            db.add(dataset)
            db.flush()  # assigns dataset.id

            base_provenance = {
                "terrain_x_package_id": package.id,
                "asset_id": asset.asset_id,
                "role": asset.role,
                "source_system": manifest.source.system,
                "source_version": manifest.source.version,
                "source_model": manifest.source.model,
            }

            declaration_error = validate_asset_declaration(asset)
            if declaration_error is not None:
                dataset.status = DatasetStatus.INVALID.value
                dataset.validation_message = declaration_error
                dataset.provenance = base_provenance
                any_invalid = True
                created_datasets.append(dataset)
                continue

            staged_file = (staging_dir / asset.file).resolve()
            if not staged_file.is_relative_to(staging_root) or not staged_file.is_file():
                dataset.status = DatasetStatus.INVALID.value
                dataset.validation_message = f"Declared asset file '{asset.file}' was not found in the package."
                dataset.provenance = base_provenance
                any_invalid = True
                created_datasets.append(dataset)
                continue

            dest_path, size, checksum = checksum_and_copy(staged_file, project_id, dataset.id)
            result = validate_dataset_file(dest_path)

            crs_mismatch = bool(
                asset.crs and result.crs and asset.crs.strip() != result.crs.strip()
            )
            checksum_mismatch = bool(asset.checksum_sha256 and asset.checksum_sha256 != checksum)

            dataset.storage_path = str(dest_path)
            dataset.file_size_bytes = size
            dataset.checksum_sha256 = checksum
            dataset.file_format = result.file_format
            dataset.crs = result.crs
            if result.bbox is not None:
                dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y = result.bbox
            dataset.status = result.status
            dataset.validation_message = result.message
            dataset.metadata_json = result.metadata
            dataset.provenance = {
                **base_provenance,
                "vertical_reference": asset.vertical_reference,
                "manifest_claimed_crs": asset.crs,
                "actual_crs": result.crs,
                "crs_mismatch": crs_mismatch,
                "manifest_checksum": asset.checksum_sha256,
                "actual_checksum": checksum,
                "checksum_mismatch": checksum_mismatch,
            }

            if dataset.status == DatasetStatus.INVALID.value:
                any_invalid = True

            created_datasets.append(dataset)

        package.status = (
            TerrainPackageStatus.PARTIALLY_INVALID.value if any_invalid else TerrainPackageStatus.IMPORTED.value
        )

        db.commit()
        db.refresh(package)
        for dataset in created_datasets:
            db.refresh(dataset)

        return TerrainPackageImportSummary(
            package=TerrainPackageRead.model_validate(package),
            datasets=[DatasetRead.model_validate(d) for d in created_datasets],
        )


@router.get("/projects/{project_id}/terrain-packages", response_model=list[TerrainPackageRead])
def list_terrain_packages(project_id: str, db: Session = Depends(get_db)) -> list[TerrainXPackage]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    stmt = (
        select(TerrainXPackage)
        .where(TerrainXPackage.project_id == project_id)
        .order_by(TerrainXPackage.imported_at.desc())
    )
    return list(db.execute(stmt).scalars())


@router.get("/terrain-packages/{package_id}", response_model=TerrainPackageRead)
def get_terrain_package(package_id: str, db: Session = Depends(get_db)) -> TerrainXPackage:
    package = db.get(TerrainXPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Terrain package not found")
    return package


@router.get("/terrain-packages/{package_id}/manifest")
def get_terrain_package_manifest(package_id: str, db: Session = Depends(get_db)) -> dict:
    package = db.get(TerrainXPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Terrain package not found")
    return package.manifest_json


@router.get("/terrain-packages/{package_id}/datasets", response_model=list[DatasetRead])
def list_terrain_package_datasets(package_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    package = db.get(TerrainXPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Terrain package not found")

    stmt = (
        select(Dataset)
        .where(Dataset.terrain_x_package_id == package_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
