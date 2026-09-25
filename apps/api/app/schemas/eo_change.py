from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.dataset import DatasetRead

EOChangeMethodLiteral = Literal["change_vector_analysis", "normalized_difference"]
ThresholdMethodLiteral = Literal["manual", "otsu"]


class EOChangeAnalysisRequest(BaseModel):
    name: str
    description: str | None = None
    before_dataset_id: str
    after_dataset_id: str

    method: EOChangeMethodLiteral
    # change_vector_analysis only. Default (when omitted): all bands common
    # to both images, 1..min(before.band_count, after.band_count) --
    # explicitly recorded in the analysis's `parameters`, never a silent
    # assumption.
    band_indices: list[int] | None = None

    # normalized_difference only. Required together; band roles are never
    # inferred, always the caller's explicit declaration.
    band_a_index: int | None = None
    band_b_index: int | None = None
    # Purely descriptive (e.g. "NDVI", "NDWI") -- never changes the math.
    index_name: str | None = None

    threshold_method: ThresholdMethodLiteral
    # Required iff threshold_method == "manual"; must be omitted for "otsu"
    # (computed automatically there) -- no silent precedence between the two.
    change_threshold: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_method_and_threshold_params(self) -> "EOChangeAnalysisRequest":
        if self.method == "normalized_difference":
            if self.band_a_index is None or self.band_b_index is None:
                raise ValueError(
                    "band_a_index and band_b_index are required for method='normalized_difference'"
                )
        if self.method == "change_vector_analysis":
            if self.band_a_index is not None or self.band_b_index is not None:
                raise ValueError(
                    "band_a_index/band_b_index are only used with method='normalized_difference'"
                )
        if self.threshold_method == "manual" and self.change_threshold is None:
            raise ValueError("change_threshold is required when threshold_method='manual'")
        if self.threshold_method == "otsu" and self.change_threshold is not None:
            raise ValueError("change_threshold must not be supplied when threshold_method='otsu'")
        return self


class EOChangeAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    before_dataset_id: str | None
    after_dataset_id: str | None
    source_format: str
    method: str
    threshold_method: str
    parameters: dict[str, Any]
    status: str
    error_message: str | None
    created_at: datetime


class EOChangeAnalysisResult(BaseModel):
    analysis: EOChangeAnalysisRead
    datasets: list[DatasetRead]
