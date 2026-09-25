import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus, DatasetType
from app.models.project import Project
from app.models.risk_analysis import RiskAnalysis, RiskAnalysisStatus
from app.models.route_analysis import RouteAnalysis, RouteAnalysisStatus
from app.schemas.dataset import DatasetRead
from app.schemas.routing import RouteAnalysisRead, RouteAnalysisRequest, RouteAnalysisResult
from app.services.exposure import SUPPORTED_HAZARD_TYPES
from app.services.routing import run_route_analysis
from app.services.storage import dataset_storage_dir
from app.services.validation import VECTOR_EXTENSIONS

router = APIRouter(tags=["route-analyses"])

HAZARD_ORIGINS = {DatasetOrigin.HAZARD_MODEL.value, DatasetOrigin.EO_ANALYSIS.value}
_ROAD_VECTOR_FORMATS = {ext.lstrip(".") for ext in VECTOR_EXTENSIONS}


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_road_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Road dataset not found in this project")
    if dataset.dataset_type != DatasetType.ROADS.value:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type '{dataset.dataset_type}' is not 'roads'; cannot use as a routing network.",
        )
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"Road dataset status is '{dataset.status}', not 'validated'."
        )
    file_format = (dataset.file_format or "").lower()
    if file_format not in _ROAD_VECTOR_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Road dataset file_format '{dataset.file_format}' is not a supported vector format.",
        )
    return dataset


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


def _get_risk_analysis_or_400(
    db: Session, project_id: str, risk_analysis_id: str, hazard_dataset_id: str, road_dataset_id: str
) -> RiskAnalysis:
    risk_analysis = db.get(RiskAnalysis, risk_analysis_id)
    if risk_analysis is None or risk_analysis.project_id != project_id:
        raise HTTPException(status_code=404, detail="Risk analysis not found in this project")
    if risk_analysis.status != RiskAnalysisStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400, detail=f"Risk analysis status is '{risk_analysis.status}', not 'completed'."
        )
    if risk_analysis.hazard_dataset_id != hazard_dataset_id:
        raise HTTPException(
            status_code=400,
            detail="Risk analysis hazard dataset must match the hazard dataset used for this route analysis.",
        )
    # The Phase 7 vulnerability_weight/consequence_weight are assumptions
    # declared for the specific exposure asset used in that RiskAnalysis --
    # they must never be silently applied to a different asset type (here,
    # roads). Requires the exact same Dataset row to have been used both as
    # this route's road_dataset_id and as that risk analysis's exposure
    # input: roads -> Phase 6 exposure analysis on those roads -> Phase 7
    # risk analysis on that exposure analysis -> Phase 8 risk-aware routing.
    if risk_analysis.exposure_dataset_id != road_dataset_id:
        raise HTTPException(
            status_code=400,
            detail="Risk analysis exposure dataset must match the road dataset for risk-aware routing.",
        )
    return risk_analysis


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


@router.post(
    "/projects/{project_id}/route-analyses",
    response_model=RouteAnalysisResult,
    status_code=201,
)
def create_route_analysis(
    project_id: str,
    payload: RouteAnalysisRequest,
    db: Session = Depends(get_db),
) -> RouteAnalysisResult:
    _get_project_or_404(db, project_id)
    road = _get_road_dataset(db, project_id, payload.road_dataset_id)
    hazard = _get_hazard_dataset(db, project_id, payload.hazard_dataset_id)

    risk_analysis: RiskAnalysis | None = None
    if payload.risk_analysis_id is not None:
        risk_analysis = _get_risk_analysis_or_400(
            db, project_id, payload.risk_analysis_id, hazard.id, road.id
        )

    road_path = Path(road.storage_path)
    hazard_path = Path(hazard.storage_path)
    _require_file(road_path)
    _require_file(hazard_path)

    class_legend_override = None
    if hazard.dataset_type == "landslide_susceptibility":
        class_legend_override = hazard.metadata_json.get("class_legend")

    parameters = {
        "hazard_penalty_weight": payload.hazard_penalty_weight,
        "block_threshold": payload.block_threshold,
        "node_snap_tolerance_m": payload.node_snap_tolerance_m,
        "max_snap_distance_m": payload.max_snap_distance_m,
        "risk_analysis_id": payload.risk_analysis_id,
    }

    analysis = RouteAnalysis(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        road_dataset_id=road.id,
        hazard_dataset_id=hazard.id,
        hazard_dataset_type=hazard.dataset_type,
        risk_analysis_id=risk_analysis.id if risk_analysis is not None else None,
        origin_lon=payload.origin.lon,
        origin_lat=payload.origin.lat,
        destination_lon=payload.destination.lon,
        destination_lat=payload.destination.lat,
        hazard_penalty_weight=payload.hazard_penalty_weight,
        block_threshold=payload.block_threshold,
        node_snap_tolerance_m=payload.node_snap_tolerance_m,
        max_snap_distance_m=payload.max_snap_distance_m,
        parameters=parameters,
        results={},
        status=RouteAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()  # assigns analysis.id

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="route-analysis-") as tmp:
        tmp_dir = Path(tmp)

        try:
            computation = run_route_analysis(
                road_path,
                hazard_path,
                hazard.dataset_type,
                origin_lon=payload.origin.lon,
                origin_lat=payload.origin.lat,
                destination_lon=payload.destination.lon,
                destination_lat=payload.destination.lat,
                hazard_penalty_weight=payload.hazard_penalty_weight,
                block_threshold=payload.block_threshold,
                node_snap_tolerance_m=payload.node_snap_tolerance_m,
                max_snap_distance_m=payload.max_snap_distance_m,
                class_legend_override=class_legend_override,
                vulnerability_weight=risk_analysis.vulnerability_weight if risk_analysis is not None else None,
                consequence_weight=risk_analysis.consequence_weight if risk_analysis is not None else None,
            )
        except Exception as exc:
            analysis.status = RouteAnalysisStatus.FAILED.value
            analysis.error_message = str(exc)
            db.commit()
            db.refresh(analysis)
            return RouteAnalysisResult(analysis=RouteAnalysisRead.model_validate(analysis), datasets=[])

        analysis.results = computation.results
        db.add(analysis)

        base_provenance = {
            "route_analysis_id": analysis.id,
            "road_dataset_id": road.id,
            "road_dataset_name": road.name,
            "hazard_dataset_id": hazard.id,
            "hazard_dataset_name": hazard.name,
            "risk_analysis_id": risk_analysis.id if risk_analysis is not None else None,
            "hazard_dataset_type": hazard.dataset_type,
        }

        def _persist_output(gdf, dataset_type: str, filename: str, route_type: str) -> None:
            gdf.to_file(tmp_dir / filename, driver="GeoJSON")
            output_dataset = Dataset(
                project_id=project_id,
                name=f"{payload.name} — {dataset_type}",
                dataset_type=dataset_type,
                origin=DatasetOrigin.ROUTE_ANALYSIS.value,
                source_dataset_id=road.id,
                route_analysis_id=analysis.id,
                source_filename=filename,
                storage_path="",
                file_size_bytes=0,
                checksum_sha256="",
                status=DatasetStatus.UPLOADED.value,
            )
            db.add(output_dataset)
            db.flush()

            final_path = dataset_storage_dir(project_id, output_dataset.id) / filename
            final_path.write_bytes((tmp_dir / filename).read_bytes())

            output_dataset.storage_path = str(final_path)
            output_dataset.file_size_bytes = final_path.stat().st_size
            output_dataset.checksum_sha256 = hashlib.sha256(final_path.read_bytes()).hexdigest()
            output_dataset.file_format = "geojson"
            output_dataset.crs = computation.working_crs
            bounds = gdf.total_bounds
            (
                output_dataset.bbox_min_x,
                output_dataset.bbox_min_y,
                output_dataset.bbox_max_x,
                output_dataset.bbox_max_y,
            ) = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
            output_dataset.status = DatasetStatus.VALIDATED.value
            output_dataset.metadata_json = {"product": dataset_type}
            output_dataset.provenance = {
                **base_provenance,
                "route_type": route_type,
                **computation.results,
            }
            output_datasets.append(output_dataset)

        if computation.shortest_route_gdf is not None:
            _persist_output(computation.shortest_route_gdf, "route_shortest", "route_shortest.geojson", "shortest")
        if computation.hazard_aware_route_gdf is not None:
            _persist_output(
                computation.hazard_aware_route_gdf, "route_hazard_aware", "route_hazard_aware.geojson", "hazard_aware"
            )
        if computation.blocked_segments_gdf is not None:
            _persist_output(
                computation.blocked_segments_gdf,
                "route_blocked_segments",
                "route_blocked_segments.geojson",
                "blocked_segments",
            )

    db.commit()
    db.refresh(analysis)
    for dataset in output_datasets:
        db.refresh(dataset)

    return RouteAnalysisResult(
        analysis=RouteAnalysisRead.model_validate(analysis),
        datasets=[DatasetRead.model_validate(d) for d in output_datasets],
    )


@router.get("/projects/{project_id}/route-analyses", response_model=list[RouteAnalysisRead])
def list_route_analyses(
    project_id: str,
    hazard_dataset_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[RouteAnalysis]:
    _get_project_or_404(db, project_id)
    stmt = select(RouteAnalysis).where(RouteAnalysis.project_id == project_id)
    if hazard_dataset_type is not None:
        stmt = stmt.where(RouteAnalysis.hazard_dataset_type == hazard_dataset_type)
    stmt = stmt.order_by(RouteAnalysis.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/route-analyses/{analysis_id}", response_model=RouteAnalysisRead)
def get_route_analysis(analysis_id: str, db: Session = Depends(get_db)) -> RouteAnalysis:
    analysis = db.get(RouteAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Route analysis not found")
    return analysis


@router.get("/route-analyses/{analysis_id}/datasets", response_model=list[DatasetRead])
def list_route_analysis_datasets(analysis_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    analysis = db.get(RouteAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Route analysis not found")

    stmt = (
        select(Dataset)
        .where(Dataset.route_analysis_id == analysis_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
