import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus
from app.models.exposure_analysis import ExposureAnalysis, ExposureAnalysisStatus
from app.models.project import Project
from app.models.risk_analysis import RiskAnalysis, RiskAnalysisStatus
from app.schemas.dataset import DatasetRead
from app.schemas.risk import RiskAnalysisRead, RiskAnalysisRequest, RiskAnalysisResult
from app.services.risk import HAZARD_CLASS_MAX_CODE, run_risk_analysis
from app.services.storage import dataset_storage_dir

router = APIRouter(tags=["risk-analyses"])


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_exposure_analysis_or_400(db: Session, project_id: str, exposure_analysis_id: str) -> ExposureAnalysis:
    analysis = db.get(ExposureAnalysis, exposure_analysis_id)
    if analysis is None or analysis.project_id != project_id:
        raise HTTPException(status_code=404, detail="Exposure analysis not found in this project")
    if analysis.status != ExposureAnalysisStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Exposure analysis status is '{analysis.status}', not 'completed'.",
        )
    return analysis


def _ensure_supported_hazard_type(hazard_dataset_type: str) -> None:
    if hazard_dataset_type not in HAZARD_CLASS_MAX_CODE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported hazard_dataset_type '{hazard_dataset_type}' for risk scoring. "
                f"Supported: {sorted(HAZARD_CLASS_MAX_CODE)}."
            ),
        )


def _get_exposure_features_dataset(db: Session, exposure_analysis_id: str) -> Dataset | None:
    stmt = (
        select(Dataset)
        .where(
            Dataset.exposure_analysis_id == exposure_analysis_id,
            Dataset.dataset_type == "exposure_features",
            Dataset.status == DatasetStatus.VALIDATED.value,
        )
        .order_by(Dataset.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


@router.post(
    "/projects/{project_id}/risk-analyses",
    response_model=RiskAnalysisResult,
    status_code=201,
)
def create_risk_analysis(
    project_id: str,
    payload: RiskAnalysisRequest,
    db: Session = Depends(get_db),
) -> RiskAnalysisResult:
    _get_project_or_404(db, project_id)
    exposure_analysis = _get_exposure_analysis_or_400(db, project_id, payload.exposure_analysis_id)
    _ensure_supported_hazard_type(exposure_analysis.hazard_dataset_type)

    hazard_dataset = (
        db.get(Dataset, exposure_analysis.hazard_dataset_id) if exposure_analysis.hazard_dataset_id else None
    )
    exposure_dataset = (
        db.get(Dataset, exposure_analysis.exposure_dataset_id) if exposure_analysis.exposure_dataset_id else None
    )
    features_dataset = _get_exposure_features_dataset(db, exposure_analysis.id)

    features_path: Path | None = None
    if features_dataset is not None:
        features_path = Path(features_dataset.storage_path)
        _require_file(features_path)

    risk_breakpoints = tuple(payload.risk_breakpoints)

    analysis = RiskAnalysis(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        exposure_analysis_id=exposure_analysis.id,
        hazard_dataset_id=exposure_analysis.hazard_dataset_id,
        exposure_dataset_id=exposure_analysis.exposure_dataset_id,
        hazard_dataset_type=exposure_analysis.hazard_dataset_type,
        exposure_dataset_type=exposure_analysis.exposure_dataset_type,
        vulnerability_weight=payload.vulnerability_weight,
        consequence_weight=payload.consequence_weight,
        risk_breakpoints=list(risk_breakpoints),
        parameters={
            "vulnerability_weight": payload.vulnerability_weight,
            "consequence_weight": payload.consequence_weight,
            "risk_breakpoints": list(risk_breakpoints),
        },
        results={},
        status=RiskAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()  # assigns analysis.id

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="risk-analysis-") as tmp:
        # A temp path, not a Dataset's own storage dir: whether an output
        # risk-classification layer is even worth keeping isn't known until
        # after computation runs -- same rationale as exposure.py's own
        # provisional_output_path.
        provisional_output_path = Path(tmp) / "risk_classification.geojson"

        try:
            features_gdf = None
            if features_path is not None:
                import geopandas as gpd

                features_gdf = gpd.read_file(features_path)

            computation = run_risk_analysis(
                exposure_analysis.results,
                exposure_analysis.hazard_dataset_type,
                vulnerability_weight=payload.vulnerability_weight,
                consequence_weight=payload.consequence_weight,
                risk_breakpoints=risk_breakpoints,
                features_gdf=features_gdf,
            )
        except Exception as exc:
            analysis.status = RiskAnalysisStatus.FAILED.value
            analysis.error_message = str(exc)
            db.commit()
            db.refresh(analysis)
            return RiskAnalysisResult(analysis=RiskAnalysisRead.model_validate(analysis), datasets=[])

        analysis.results = computation.results
        db.add(analysis)

        if computation.output_gdf is not None and not computation.output_gdf.empty:
            computation.output_gdf.to_file(provisional_output_path, driver="GeoJSON")

            risk_dataset = Dataset(
                project_id=project_id,
                name=f"{payload.name} — risk classification",
                dataset_type="risk_classification",
                origin=DatasetOrigin.RISK_ANALYSIS.value,
                source_dataset_id=features_dataset.id if features_dataset is not None else None,
                risk_analysis_id=analysis.id,
                source_filename="risk_classification.geojson",
                storage_path="",
                file_size_bytes=0,
                checksum_sha256="",
                status=DatasetStatus.UPLOADED.value,
            )
            db.add(risk_dataset)
            db.flush()

            final_path = dataset_storage_dir(project_id, risk_dataset.id) / "risk_classification.geojson"
            final_path.write_bytes(provisional_output_path.read_bytes())

            risk_dataset.storage_path = str(final_path)
            risk_dataset.file_size_bytes = final_path.stat().st_size
            risk_dataset.checksum_sha256 = hashlib.sha256(final_path.read_bytes()).hexdigest()
            risk_dataset.file_format = "geojson"
            risk_dataset.crs = features_dataset.crs if features_dataset is not None else None
            bounds = computation.output_gdf.total_bounds
            (
                risk_dataset.bbox_min_x,
                risk_dataset.bbox_min_y,
                risk_dataset.bbox_max_x,
                risk_dataset.bbox_max_y,
            ) = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
            risk_dataset.status = DatasetStatus.VALIDATED.value
            risk_dataset.metadata_json = {"product": "risk_classification"}
            risk_dataset.provenance = {
                "risk_analysis_id": analysis.id,
                "exposure_analysis_id": exposure_analysis.id,
                "hazard_dataset_id": exposure_analysis.hazard_dataset_id,
                "hazard_dataset_name": hazard_dataset.name if hazard_dataset is not None else None,
                "exposure_dataset_id": exposure_analysis.exposure_dataset_id,
                "exposure_dataset_name": exposure_dataset.name if exposure_dataset is not None else None,
                **computation.results,
            }
            output_datasets.append(risk_dataset)

    db.commit()
    db.refresh(analysis)
    for dataset in output_datasets:
        db.refresh(dataset)

    return RiskAnalysisResult(
        analysis=RiskAnalysisRead.model_validate(analysis),
        datasets=[DatasetRead.model_validate(d) for d in output_datasets],
    )


@router.get("/projects/{project_id}/risk-analyses", response_model=list[RiskAnalysisRead])
def list_risk_analyses(
    project_id: str,
    hazard_dataset_type: str | None = None,
    exposure_dataset_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[RiskAnalysis]:
    _get_project_or_404(db, project_id)
    stmt = select(RiskAnalysis).where(RiskAnalysis.project_id == project_id)
    if hazard_dataset_type is not None:
        stmt = stmt.where(RiskAnalysis.hazard_dataset_type == hazard_dataset_type)
    if exposure_dataset_type is not None:
        stmt = stmt.where(RiskAnalysis.exposure_dataset_type == exposure_dataset_type)
    stmt = stmt.order_by(RiskAnalysis.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/risk-analyses/{analysis_id}", response_model=RiskAnalysisRead)
def get_risk_analysis(analysis_id: str, db: Session = Depends(get_db)) -> RiskAnalysis:
    analysis = db.get(RiskAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Risk analysis not found")
    return analysis


@router.get("/risk-analyses/{analysis_id}/datasets", response_model=list[DatasetRead])
def list_risk_analysis_datasets(analysis_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    analysis = db.get(RiskAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Risk analysis not found")

    stmt = (
        select(Dataset)
        .where(Dataset.risk_analysis_id == analysis_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
