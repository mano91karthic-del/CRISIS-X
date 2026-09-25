import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus
from app.models.eo_change_analysis import EOChangeAnalysis, EOChangeAnalysisStatus
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.schemas.eo_change import EOChangeAnalysisRead, EOChangeAnalysisRequest, EOChangeAnalysisResult
from app.services.eo_change import check_bounds_overlap, run_change_detection
from app.services.storage import dataset_storage_dir

router = APIRouter(tags=["eo-change-analyses"])

EO_IMAGE_DATASET_TYPE = "imagery"


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_validated_imagery(db: Session, project_id: str, dataset_id: str, role: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"{role} dataset not found in this project")
    if dataset.dataset_type != EO_IMAGE_DATASET_TYPE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{role} dataset_type '{dataset.dataset_type}' is not 'imagery'; only imagery "
                "datasets can be used for EO change detection."
            ),
        )
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"{role} dataset status is '{dataset.status}', not 'validated'."
        )
    return dataset


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


@router.post(
    "/projects/{project_id}/eo-change-analyses",
    response_model=EOChangeAnalysisResult,
    status_code=201,
)
def create_eo_change_analysis(
    project_id: str,
    payload: EOChangeAnalysisRequest,
    db: Session = Depends(get_db),
) -> EOChangeAnalysisResult:
    _get_project_or_404(db, project_id)
    before = _get_validated_imagery(db, project_id, payload.before_dataset_id, "before")
    after = _get_validated_imagery(db, project_id, payload.after_dataset_id, "after")

    before_path = Path(before.storage_path)
    after_path = Path(after.storage_path)
    _require_file(before_path)
    _require_file(after_path)

    if not check_bounds_overlap(before_path, after_path):
        raise HTTPException(
            status_code=400, detail="before and after images do not spatially overlap."
        )

    parameters = {
        "band_indices": payload.band_indices,
        "band_a_index": payload.band_a_index,
        "band_b_index": payload.band_b_index,
        "index_name": payload.index_name,
        "change_threshold": payload.change_threshold,
    }

    analysis = EOChangeAnalysis(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        before_dataset_id=before.id,
        after_dataset_id=after.id,
        source_format="geotiff",
        method=payload.method,
        threshold_method=payload.threshold_method,
        parameters=parameters,
        status=EOChangeAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()  # assigns analysis.id

    magnitude_dataset = Dataset(
        project_id=project_id,
        name=f"{payload.name} — change magnitude",
        dataset_type="eo_change_magnitude",
        origin=DatasetOrigin.EO_ANALYSIS.value,
        source_dataset_id=before.id,
        eo_change_analysis_id=analysis.id,
        source_filename="eo_change_magnitude.tif",
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    mask_dataset = Dataset(
        project_id=project_id,
        name=f"{payload.name} — change mask",
        dataset_type="eo_change_mask",
        origin=DatasetOrigin.EO_ANALYSIS.value,
        source_dataset_id=before.id,
        eo_change_analysis_id=analysis.id,
        source_filename="eo_change_mask.tif",
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    db.add(magnitude_dataset)
    db.add(mask_dataset)
    db.flush()  # assigns dataset ids

    magnitude_path = dataset_storage_dir(project_id, magnitude_dataset.id) / "eo_change_magnitude.tif"
    mask_path = dataset_storage_dir(project_id, mask_dataset.id) / "eo_change_mask.tif"

    try:
        result = run_change_detection(
            before_path,
            after_path,
            magnitude_path,
            mask_path,
            method=payload.method,
            band_indices=payload.band_indices,
            band_a_index=payload.band_a_index,
            band_b_index=payload.band_b_index,
            index_name=payload.index_name,
            threshold_method=payload.threshold_method,
            change_threshold=payload.change_threshold,
        )
    except Exception as exc:
        analysis.status = EOChangeAnalysisStatus.FAILED.value
        analysis.error_message = str(exc)
        for dataset in (magnitude_dataset, mask_dataset):
            dataset.status = DatasetStatus.INVALID.value
            dataset.validation_message = f"EO change analysis computation failed: {exc}"
        db.commit()
        db.refresh(analysis)
        db.refresh(magnitude_dataset)
        db.refresh(mask_dataset)
        return EOChangeAnalysisResult(
            analysis=EOChangeAnalysisRead.model_validate(analysis),
            datasets=[
                DatasetRead.model_validate(magnitude_dataset),
                DatasetRead.model_validate(mask_dataset),
            ],
        )

    base_provenance = {
        "eo_change_analysis_id": analysis.id,
        "before_dataset_id": before.id,
        "before_dataset_name": before.name,
        # Explicitly null when unknown -- never fabricated. See ADR 0006.
        "before_acquisition_date": before.acquisition_date.isoformat() if before.acquisition_date else None,
        "after_dataset_id": after.id,
        "after_dataset_name": after.name,
        "after_acquisition_date": after.acquisition_date.isoformat() if after.acquisition_date else None,
    }

    def _finalize(dataset: Dataset, output_path: Path, metadata: dict) -> None:
        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        dataset.storage_path = str(output_path)
        dataset.file_size_bytes = output_path.stat().st_size
        dataset.checksum_sha256 = checksum
        dataset.file_format = "geotiff"
        dataset.crs = result.crs
        dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y = result.bbox
        dataset.status = DatasetStatus.VALIDATED.value
        dataset.metadata_json = metadata
        dataset.provenance = {**base_provenance, **metadata}

    _finalize(magnitude_dataset, result.magnitude_output_path, result.magnitude_metadata)
    _finalize(mask_dataset, result.mask_output_path, result.mask_metadata)

    db.commit()
    db.refresh(analysis)
    db.refresh(magnitude_dataset)
    db.refresh(mask_dataset)

    return EOChangeAnalysisResult(
        analysis=EOChangeAnalysisRead.model_validate(analysis),
        datasets=[
            DatasetRead.model_validate(magnitude_dataset),
            DatasetRead.model_validate(mask_dataset),
        ],
    )


@router.get("/projects/{project_id}/eo-change-analyses", response_model=list[EOChangeAnalysisRead])
def list_eo_change_analyses(
    project_id: str,
    method: str | None = None,
    db: Session = Depends(get_db),
) -> list[EOChangeAnalysis]:
    _get_project_or_404(db, project_id)
    stmt = select(EOChangeAnalysis).where(EOChangeAnalysis.project_id == project_id)
    if method is not None:
        stmt = stmt.where(EOChangeAnalysis.method == method)
    stmt = stmt.order_by(EOChangeAnalysis.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/eo-change-analyses/{analysis_id}", response_model=EOChangeAnalysisRead)
def get_eo_change_analysis(analysis_id: str, db: Session = Depends(get_db)) -> EOChangeAnalysis:
    analysis = db.get(EOChangeAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="EO change analysis not found")
    return analysis


@router.get("/eo-change-analyses/{analysis_id}/datasets", response_model=list[DatasetRead])
def list_eo_change_analysis_datasets(analysis_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    analysis = db.get(EOChangeAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="EO change analysis not found")

    stmt = (
        select(Dataset)
        .where(Dataset.eo_change_analysis_id == analysis_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
