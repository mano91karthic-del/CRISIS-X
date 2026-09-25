from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.dataset import DatasetRead


class RoutePoint(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class RouteAnalysisRequest(BaseModel):
    name: str
    description: str | None = None
    road_dataset_id: str
    hazard_dataset_id: str
    # Optional: reuses a completed Phase 7 RiskAnalysis's declared
    # vulnerability_weight/consequence_weight for risk-aware costing
    # instead of raw hazard intensity alone. Must reference the same
    # hazard_dataset_id, and its exposure_dataset_id must be this exact
    # road_dataset_id -- checked in the endpoint (depends on looking both
    # datasets up first) -- the vulnerability/consequence assumptions were
    # declared for a specific asset type and must never be silently reused
    # for a different one.
    risk_analysis_id: str | None = None

    origin: RoutePoint
    destination: RoutePoint

    # Explicit, required assumption -- no silent default (same rule Phase 7
    # applies to vulnerability_weight/consequence_weight): "how strongly
    # should the hazard-aware route avoid modeled hazard vs. go the short
    # way."
    hazard_penalty_weight: float = Field(ge=0)
    # Generic classification boundary -- has a defensible default, still
    # overridable (like Phase 4/7's breakpoints).
    block_threshold: float = Field(default=1.0, gt=0, le=1)
    node_snap_tolerance_m: float = Field(default=1.0, gt=0)
    max_snap_distance_m: float = Field(default=100.0, gt=0)


class RouteAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    road_dataset_id: str | None
    hazard_dataset_id: str | None
    hazard_dataset_type: str
    risk_analysis_id: str | None
    origin_lon: float
    origin_lat: float
    destination_lon: float
    destination_lat: float
    hazard_penalty_weight: float
    block_threshold: float
    node_snap_tolerance_m: float
    max_snap_distance_m: float
    scenario_id: str | None
    parameters: dict[str, Any]
    results: dict[str, Any]
    status: str
    error_message: str | None
    created_at: datetime


class RouteAnalysisResult(BaseModel):
    analysis: RouteAnalysisRead
    datasets: list[DatasetRead]
