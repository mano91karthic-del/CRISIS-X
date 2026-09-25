from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.dataset import DatasetRead


class FloodScenarioRequest(BaseModel):
    name: str
    description: str | None = None
    dem_dataset_id: str
    # A direct, user-supplied scenario assumption -- never derived from
    # rainfall or any rainfall-runoff model. See ADR 0005.
    depth_above_drainage_m: float = Field(gt=0)
    channel_threshold_cells: float = Field(default=50.0, gt=0)
    rainfall_dataset_id: str | None = None


class LandslideScenarioRequest(BaseModel):
    name: str
    description: str | None = None
    dem_dataset_id: str
    slope_breakpoints_deg: list[float] = Field(default_factory=lambda: [5.0, 15.0, 25.0, 35.0])
    # Descriptive only -- recorded in provenance, has no effect on the
    # computed susceptibility class. See ADR 0005.
    rainfall_context: str | None = None
    rainfall_dataset_id: str | None = None

    @field_validator("slope_breakpoints_deg")
    @classmethod
    def _validate_breakpoints(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("slope_breakpoints_deg must contain exactly 4 values")
        if list(v) != sorted(v) or len(set(v)) != 4:
            raise ValueError("slope_breakpoints_deg must be strictly ascending")
        return v


class HazardScenarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    hazard_type: str
    name: str
    description: str | None
    input_datasets: dict[str, Any]
    parameters: dict[str, Any]
    rainfall_dataset_id: str | None
    scenario_id: str | None
    status: str
    error_message: str | None
    created_at: datetime


class HazardScenarioResult(BaseModel):
    scenario: HazardScenarioRead
    datasets: list[DatasetRead]
