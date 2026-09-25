from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.dataset import DatasetRead
from app.schemas.exposure import ExposureAnalysisRead
from app.schemas.hazard import HazardScenarioRead
from app.schemas.risk import RiskAnalysisRead
from app.schemas.routing import RoutePoint, RouteAnalysisRead


def _validate_ascending_quadruple(v: list[float], field_name: str) -> list[float]:
    if len(v) != 4:
        raise ValueError(f"{field_name} must contain exactly 4 values")
    if list(v) != sorted(v) or len(set(v)) != 4:
        raise ValueError(f"{field_name} must be strictly ascending")
    return v


# --- scenario / baseline / overrides -----------------------------------------------


class ScenarioRequest(BaseModel):
    name: str
    description: str | None = None


class ScenarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    twin_id: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ScenarioBaselineLayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str
    twin_layer_id: str | None
    dataset_id: str | None
    dataset_type: str
    category: str
    created_at: datetime


class ScenarioBaselineLayerResult(BaseModel):
    layer: ScenarioBaselineLayerRead
    dataset: DatasetRead | None


class ScenarioLayerOverrideRequest(BaseModel):
    dataset_id: str
    reason: str | None = None


class ScenarioLayerOverrideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str
    dataset_type: str
    category: str
    override_dataset_id: str | None
    reason: str | None
    created_at: datetime


class ScenarioLayerOverrideResult(BaseModel):
    override: ScenarioLayerOverrideRead
    dataset: DatasetRead | None


class ScenarioEffectiveLayerRead(BaseModel):
    dataset_type: str
    category: str
    source: str  # "baseline" | "override"
    dataset: DatasetRead | None
    # False once some run on this scenario has already resolved-and-
    # consumed this exact dataset_type -- see
    # app/services/scenario.py::can_mutate_overrides.
    can_override: bool


class ScenarioDerivedAnalysesRead(BaseModel):
    hazard_scenarios: list[HazardScenarioRead]
    exposure_analyses: list[ExposureAnalysisRead]
    risk_analyses: list[RiskAnalysisRead]
    route_analyses: list[RouteAnalysisRead]


class ScenarioStateRead(BaseModel):
    scenario: ScenarioRead
    baseline_layers: list[ScenarioBaselineLayerResult]
    layer_overrides: list[ScenarioLayerOverrideResult]
    effective_layers: list[ScenarioEffectiveLayerRead]
    derived_analyses: ScenarioDerivedAnalysesRead


# --- scenario-scoped run requests ---------------------------------------------------
#
# Each mirrors the corresponding Phase 4/6/7/8 request schema exactly, with
# dataset-id fields made optional where they're resolvable from the
# scenario's effective layers (override > baseline); see
# app/api/scenarios.py's resolution helpers. No new parameters are
# introduced beyond what the underlying run_* service functions already
# accept -- see docs/architecture/0011-phase-10-scenario-lab.md.


class ScenarioFloodRunRequest(BaseModel):
    name: str
    description: str | None = None
    dem_dataset_id: str | None = None
    depth_above_drainage_m: float = Field(gt=0)
    channel_threshold_cells: float = Field(default=50.0, gt=0)
    rainfall_dataset_id: str | None = None


class ScenarioLandslideRunRequest(BaseModel):
    name: str
    description: str | None = None
    dem_dataset_id: str | None = None
    slope_breakpoints_deg: list[float] = Field(default_factory=lambda: [5.0, 15.0, 25.0, 35.0])
    rainfall_context: str | None = None
    rainfall_dataset_id: str | None = None

    @field_validator("slope_breakpoints_deg")
    @classmethod
    def _validate_breakpoints(cls, v: list[float]) -> list[float]:
        return _validate_ascending_quadruple(v, "slope_breakpoints_deg")


class ScenarioExposureRunRequest(BaseModel):
    name: str
    description: str | None = None
    # Resolved from the scenario's effective layers if omitted (there must
    # be exactly one hazard-type dataset_type present, else 400 -- see
    # app/api/scenarios.py::_resolve_hazard_dataset_id).
    hazard_dataset_id: str | None = None
    # Never auto-resolved: the exposure "asset" dataset_type space (roads,
    # population, buildings, ...) is open-ended, so guessing would be
    # exactly the kind of silent misattribution this codebase avoids.
    exposure_dataset_id: str
    population_field: str | None = None


class ScenarioRiskRunRequest(BaseModel):
    name: str
    description: str | None = None
    # Always explicit -- an ExposureAnalysis is a run record, not a Dataset
    # layer, so it is never resolved through baseline/override layers.
    exposure_analysis_id: str
    vulnerability_weight: float = Field(ge=0, le=1)
    consequence_weight: float = Field(ge=0, le=1)
    risk_breakpoints: list[float] = Field(default_factory=lambda: [0.2, 0.4, 0.6, 0.8])

    @field_validator("risk_breakpoints")
    @classmethod
    def _validate_breakpoints(cls, v: list[float]) -> list[float]:
        v = _validate_ascending_quadruple(v, "risk_breakpoints")
        if not all(0.0 < x < 1.0 for x in v):
            raise ValueError("risk_breakpoints values must be strictly between 0 and 1")
        return v


class ScenarioRouteRunRequest(BaseModel):
    name: str
    description: str | None = None
    road_dataset_id: str | None = None
    hazard_dataset_id: str | None = None
    risk_analysis_id: str | None = None
    origin: RoutePoint
    destination: RoutePoint
    hazard_penalty_weight: float = Field(ge=0)
    block_threshold: float = Field(default=1.0, gt=0, le=1)
    node_snap_tolerance_m: float = Field(default=1.0, gt=0)
    max_snap_distance_m: float = Field(default=100.0, gt=0)
    # Phase 10 addition (see app/services/routing.py::run_route_analysis) --
    # GeoJSON-like LineString/MultiLineString geometries in WGS84.
    blocked_segment_geometries: list[dict[str, Any]] | None = None
    match_buffer_m: float = Field(default=5.0, gt=0)


# --- comparison -----------------------------------------------------------------


class ScenarioComparisonRequest(BaseModel):
    analysis_type: str  # "exposure" | "risk" | "route"
    left_analysis_id: str
    right_analysis_id: str


class ScenarioComparisonRead(BaseModel):
    analysis_type: str
    left_analysis_id: str
    right_analysis_id: str
    diff: dict[str, Any]
    comparability_warnings: list[str]
