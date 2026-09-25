from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.dataset import DatasetRead


class ExposureAnalysisRequest(BaseModel):
    name: str
    description: str | None = None
    hazard_dataset_id: str
    exposure_dataset_id: str
    # Required only when exposure_dataset_id refers to vector population
    # data (a column holding the population count) -- never guessed at.
    # Checked in the endpoint, not here, since it depends on looking up the
    # exposure dataset's type/format first.
    population_field: str | None = None


class ExposureAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    hazard_dataset_id: str | None
    exposure_dataset_id: str | None
    hazard_dataset_type: str
    exposure_dataset_type: str
    population_field: str | None
    scenario_id: str | None
    parameters: dict[str, Any]
    results: dict[str, Any]
    status: str
    error_message: str | None
    created_at: datetime


class ExposureAnalysisResult(BaseModel):
    analysis: ExposureAnalysisRead
    datasets: list[DatasetRead]
