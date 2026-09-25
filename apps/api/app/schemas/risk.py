from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.dataset import DatasetRead


class RiskAnalysisRequest(BaseModel):
    name: str
    description: str | None = None
    exposure_analysis_id: str
    # Explicit, required assumptions -- no silent default. See
    # app/services/risk.py module docstring for why these are never
    # inferred or defaulted quietly.
    vulnerability_weight: float = Field(ge=0, le=1)
    consequence_weight: float = Field(ge=0, le=1)
    risk_breakpoints: list[float] = Field(default_factory=lambda: [0.2, 0.4, 0.6, 0.8])

    @field_validator("risk_breakpoints")
    @classmethod
    def _validate_breakpoints(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("risk_breakpoints must contain exactly 4 values")
        if list(v) != sorted(v) or len(set(v)) != 4:
            raise ValueError("risk_breakpoints must be strictly ascending")
        if not all(0.0 < x < 1.0 for x in v):
            raise ValueError("risk_breakpoints values must be strictly between 0 and 1")
        return v


class RiskAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    exposure_analysis_id: str | None
    hazard_dataset_id: str | None
    exposure_dataset_id: str | None
    hazard_dataset_type: str
    exposure_dataset_type: str
    vulnerability_weight: float
    consequence_weight: float
    risk_breakpoints: list[float]
    scenario_id: str | None
    parameters: dict[str, Any]
    results: dict[str, Any]
    status: str
    error_message: str | None
    created_at: datetime


class RiskAnalysisResult(BaseModel):
    analysis: RiskAnalysisRead
    datasets: list[DatasetRead]
