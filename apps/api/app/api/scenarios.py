"""Phase 10: Scenario Lab API.

Orchestration and comparison only -- every hazard/exposure/risk/route
"run" triggered through a scenario calls the exact same Phase 4/6/7/8
`run_*` service functions those phases already expose (see the imports
below), with scenario-supplied parameter/dataset overrides. No hazard,
exposure, risk, or routing algorithm is reimplemented here.

A Scenario is always rooted at one DigitalTwin (`twin_id`). Its baseline
is a frozen, point-in-time copy of that twin's active layers, captured at
creation and never updated afterward -- no code path in this module
writes to `digital_twins` or `twin_layers`. See
docs/architecture/0011-phase-10-scenario-lab.md for the full reasoning.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus, DatasetType
from app.models.digital_twin import DigitalTwin, TwinLayer, TwinLayerStatus
from app.models.exposure_analysis import ExposureAnalysis, ExposureAnalysisStatus
from app.models.hazard_scenario import HazardScenario, HazardScenarioStatus, HazardType
from app.models.risk_analysis import RiskAnalysis, RiskAnalysisStatus
from app.models.route_analysis import RouteAnalysis, RouteAnalysisStatus
from app.models.scenario import Scenario, ScenarioBaselineLayer, ScenarioLayerOverride, ScenarioStatus
from app.schemas.dataset import DatasetRead
from app.schemas.exposure import ExposureAnalysisRead, ExposureAnalysisResult
from app.schemas.hazard import HazardScenarioRead, HazardScenarioResult
from app.schemas.risk import RiskAnalysisRead, RiskAnalysisResult
from app.schemas.routing import RouteAnalysisRead, RouteAnalysisResult
from app.schemas.scenario import (
    ScenarioBaselineLayerRead,
    ScenarioBaselineLayerResult,
    ScenarioComparisonRead,
    ScenarioComparisonRequest,
    ScenarioDerivedAnalysesRead,
    ScenarioEffectiveLayerRead,
    ScenarioExposureRunRequest,
    ScenarioFloodRunRequest,
    ScenarioLandslideRunRequest,
    ScenarioLayerOverrideRead,
    ScenarioLayerOverrideRequest,
    ScenarioLayerOverrideResult,
    ScenarioRead,
    ScenarioRequest,
    ScenarioRiskRunRequest,
    ScenarioRouteRunRequest,
    ScenarioStateRead,
)
from app.services.dataset_gates import SourceNotEligible, ensure_metric_calibrated_if_terrain_x_import
from app.services.digital_twin import derive_layer_category
from app.services.exposure import SUPPORTED_HAZARD_TYPES, run_exposure_analysis
from app.services.hazard_flood import run_flood_scenario
from app.services.hazard_landslide import run_landslide_scenario
from app.services.risk import HAZARD_CLASS_MAX_CODE, run_risk_analysis
from app.services.routing import run_route_analysis
from app.services.scenario import LayerRef, can_mutate_overrides, compute_comparison, resolve_effective_layers
from app.services.storage import dataset_storage_dir
from app.services.validation import VECTOR_EXTENSIONS

router = APIRouter(tags=["scenario-lab"])

DEM_SOURCE_TYPES = {"dem", "dsm"}
HAZARD_ORIGINS = {DatasetOrigin.HAZARD_MODEL.value, DatasetOrigin.EO_ANALYSIS.value}
_ROAD_VECTOR_FORMATS = {ext.lstrip(".") for ext in VECTOR_EXTENSIONS}


# --- shared lookups (mirrors the private helpers already independently
# duplicated across hazards.py/exposure.py/risk.py/routing.py -- see ADR
# 0011 for why this module follows the same precedent rather than
# introducing a new shared abstraction) --------------------------------------


def _get_twin_or_404(db: Session, twin_id: str) -> DigitalTwin:
    twin = db.get(DigitalTwin, twin_id)
    if twin is None:
        raise HTTPException(status_code=404, detail="Digital twin not found")
    return twin


def _get_scenario_or_404(db: Session, scenario_id: str) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


def _require_active_scenario(scenario: Scenario) -> None:
    if scenario.status != ScenarioStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400, detail=f"Scenario status is '{scenario.status}', not 'active'."
        )


def _require_file(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"Required input file is missing on disk: {path}")


def _get_validated_dem(db: Session, project_id: str, dem_dataset_id: str) -> Dataset:
    dem = db.get(Dataset, dem_dataset_id)
    if dem is None or dem.project_id != project_id:
        raise HTTPException(status_code=404, detail="DEM/DSM dataset not found in this project")
    if dem.dataset_type not in DEM_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type '{dem.dataset_type}' is not a DEM/DSM; cannot use as a hazard-model elevation source.",
        )
    if dem.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(status_code=400, detail=f"DEM/DSM status is '{dem.status}', not 'validated'.")
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


def _validate_rainfall_reference(db: Session, project_id: str, rainfall_dataset_id: str | None) -> None:
    if rainfall_dataset_id is None:
        return
    rainfall = db.get(Dataset, rainfall_dataset_id)
    if rainfall is None or rainfall.project_id != project_id:
        raise HTTPException(status_code=400, detail="rainfall_dataset_id does not exist in this project")


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
        raise HTTPException(status_code=400, detail=f"Hazard dataset status is '{dataset.status}', not 'validated'.")
    return dataset


_EXPOSURE_VECTOR_FORMATS = {ext.lstrip(".") for ext in VECTOR_EXTENSIONS}


def _get_exposure_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    from app.services.validation import RASTER_EXTENSIONS

    exposure_raster_formats = {ext.lstrip(".") for ext in RASTER_EXTENSIONS}
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Exposure dataset not found in this project")
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(status_code=400, detail=f"Exposure dataset status is '{dataset.status}', not 'validated'.")
    file_format = (dataset.file_format or "").lower()
    if file_format == "geotiff":
        if dataset.dataset_type != DatasetType.POPULATION.value:
            raise HTTPException(
                status_code=400, detail="Only dataset_type='population' rasters can be used as a raster exposure input."
            )
    elif file_format not in _EXPOSURE_VECTOR_FORMATS and file_format not in exposure_raster_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Exposure dataset file_format '{dataset.file_format}' has no usable geometry.",
        )
    return dataset


def _resolve_population_field(exposure_dataset: Dataset, exposure_path: Path, requested_field: str | None) -> str | None:
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
            raise HTTPException(status_code=400, detail="population_field is not applicable to raster population data.")
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


def _get_exposure_analysis_or_400(db: Session, project_id: str, exposure_analysis_id: str) -> ExposureAnalysis:
    analysis = db.get(ExposureAnalysis, exposure_analysis_id)
    if analysis is None or analysis.project_id != project_id:
        raise HTTPException(status_code=404, detail="Exposure analysis not found in this project")
    if analysis.status != ExposureAnalysisStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail=f"Exposure analysis status is '{analysis.status}', not 'completed'.")
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
        raise HTTPException(status_code=400, detail=f"Road dataset status is '{dataset.status}', not 'validated'.")
    file_format = (dataset.file_format or "").lower()
    if file_format not in _ROAD_VECTOR_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Road dataset file_format '{dataset.file_format}' is not a supported vector format.",
        )
    return dataset


def _get_risk_analysis_or_400(
    db: Session, project_id: str, risk_analysis_id: str, hazard_dataset_id: str, road_dataset_id: str
) -> RiskAnalysis:
    risk_analysis = db.get(RiskAnalysis, risk_analysis_id)
    if risk_analysis is None or risk_analysis.project_id != project_id:
        raise HTTPException(status_code=404, detail="Risk analysis not found in this project")
    if risk_analysis.status != RiskAnalysisStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail=f"Risk analysis status is '{risk_analysis.status}', not 'completed'.")
    if risk_analysis.hazard_dataset_id != hazard_dataset_id:
        raise HTTPException(
            status_code=400,
            detail="Risk analysis hazard dataset must match the hazard dataset used for this route analysis.",
        )
    # Phase 8's unmodified rule (see app/api/routing.py) -- reused verbatim,
    # never re-derived: the vulnerability_weight/consequence_weight
    # assumptions were declared for a specific exposure asset and must
    # never be silently reused for a different one (roads).
    if risk_analysis.exposure_dataset_id != road_dataset_id:
        raise HTTPException(
            status_code=400,
            detail="Risk analysis exposure dataset must match the road dataset for risk-aware routing.",
        )
    return risk_analysis


# --- effective-layer resolution --------------------------------------------------


def _get_effective_layers(db: Session, scenario: Scenario) -> dict[str, LayerRef]:
    baseline_rows = list(
        db.execute(select(ScenarioBaselineLayer).where(ScenarioBaselineLayer.scenario_id == scenario.id)).scalars()
    )
    override_rows = list(
        db.execute(select(ScenarioLayerOverride).where(ScenarioLayerOverride.scenario_id == scenario.id)).scalars()
    )
    baseline = [
        LayerRef(dataset_type=r.dataset_type, category=r.category, dataset_id=r.dataset_id, source="baseline")
        for r in baseline_rows
    ]
    overrides = [
        LayerRef(dataset_type=r.dataset_type, category=r.category, dataset_id=r.override_dataset_id, source="override")
        for r in override_rows
    ]
    return resolve_effective_layers(baseline, overrides)


def _resolve_dataset_id(effective: dict[str, LayerRef], dataset_type: str, explicit_id: str | None) -> str | None:
    if explicit_id is not None:
        return explicit_id
    layer = effective.get(dataset_type)
    return layer.dataset_id if layer is not None else None


def _resolve_hazard_dataset_id(effective: dict[str, LayerRef], explicit_id: str | None) -> str | None:
    if explicit_id is not None:
        return explicit_id
    candidates = [
        layer.dataset_id
        for dataset_type, layer in effective.items()
        if dataset_type in SUPPORTED_HAZARD_TYPES and layer.dataset_id is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _consumed_dataset_types(db: Session, scenario_id: str) -> set[str]:
    """Every dataset_type actually resolved-and-used as an input by some
    run already attached to this scenario -- the freeze boundary for
    `can_mutate_overrides` (see app/services/scenario.py). Derived from
    each run type's own denormalized input-role fields, no extra bookkeeping
    table needed:
    - HazardScenario.input_datasets is a role -> dataset_id map whose role
      names are themselves dataset_type strings ("dem", "slope",
      "flow_direction", "flow_accumulation").
    - ExposureAnalysis/RiskAnalysis carry hazard_dataset_type/
      exposure_dataset_type directly.
    - RouteAnalysis carries hazard_dataset_type directly; its road input is
      always dataset_type "roads" by construction (see _get_road_dataset).
    """
    consumed: set[str] = set()

    hazard_runs = list(
        db.execute(select(HazardScenario).where(HazardScenario.scenario_id == scenario_id)).scalars()
    )
    for run in hazard_runs:
        consumed.update(run.input_datasets.keys())

    exposure_runs = list(
        db.execute(select(ExposureAnalysis).where(ExposureAnalysis.scenario_id == scenario_id)).scalars()
    )
    for run in exposure_runs:
        consumed.add(run.hazard_dataset_type)
        consumed.add(run.exposure_dataset_type)

    risk_runs = list(db.execute(select(RiskAnalysis).where(RiskAnalysis.scenario_id == scenario_id)).scalars())
    for run in risk_runs:
        consumed.add(run.hazard_dataset_type)
        consumed.add(run.exposure_dataset_type)

    route_runs = list(db.execute(select(RouteAnalysis).where(RouteAnalysis.scenario_id == scenario_id)).scalars())
    for run in route_runs:
        consumed.add(run.hazard_dataset_type)
        if run.road_dataset_id is not None:
            consumed.add(DatasetType.ROADS.value)

    return consumed


# --- scenario / baseline / lifecycle ----------------------------------------------


@router.post("/digital-twins/{twin_id}/scenarios", response_model=ScenarioRead, status_code=201)
def create_scenario(twin_id: str, payload: ScenarioRequest, db: Session = Depends(get_db)) -> Scenario:
    twin = _get_twin_or_404(db, twin_id)

    scenario = Scenario(
        project_id=twin.project_id,
        twin_id=twin.id,
        name=payload.name,
        description=payload.description,
        status=ScenarioStatus.ACTIVE.value,
    )
    db.add(scenario)
    db.flush()

    active_layers = list(
        db.execute(
            select(TwinLayer).where(TwinLayer.twin_id == twin.id, TwinLayer.status == TwinLayerStatus.ACTIVE.value)
        ).scalars()
    )
    for layer in active_layers:
        db.add(
            ScenarioBaselineLayer(
                scenario_id=scenario.id,
                twin_layer_id=layer.id,
                dataset_id=layer.dataset_id,
                dataset_type=layer.dataset_type,
                category=layer.category,
            )
        )

    db.commit()
    db.refresh(scenario)
    return scenario


@router.get("/digital-twins/{twin_id}/scenarios", response_model=list[ScenarioRead])
def list_scenarios(twin_id: str, status: str | None = None, db: Session = Depends(get_db)) -> list[Scenario]:
    _get_twin_or_404(db, twin_id)
    stmt = select(Scenario).where(Scenario.twin_id == twin_id)
    if status is None:
        stmt = stmt.where(Scenario.status == ScenarioStatus.ACTIVE.value)
    elif status != "all":
        stmt = stmt.where(Scenario.status == status)
    stmt = stmt.order_by(Scenario.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/scenarios/{scenario_id}", response_model=ScenarioRead)
def get_scenario(scenario_id: str, db: Session = Depends(get_db)) -> Scenario:
    return _get_scenario_or_404(db, scenario_id)


@router.post("/scenarios/{scenario_id}/archive", response_model=ScenarioRead)
def archive_scenario(scenario_id: str, db: Session = Depends(get_db)) -> Scenario:
    scenario = _get_scenario_or_404(db, scenario_id)
    if scenario.status != ScenarioStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400, detail=f"Scenario status is '{scenario.status}', not 'active'; cannot archive."
        )
    scenario.status = ScenarioStatus.ARCHIVED.value
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


def _baseline_layer_result(layer: ScenarioBaselineLayer, dataset: Dataset | None) -> ScenarioBaselineLayerResult:
    return ScenarioBaselineLayerResult(
        layer=ScenarioBaselineLayerRead.model_validate(layer),
        dataset=DatasetRead.model_validate(dataset) if dataset is not None else None,
    )


@router.get("/scenarios/{scenario_id}/baseline-layers", response_model=list[ScenarioBaselineLayerResult])
def list_baseline_layers(scenario_id: str, db: Session = Depends(get_db)) -> list[ScenarioBaselineLayerResult]:
    _get_scenario_or_404(db, scenario_id)
    stmt = (
        select(ScenarioBaselineLayer)
        .where(ScenarioBaselineLayer.scenario_id == scenario_id)
        .order_by(ScenarioBaselineLayer.created_at)
    )
    layers = list(db.execute(stmt).scalars())
    return [
        _baseline_layer_result(layer, db.get(Dataset, layer.dataset_id) if layer.dataset_id is not None else None)
        for layer in layers
    ]


# --- layer overrides ------------------------------------------------------------


def _override_result(override: ScenarioLayerOverride, dataset: Dataset | None) -> ScenarioLayerOverrideResult:
    return ScenarioLayerOverrideResult(
        override=ScenarioLayerOverrideRead.model_validate(override),
        dataset=DatasetRead.model_validate(dataset) if dataset is not None else None,
    )


@router.post(
    "/scenarios/{scenario_id}/layer-overrides", response_model=ScenarioLayerOverrideResult, status_code=201
)
def add_layer_override(
    scenario_id: str, payload: ScenarioLayerOverrideRequest, db: Session = Depends(get_db)
) -> ScenarioLayerOverrideResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    dataset = db.get(Dataset, payload.dataset_id)
    if dataset is None or dataset.project_id != scenario.project_id:
        raise HTTPException(status_code=404, detail="Dataset not found in this project")
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(status_code=400, detail=f"Dataset status is '{dataset.status}', not 'validated'.")

    try:
        category = derive_layer_category(dataset.origin, dataset.dataset_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not can_mutate_overrides(dataset.dataset_type, _consumed_dataset_types(db, scenario_id)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"dataset_type '{dataset.dataset_type}' has already been consumed by a run on this scenario; "
                "its override is frozen."
            ),
        )

    existing = db.execute(
        select(ScenarioLayerOverride).where(
            ScenarioLayerOverride.scenario_id == scenario_id,
            ScenarioLayerOverride.dataset_type == dataset.dataset_type,
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"An override for dataset_type '{dataset.dataset_type}' already exists on this scenario "
                f"(override id {existing.id}); delete it first."
            ),
        )

    override = ScenarioLayerOverride(
        scenario_id=scenario_id,
        dataset_type=dataset.dataset_type,
        category=category,
        override_dataset_id=dataset.id,
        reason=payload.reason,
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    db.refresh(dataset)
    return _override_result(override, dataset)


@router.get("/scenarios/{scenario_id}/layer-overrides", response_model=list[ScenarioLayerOverrideResult])
def list_layer_overrides(scenario_id: str, db: Session = Depends(get_db)) -> list[ScenarioLayerOverrideResult]:
    _get_scenario_or_404(db, scenario_id)
    stmt = (
        select(ScenarioLayerOverride)
        .where(ScenarioLayerOverride.scenario_id == scenario_id)
        .order_by(ScenarioLayerOverride.created_at)
    )
    overrides = list(db.execute(stmt).scalars())
    return [
        _override_result(o, db.get(Dataset, o.override_dataset_id) if o.override_dataset_id is not None else None)
        for o in overrides
    ]


@router.delete("/scenario-layer-overrides/{override_id}", status_code=204)
def delete_layer_override(override_id: str, db: Session = Depends(get_db)) -> None:
    override = db.get(ScenarioLayerOverride, override_id)
    if override is None:
        raise HTTPException(status_code=404, detail="Scenario layer override not found")
    if not can_mutate_overrides(override.dataset_type, _consumed_dataset_types(db, override.scenario_id)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"dataset_type '{override.dataset_type}' has already been consumed by a run on this scenario; "
                "its override is frozen."
            ),
        )
    db.delete(override)
    db.commit()


# --- composed state ---------------------------------------------------------------


@router.get("/scenarios/{scenario_id}/state", response_model=ScenarioStateRead)
def get_scenario_state(scenario_id: str, db: Session = Depends(get_db)) -> ScenarioStateRead:
    scenario = _get_scenario_or_404(db, scenario_id)

    baseline_rows = list(
        db.execute(
            select(ScenarioBaselineLayer)
            .where(ScenarioBaselineLayer.scenario_id == scenario_id)
            .order_by(ScenarioBaselineLayer.created_at)
        ).scalars()
    )
    override_rows = list(
        db.execute(
            select(ScenarioLayerOverride)
            .where(ScenarioLayerOverride.scenario_id == scenario_id)
            .order_by(ScenarioLayerOverride.created_at)
        ).scalars()
    )

    baseline_results = [
        _baseline_layer_result(row, db.get(Dataset, row.dataset_id) if row.dataset_id is not None else None)
        for row in baseline_rows
    ]
    override_results = [
        _override_result(row, db.get(Dataset, row.override_dataset_id) if row.override_dataset_id is not None else None)
        for row in override_rows
    ]

    consumed = _consumed_dataset_types(db, scenario_id)
    effective = _get_effective_layers(db, scenario)
    effective_results = []
    for dataset_type in sorted(effective):
        layer_ref = effective[dataset_type]
        dataset = db.get(Dataset, layer_ref.dataset_id) if layer_ref.dataset_id is not None else None
        effective_results.append(
            ScenarioEffectiveLayerRead(
                dataset_type=dataset_type,
                category=layer_ref.category,
                source=layer_ref.source,
                dataset=DatasetRead.model_validate(dataset) if dataset is not None else None,
                can_override=can_mutate_overrides(dataset_type, consumed),
            )
        )

    hazard_runs = list(
        db.execute(
            select(HazardScenario).where(HazardScenario.scenario_id == scenario_id).order_by(HazardScenario.created_at.desc())
        ).scalars()
    )
    exposure_runs = list(
        db.execute(
            select(ExposureAnalysis)
            .where(ExposureAnalysis.scenario_id == scenario_id)
            .order_by(ExposureAnalysis.created_at.desc())
        ).scalars()
    )
    risk_runs = list(
        db.execute(
            select(RiskAnalysis).where(RiskAnalysis.scenario_id == scenario_id).order_by(RiskAnalysis.created_at.desc())
        ).scalars()
    )
    route_runs = list(
        db.execute(
            select(RouteAnalysis).where(RouteAnalysis.scenario_id == scenario_id).order_by(RouteAnalysis.created_at.desc())
        ).scalars()
    )
    return ScenarioStateRead(
        scenario=ScenarioRead.model_validate(scenario),
        baseline_layers=baseline_results,
        layer_overrides=override_results,
        effective_layers=effective_results,
        derived_analyses=ScenarioDerivedAnalysesRead(
            hazard_scenarios=[HazardScenarioRead.model_validate(r) for r in hazard_runs],
            exposure_analyses=[ExposureAnalysisRead.model_validate(r) for r in exposure_runs],
            risk_analyses=[RiskAnalysisRead.model_validate(r) for r in risk_runs],
            route_analyses=[RouteAnalysisRead.model_validate(r) for r in route_runs],
        ),
    )


# --- scenario-scoped hazard runs --------------------------------------------------


def _run_scenario_hazard(
    db: Session,
    *,
    scenario: Scenario,
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
    scenario_record = HazardScenario(
        project_id=scenario.project_id,
        hazard_type=hazard_type,
        name=name,
        description=description,
        input_datasets=input_datasets,
        parameters=parameters,
        rainfall_dataset_id=rainfall_dataset_id,
        scenario_id=scenario.id,
        status=HazardScenarioStatus.COMPLETED.value,
    )
    db.add(scenario_record)
    db.flush()

    output_dataset = Dataset(
        project_id=scenario.project_id,
        name=f"{name} — {output_dataset_type}",
        dataset_type=output_dataset_type,
        origin=DatasetOrigin.HAZARD_MODEL.value,
        source_dataset_id=source_dataset_id,
        hazard_scenario_id=scenario_record.id,
        source_filename=output_filename,
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    db.add(output_dataset)
    db.flush()

    output_path = dataset_storage_dir(scenario.project_id, output_dataset.id) / output_filename

    try:
        result = compute(output_path)
    except Exception as exc:
        scenario_record.status = HazardScenarioStatus.FAILED.value
        scenario_record.error_message = str(exc)
        output_dataset.status = DatasetStatus.INVALID.value
        output_dataset.validation_message = f"Hazard scenario computation failed: {exc}"
        db.commit()
        db.refresh(scenario_record)
        db.refresh(output_dataset)
        return HazardScenarioResult(
            scenario=HazardScenarioRead.model_validate(scenario_record),
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
        "hazard_scenario_id": scenario_record.id,
        "scenario_id": scenario.id,
        "hazard_type": hazard_type,
        "method": result.metadata.get("method"),
        "input_datasets": input_datasets,
        "parameters": parameters,
        "rainfall_dataset_id": rainfall_dataset_id,
        "limitations": result.metadata.get("limitations", []),
    }

    db.commit()
    db.refresh(scenario_record)
    db.refresh(output_dataset)

    return HazardScenarioResult(
        scenario=HazardScenarioRead.model_validate(scenario_record),
        datasets=[DatasetRead.model_validate(output_dataset)],
    )


@router.post("/scenarios/{scenario_id}/hazard-runs/flood", response_model=HazardScenarioResult, status_code=201)
def run_scenario_flood(
    scenario_id: str, payload: ScenarioFloodRunRequest, db: Session = Depends(get_db)
) -> HazardScenarioResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    effective = _get_effective_layers(db, scenario)
    dem_id = _resolve_dataset_id(effective, "dem", payload.dem_dataset_id)
    if dem_id is None:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve dem_dataset_id: not provided and no 'dem' layer in this scenario's effective baseline/overrides.",
        )

    dem = _get_validated_dem(db, scenario.project_id, dem_id)
    flow_direction = _get_derivative_or_400(db, dem.id, "flow_direction")
    flow_accumulation = _get_derivative_or_400(db, dem.id, "flow_accumulation")
    _validate_rainfall_reference(db, scenario.project_id, payload.rainfall_dataset_id)

    dem_path = Path(dem.storage_path)
    direction_path = Path(flow_direction.storage_path)
    accumulation_path = Path(flow_accumulation.storage_path)
    for path in (dem_path, direction_path, accumulation_path):
        _require_file(path)

    return _run_scenario_hazard(
        db,
        scenario=scenario,
        hazard_type=HazardType.FLOOD.value,
        name=payload.name,
        description=payload.description,
        input_datasets={"dem": dem.id, "flow_direction": flow_direction.id, "flow_accumulation": flow_accumulation.id},
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


@router.post("/scenarios/{scenario_id}/hazard-runs/landslide", response_model=HazardScenarioResult, status_code=201)
def run_scenario_landslide(
    scenario_id: str, payload: ScenarioLandslideRunRequest, db: Session = Depends(get_db)
) -> HazardScenarioResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    effective = _get_effective_layers(db, scenario)
    dem_id = _resolve_dataset_id(effective, "dem", payload.dem_dataset_id)
    if dem_id is None:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve dem_dataset_id: not provided and no 'dem' layer in this scenario's effective baseline/overrides.",
        )

    dem = _get_validated_dem(db, scenario.project_id, dem_id)
    slope = _get_derivative_or_400(db, dem.id, "slope")
    _validate_rainfall_reference(db, scenario.project_id, payload.rainfall_dataset_id)

    slope_path = Path(slope.storage_path)
    _require_file(slope_path)

    return _run_scenario_hazard(
        db,
        scenario=scenario,
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


# --- scenario-scoped exposure run --------------------------------------------------


@router.post("/scenarios/{scenario_id}/exposure-runs", response_model=ExposureAnalysisResult, status_code=201)
def run_scenario_exposure(
    scenario_id: str, payload: ScenarioExposureRunRequest, db: Session = Depends(get_db)
) -> ExposureAnalysisResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    effective = _get_effective_layers(db, scenario)
    hazard_id = _resolve_hazard_dataset_id(effective, payload.hazard_dataset_id)
    if hazard_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not resolve hazard_dataset_id: not provided, and this scenario's effective layers have "
                "none or multiple hazard-type candidates."
            ),
        )

    hazard = _get_hazard_dataset(db, scenario.project_id, hazard_id)
    exposure = _get_exposure_dataset(db, scenario.project_id, payload.exposure_dataset_id)

    hazard_path = Path(hazard.storage_path)
    exposure_path = Path(exposure.storage_path)
    _require_file(hazard_path)
    _require_file(exposure_path)

    population_field = _resolve_population_field(exposure, exposure_path, payload.population_field)

    class_legend_override = None
    if hazard.dataset_type == "landslide_susceptibility":
        class_legend_override = hazard.metadata_json.get("class_legend")

    analysis = ExposureAnalysis(
        project_id=scenario.project_id,
        name=payload.name,
        description=payload.description,
        hazard_dataset_id=hazard.id,
        exposure_dataset_id=exposure.id,
        hazard_dataset_type=hazard.dataset_type,
        exposure_dataset_type=exposure.dataset_type,
        population_field=population_field,
        scenario_id=scenario.id,
        parameters={"population_field": population_field},
        results={},
        status=ExposureAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="scenario-exposure-analysis-") as tmp:
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
                project_id=scenario.project_id,
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

            final_path = dataset_storage_dir(scenario.project_id, feature_dataset.id) / "exposure_features.geojson"
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
                "scenario_id": scenario.id,
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


# --- scenario-scoped risk run -----------------------------------------------------


@router.post("/scenarios/{scenario_id}/risk-runs", response_model=RiskAnalysisResult, status_code=201)
def run_scenario_risk(
    scenario_id: str, payload: ScenarioRiskRunRequest, db: Session = Depends(get_db)
) -> RiskAnalysisResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    exposure_analysis = _get_exposure_analysis_or_400(db, scenario.project_id, payload.exposure_analysis_id)
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
        project_id=scenario.project_id,
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
        scenario_id=scenario.id,
        parameters={
            "vulnerability_weight": payload.vulnerability_weight,
            "consequence_weight": payload.consequence_weight,
            "risk_breakpoints": list(risk_breakpoints),
        },
        results={},
        status=RiskAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="scenario-risk-analysis-") as tmp:
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
                project_id=scenario.project_id,
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

            final_path = dataset_storage_dir(scenario.project_id, risk_dataset.id) / "risk_classification.geojson"
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
                "scenario_id": scenario.id,
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


# --- scenario-scoped route run ----------------------------------------------------


@router.post("/scenarios/{scenario_id}/route-runs", response_model=RouteAnalysisResult, status_code=201)
def run_scenario_route(
    scenario_id: str, payload: ScenarioRouteRunRequest, db: Session = Depends(get_db)
) -> RouteAnalysisResult:
    scenario = _get_scenario_or_404(db, scenario_id)
    _require_active_scenario(scenario)

    effective = _get_effective_layers(db, scenario)
    road_id = _resolve_dataset_id(effective, DatasetType.ROADS.value, payload.road_dataset_id)
    if road_id is None:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve road_dataset_id: not provided and no 'roads' layer in this scenario's effective baseline/overrides.",
        )
    hazard_id = _resolve_hazard_dataset_id(effective, payload.hazard_dataset_id)
    if hazard_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not resolve hazard_dataset_id: not provided, and this scenario's effective layers have "
                "none or multiple hazard-type candidates."
            ),
        )

    road = _get_road_dataset(db, scenario.project_id, road_id)
    hazard = _get_hazard_dataset(db, scenario.project_id, hazard_id)

    risk_analysis: RiskAnalysis | None = None
    if payload.risk_analysis_id is not None:
        risk_analysis = _get_risk_analysis_or_400(db, scenario.project_id, payload.risk_analysis_id, hazard.id, road.id)

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
        "blocked_segment_geometries": payload.blocked_segment_geometries,
        "match_buffer_m": payload.match_buffer_m,
    }

    analysis = RouteAnalysis(
        project_id=scenario.project_id,
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
        scenario_id=scenario.id,
        parameters=parameters,
        results={},
        status=RouteAnalysisStatus.COMPLETED.value,
    )
    db.add(analysis)
    db.flush()

    output_datasets: list[Dataset] = []

    with tempfile.TemporaryDirectory(prefix="scenario-route-analysis-") as tmp:
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
                blocked_segment_geometries=payload.blocked_segment_geometries,
                match_buffer_m=payload.match_buffer_m,
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
            "scenario_id": scenario.id,
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
                project_id=scenario.project_id,
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

            final_path = dataset_storage_dir(scenario.project_id, output_dataset.id) / filename
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
            output_dataset.provenance = {**base_provenance, "route_type": route_type, **computation.results}
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


# --- scenario-scoped listings -----------------------------------------------------


@router.get("/scenarios/{scenario_id}/hazard-scenarios", response_model=list[HazardScenarioRead])
def list_scenario_hazard_runs(scenario_id: str, db: Session = Depends(get_db)) -> list[HazardScenario]:
    _get_scenario_or_404(db, scenario_id)
    stmt = (
        select(HazardScenario)
        .where(HazardScenario.scenario_id == scenario_id)
        .order_by(HazardScenario.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


@router.get("/scenarios/{scenario_id}/exposure-analyses", response_model=list[ExposureAnalysisRead])
def list_scenario_exposure_runs(scenario_id: str, db: Session = Depends(get_db)) -> list[ExposureAnalysis]:
    _get_scenario_or_404(db, scenario_id)
    stmt = (
        select(ExposureAnalysis)
        .where(ExposureAnalysis.scenario_id == scenario_id)
        .order_by(ExposureAnalysis.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


@router.get("/scenarios/{scenario_id}/risk-analyses", response_model=list[RiskAnalysisRead])
def list_scenario_risk_runs(scenario_id: str, db: Session = Depends(get_db)) -> list[RiskAnalysis]:
    _get_scenario_or_404(db, scenario_id)
    stmt = select(RiskAnalysis).where(RiskAnalysis.scenario_id == scenario_id).order_by(RiskAnalysis.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/scenarios/{scenario_id}/route-analyses", response_model=list[RouteAnalysisRead])
def list_scenario_route_runs(scenario_id: str, db: Session = Depends(get_db)) -> list[RouteAnalysis]:
    _get_scenario_or_404(db, scenario_id)
    stmt = (
        select(RouteAnalysis).where(RouteAnalysis.scenario_id == scenario_id).order_by(RouteAnalysis.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


# --- comparison -------------------------------------------------------------------


_ANALYSIS_MODEL_BY_TYPE: dict[str, Any] = {
    "exposure": ExposureAnalysis,
    "risk": RiskAnalysis,
    "route": RouteAnalysis,
}
_COMPLETED_STATUS_BY_TYPE = {
    "exposure": ExposureAnalysisStatus.COMPLETED.value,
    "risk": RiskAnalysisStatus.COMPLETED.value,
    "route": RouteAnalysisStatus.COMPLETED.value,
}


def _get_analysis_for_comparison(db: Session, analysis_type: str, analysis_id: str) -> Any:
    model = _ANALYSIS_MODEL_BY_TYPE.get(analysis_type)
    if model is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported analysis_type '{analysis_type}'. Supported: {sorted(_ANALYSIS_MODEL_BY_TYPE)}.",
        )
    analysis = db.get(model, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"{analysis_type} analysis not found: {analysis_id}")
    if analysis.status != _COMPLETED_STATUS_BY_TYPE[analysis_type]:
        raise HTTPException(
            status_code=400, detail=f"{analysis_type} analysis status is '{analysis.status}', not 'completed'."
        )
    return analysis


@router.post("/scenario-comparisons", response_model=ScenarioComparisonRead, status_code=201)
def create_scenario_comparison(payload: ScenarioComparisonRequest, db: Session = Depends(get_db)) -> ScenarioComparisonRead:
    left = _get_analysis_for_comparison(db, payload.analysis_type, payload.left_analysis_id)
    right = _get_analysis_for_comparison(db, payload.analysis_type, payload.right_analysis_id)

    try:
        result = compute_comparison(
            payload.analysis_type,
            left.results,
            right.results,
            left.hazard_dataset_type,
            right.hazard_dataset_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ScenarioComparisonRead(
        analysis_type=payload.analysis_type,
        left_analysis_id=left.id,
        right_analysis_id=right.id,
        diff=result.diff,
        comparability_warnings=result.comparability_warnings,
    )
