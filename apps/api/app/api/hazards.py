import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus
from app.models.hazard_scenario import HazardScenario, HazardScenarioStatus, HazardType
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.schemas.hazard import (
    FloodScenarioRequest,
    HazardScenarioRead,
    HazardScenarioResult,
    LandslideScenarioRequest,
)
from app.services.dataset_gates import SourceNotEligible, ensure_metric_calibrated_if_terrain_x_import
from app.services.hazard_flood import run_flood_scenario
from app.services.hazard_landslide import run_landslide_scenario
from app.services.storage import dataset_storage_dir

router = APIRouter(tags=["hazard-scenarios"])

DEM_SOURCE_TYPES = {"dem", "dsm"}


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_validated_dem(db: Session, project_id: str, dem_dataset_id: str) -> Dataset:
    dem = db.get(Dataset, dem_dataset_id)
    if dem is None or dem.project_id != project_id:
        raise HTTPException(status_code=404, detail="DEM/DSM dataset not found in this project")
    if dem.dataset_type not in DEM_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"dataset_type '{dem.dataset_type}' is not a DEM/DSM; cannot use as a "
                "hazard-model elevation source."
            ),
        )
    if dem.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"DEM/DSM status is '{dem.status}', not 'validated'."
        )
    try:
        ensure_metric_calibrated_if_terrain_x_import(dem)
    except SourceNotEligible as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dem


def _get_derivative_or_400(db: Session, dem_id: str, dataset_type: str) -> Dataset:
    stmt = (
        select(Dataset)
        .where(Dataset.source_dataset_id == dem_id, Dataset.dataset_type == dataset_type)
        .order_by(Dataset.created_at.desc())
    )
    derivative = db.execute(stmt).scalars().first()
    if derivative is None or derivative.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No validated '{dataset_type}' derivative found for dataset {dem_id}. "
                f"Run POST /datasets/{dem_id}/derive with product='{dataset_type}' first."
            ),
        )
    return derivative


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


def _validate_rainfall_reference(db: Session, project_id: str, rainfall_dataset_id: str | None) -> None:
    if rainfall_dataset_id is None:
        return
    rainfall = db.get(Dataset, rainfall_dataset_id)
    if rainfall is None or rainfall.project_id != project_id:
        raise HTTPException(status_code=400, detail="rainfall_dataset_id does not exist in this project")


def _run_hazard_scenario(
    db: Session,
    *,
    project_id: str,
    hazard_type: str,
    name: str,
    description: str | None,
    input_datasets: dict[str, str],
    parameters: dict[str, Any],
    rainfall_dataset_id: str | None,
    source_dataset_id: str,
    output_dataset_type: str,
    output_filename: str,
    compute: Callable[[Path], Any],
) -> HazardScenarioResult:
    """Shared orchestration for both hazard endpoints: creates the scenario
    record and a placeholder output Dataset, runs `compute`, and finalizes
    both on success or records a diagnosable failure on exception — mirrors
    Phase 2's /derive and Phase 3's package-asset handling exactly, so a
    computation failure is always a persisted, explicit record, never a raw
    500. `compute` returns an object exposing .output_path/.crs/.bbox/.metadata.
    """
    scenario = HazardScenario(
        project_id=project_id,
        hazard_type=hazard_type,
        name=name,
        description=description,
        input_datasets=input_datasets,
        parameters=parameters,
        rainfall_dataset_id=rainfall_dataset_id,
        status=HazardScenarioStatus.COMPLETED.value,
    )
    db.add(scenario)
    db.flush()

    output_dataset = Dataset(
        project_id=project_id,
        name=f"{name} — {output_dataset_type}",
        dataset_type=output_dataset_type,
        origin=DatasetOrigin.HAZARD_MODEL.value,
        source_dataset_id=source_dataset_id,
        hazard_scenario_id=scenario.id,
        source_filename=output_filename,
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    db.add(output_dataset)
    db.flush()

    output_dir = dataset_storage_dir(project_id, output_dataset.id)
    output_path = output_dir / output_filename

    try:
        result = compute(output_path)
    except Exception as exc:
        scenario.status = HazardScenarioStatus.FAILED.value
        scenario.error_message = str(exc)
        output_dataset.status = DatasetStatus.INVALID.value
        output_dataset.validation_message = f"Hazard scenario computation failed: {exc}"
        db.commit()
        db.refresh(scenario)
        db.refresh(output_dataset)
        return HazardScenarioResult(
            scenario=HazardScenarioRead.model_validate(scenario),
            datasets=[DatasetRead.model_validate(output_dataset)],
        )

    checksum = hashlib.sha256(result.output_path.read_bytes()).hexdigest()
    output_dataset.storage_path = str(result.output_path)
    output_dataset.file_size_bytes = result.output_path.stat().st_size
    output_dataset.checksum_sha256 = checksum
    output_dataset.file_format = "geotiff"
    output_dataset.crs = result.crs
    output_dataset.bbox_min_x, output_dataset.bbox_min_y, output_dataset.bbox_max_x, output_dataset.bbox_max_y = (
        result.bbox
    )
    output_dataset.status = DatasetStatus.VALIDATED.value
    output_dataset.metadata_json = result.metadata
    output_dataset.provenance = {
        "hazard_scenario_id": scenario.id,
        "hazard_type": hazard_type,
        "method": result.metadata.get("method"),
        "input_datasets": input_datasets,
        "parameters": parameters,
        "rainfall_dataset_id": rainfall_dataset_id,
        "limitations": result.metadata.get("limitations", []),
    }

    db.commit()
    db.refresh(scenario)
    db.refresh(output_dataset)

    return HazardScenarioResult(
        scenario=HazardScenarioRead.model_validate(scenario),
        datasets=[DatasetRead.model_validate(output_dataset)],
    )


@router.post(
    "/projects/{project_id}/hazard-scenarios/flood",
    response_model=HazardScenarioResult,
    status_code=201,
)
def create_flood_scenario(
    project_id: str,
    payload: FloodScenarioRequest,
    db: Session = Depends(get_db),
) -> HazardScenarioResult:
    _get_project_or_404(db, project_id)
    dem = _get_validated_dem(db, project_id, payload.dem_dataset_id)
    flow_direction = _get_derivative_or_400(db, dem.id, "flow_direction")
    flow_accumulation = _get_derivative_or_400(db, dem.id, "flow_accumulation")
    _validate_rainfall_reference(db, project_id, payload.rainfall_dataset_id)

    dem_path = Path(dem.storage_path)
    direction_path = Path(flow_direction.storage_path)
    accumulation_path = Path(flow_accumulation.storage_path)
    for path in (dem_path, direction_path, accumulation_path):
        _require_file(path)

    return _run_hazard_scenario(
        db,
        project_id=project_id,
        hazard_type=HazardType.FLOOD.value,
        name=payload.name,
        description=payload.description,
        input_datasets={
            "dem": dem.id,
            "flow_direction": flow_direction.id,
            "flow_accumulation": flow_accumulation.id,
        },
        parameters={
            "depth_above_drainage_m": payload.depth_above_drainage_m,
            "channel_threshold_cells": payload.channel_threshold_cells,
        },
        rainfall_dataset_id=payload.rainfall_dataset_id,
        source_dataset_id=dem.id,
        output_dataset_type="flood_inundation",
        output_filename="flood_inundation.tif",
        compute=lambda output_path: run_flood_scenario(
            dem_path,
            direction_path,
            accumulation_path,
            output_path,
            depth_above_drainage_m=payload.depth_above_drainage_m,
            channel_threshold_cells=payload.channel_threshold_cells,
        ),
    )


@router.post(
    "/projects/{project_id}/hazard-scenarios/landslide",
    response_model=HazardScenarioResult,
    status_code=201,
)
def create_landslide_scenario(
    project_id: str,
    payload: LandslideScenarioRequest,
    db: Session = Depends(get_db),
) -> HazardScenarioResult:
    _get_project_or_404(db, project_id)
    dem = _get_validated_dem(db, project_id, payload.dem_dataset_id)
    slope = _get_derivative_or_400(db, dem.id, "slope")
    _validate_rainfall_reference(db, project_id, payload.rainfall_dataset_id)

    slope_path = Path(slope.storage_path)
    _require_file(slope_path)

    return _run_hazard_scenario(
        db,
        project_id=project_id,
        hazard_type=HazardType.LANDSLIDE.value,
        name=payload.name,
        description=payload.description,
        input_datasets={"dem": dem.id, "slope": slope.id},
        parameters={
            "slope_breakpoints_deg": payload.slope_breakpoints_deg,
            "rainfall_context": payload.rainfall_context,
        },
        rainfall_dataset_id=payload.rainfall_dataset_id,
        source_dataset_id=dem.id,
        output_dataset_type="landslide_susceptibility",
        output_filename="landslide_susceptibility.tif",
        compute=lambda output_path: run_landslide_scenario(
            slope_path,
            output_path,
            slope_breakpoints_deg=tuple(payload.slope_breakpoints_deg),
            rainfall_context=payload.rainfall_context,
        ),
    )


@router.get("/projects/{project_id}/hazard-scenarios", response_model=list[HazardScenarioRead])
def list_hazard_scenarios(
    project_id: str,
    hazard_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[HazardScenario]:
    _get_project_or_404(db, project_id)
    stmt = select(HazardScenario).where(HazardScenario.project_id == project_id)
    if hazard_type is not None:
        stmt = stmt.where(HazardScenario.hazard_type == hazard_type)
    stmt = stmt.order_by(HazardScenario.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/hazard-scenarios/{scenario_id}", response_model=HazardScenarioRead)
def get_hazard_scenario(scenario_id: str, db: Session = Depends(get_db)) -> HazardScenario:
    scenario = db.get(HazardScenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Hazard scenario not found")
    return scenario


@router.get("/hazard-scenarios/{scenario_id}/datasets", response_model=list[DatasetRead])
def list_hazard_scenario_datasets(scenario_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    scenario = db.get(HazardScenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Hazard scenario not found")

    stmt = (
        select(Dataset)
        .where(Dataset.hazard_scenario_id == scenario_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
