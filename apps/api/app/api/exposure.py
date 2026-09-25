import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus, DatasetType
from app.models.exposure_analysis import ExposureAnalysis, ExposureAnalysisStatus
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.schemas.exposure import ExposureAnalysisRead, ExposureAnalysisRequest, ExposureAnalysisResult
from app.services.exposure import SUPPORTED_HAZARD_TYPES, run_exposure_analysis
from app.services.storage import dataset_storage_dir
from app.services.validation import RASTER_EXTENSIONS, VECTOR_EXTENSIONS

router = APIRouter(tags=["exposure-analyses"])

HAZARD_ORIGINS = {DatasetOrigin.HAZARD_MODEL.value, DatasetOrigin.EO_ANALYSIS.value}


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_hazard_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Hazard dataset not found in this project")
    if dataset.origin not in HAZARD_ORIGINS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dataset origin '{dataset.origin}' is not a hazard/change-analysis output "
                f"(expected one of {sorted(HAZARD_ORIGINS)})."
            ),
        )
    if dataset.dataset_type not in SUPPORTED_HAZARD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported hazard dataset_type '{dataset.dataset_type}'. "
                f"Supported: {sorted(SUPPORTED_HAZARD_TYPES)}."
            ),
        )
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"Hazard dataset status is '{dataset.status}', not 'validated'."
        )
    return dataset


_EXPOSURE_VECTOR_FORMATS = {ext.lstrip(".") for ext in VECTOR_EXTENSIONS}
_EXPOSURE_RASTER_FORMATS = {ext.lstrip(".") for ext in RASTER_EXTENSIONS}


def _get_exposure_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Exposure dataset not found in this project")
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"Exposure dataset status is '{dataset.status}', not 'validated'."
        )
    file_format = (dataset.file_format or "").lower()
    if file_format == "geotiff":
        if dataset.dataset_type != DatasetType.POPULATION.value:
            raise HTTPException(
                status_code=400,
                detail="Only dataset_type='population' rasters can be used as a raster exposure input.",
            )
    elif file_format not in _EXPOSURE_VECTOR_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Exposure dataset file_format '{dataset.file_format}' has no usable geometry "
                "(only rasters and vector formats are supported)."
            ),
        )
    return dataset


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


def _resolve_population_field(
    exposure_dataset: Dataset, exposure_path: Path, requested_field: str | None
) -> str | None:
    is_raster = (exposure_dataset.file_format or "").lower() == "geotiff"

    if exposure_dataset.dataset_type != DatasetType.POPULATION.value:
        if requested_field is not None:
            raise HTTPException(
                status_code=400,
                detail="population_field is only applicable when exposure_dataset_id refers to population data.",
            )
        return None

    if is_raster:
        if requested_field is not None:
            raise HTTPException(
                status_code=400, detail="population_field is not applicable to raster population data."
            )
        return None

    if requested_field is None:
        raise HTTPException(
            status_code=400,
            detail="population_field is required when exposure_dataset_id refers to vector population data.",
        )

    import geopandas as gpd

    gdf = gpd.read_file(exposure_path, rows=1)
    if requested_field not in gdf.columns:
        raise HTTPException(
            status_code=400,
            detail=f"population_field '{requested_field}' not found in exposure dataset columns: {list(gdf.columns)}.",
        )
    return requested_field


@router.post(
    "/projects/{project_id}/exposure-analyses",
    response_model=ExposureAnalysisResult,
    status_code=201,
)
def create_exposure_analysis(
    project_id: str,
    payload: ExposureAnalysisRequest,
    db: Session = Depends(get_db),
) -> ExposureAnalysisResult:
    _get_project_or_404(db, project_id)
    hazard = _get_hazard_dataset(db, project_id, payload.hazard_dataset_id)
    exposure = _get_exposure_dataset(db, project_id, payload.exposure_dataset_id)

    hazard_path = Path(hazard.storage_path)
    exposure_path = Path(exposure.storage_path)
    _require_file(hazard_path)
    _require_file(exposure_path)

    population_field = _resolve_population_field(exposure, exposure_path, payload.population_field)

    class_legend_override = None
    if hazard.dataset_type == "landslide_susceptibility":
        class_legend_override = hazard.metadata_json.get("class_legend")

    analysis = ExposureAnalysis(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        hazard_dataset_id=hazard.id,
        exposure_dataset_id=exposure.id,
        hazard_dataset_type=hazard.dataset_type,
        exposure_dataset_type=exposure.dataset_type,
        population_field=population_field,
        parameters={"population_field": population_field},
        results={},
        status=ExposureAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()  # assigns analysis.id

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="exposure-analysis-") as tmp:
        # A temp path, not a Dataset's own storage dir: whether an output
        # feature layer is even worth keeping isn't known until after
        # computation runs, so nothing is written into permanent dataset
        # storage until that's decided -- avoids leaving an orphaned
        # directory behind for the (common) case of no meaningful output.
        provisional_output_path = Path(tmp) / "exposure_features.geojson"

        try:
            result = run_exposure_analysis(
                hazard_path,
                hazard.dataset_type,
                exposure_path,
                provisional_output_path,
                population_field=population_field,
                class_legend_override=class_legend_override,
            )
        except Exception as exc:
            analysis.status = ExposureAnalysisStatus.FAILED.value
            analysis.error_message = str(exc)
            db.commit()
            db.refresh(analysis)
            return ExposureAnalysisResult(analysis=ExposureAnalysisRead.model_validate(analysis), datasets=[])

        analysis.results = result.results
        db.add(analysis)

        if result.output_path is not None:
            feature_dataset = Dataset(
                project_id=project_id,
                name=f"{payload.name} — exposure features",
                dataset_type="exposure_features",
                origin=DatasetOrigin.EXPOSURE_ANALYSIS.value,
                source_dataset_id=exposure.id,
                exposure_analysis_id=analysis.id,
                source_filename="exposure_features.geojson",
                storage_path="",
                file_size_bytes=0,
                checksum_sha256="",
                status=DatasetStatus.UPLOADED.value,
            )
            db.add(feature_dataset)
            db.flush()

            final_path = dataset_storage_dir(project_id, feature_dataset.id) / "exposure_features.geojson"
            final_path.write_bytes(result.output_path.read_bytes())

            feature_dataset.storage_path = str(final_path)
            feature_dataset.file_size_bytes = final_path.stat().st_size
            feature_dataset.checksum_sha256 = hashlib.sha256(final_path.read_bytes()).hexdigest()
            feature_dataset.file_format = "geojson"
            feature_dataset.crs = result.output_crs
            if result.output_bbox is not None:
                (
                    feature_dataset.bbox_min_x,
                    feature_dataset.bbox_min_y,
                    feature_dataset.bbox_max_x,
                    feature_dataset.bbox_max_y,
                ) = result.output_bbox
            feature_dataset.status = DatasetStatus.VALIDATED.value
            feature_dataset.metadata_json = {"product": "exposure_features"}
            feature_dataset.provenance = {
                "exposure_analysis_id": analysis.id,
                "hazard_dataset_id": hazard.id,
                "hazard_dataset_name": hazard.name,
                "exposure_dataset_id": exposure.id,
                "exposure_dataset_name": exposure.name,
                **result.results,
            }
            output_datasets.append(feature_dataset)

    db.commit()
    db.refresh(analysis)
    for dataset in output_datasets:
        db.refresh(dataset)

    return ExposureAnalysisResult(
        analysis=ExposureAnalysisRead.model_validate(analysis),
        datasets=[DatasetRead.model_validate(d) for d in output_datasets],
    )


@router.get("/projects/{project_id}/exposure-analyses", response_model=list[ExposureAnalysisRead])
def list_exposure_analyses(
    project_id: str,
    exposure_dataset_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[ExposureAnalysis]:
    _get_project_or_404(db, project_id)
    stmt = select(ExposureAnalysis).where(ExposureAnalysis.project_id == project_id)
    if exposure_dataset_type is not None:
        stmt = stmt.where(ExposureAnalysis.exposure_dataset_type == exposure_dataset_type)
    stmt = stmt.order_by(ExposureAnalysis.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/exposure-analyses/{analysis_id}", response_model=ExposureAnalysisRead)
def get_exposure_analysis(analysis_id: str, db: Session = Depends(get_db)) -> ExposureAnalysis:
    analysis = db.get(ExposureAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Exposure analysis not found")
    return analysis


@router.get("/exposure-analyses/{analysis_id}/datasets", response_model=list[DatasetRead])
def list_exposure_analysis_datasets(analysis_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    analysis = db.get(ExposureAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Exposure analysis not found")

    stmt = (
        select(Dataset)
        .where(Dataset.exposure_analysis_id == analysis_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
