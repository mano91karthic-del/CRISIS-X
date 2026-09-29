from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.dataset import DatasetRead


class DigitalTwinRequest(BaseModel):
    name: str
    description: str | None = None


class DigitalTwinRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    version: int
    study_area_dataset_id: str | None
    created_at: datetime
    updated_at: datetime


class StudyAreaRequest(BaseModel):
    """Sets (or clears, with dataset_id=None) the twin's canonical study
    area -- see ADR 0013. The referenced dataset must be a validated
    STUDY_AREA-type dataset in the same project.
    """

    dataset_id: str | None


class TwinLayerRequest(BaseModel):
    dataset_id: str


class TwinLayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    twin_id: str
    dataset_id: str | None
    dataset_type: str
    category: str
    status: str
    registered_at_version: int
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TwinLayerResult(BaseModel):
    layer: TwinLayerRead
    dataset: DatasetRead | None


class TwinExtentRead(BaseModel):
    reference_crs: str | None
    bbox_min_x: float | None
    bbox_min_y: float | None
    bbox_max_x: float | None
    bbox_max_y: float | None
    crs_mismatch_layer_ids: list[str]
    layers_without_extent: list[str]


class TwinAcquisitionDateRangeRead(BaseModel):
    earliest: datetime | None
    latest: datetime | None
    layers_without_acquisition_date: list[str]


class DigitalTwinStateRead(BaseModel):
    twin: DigitalTwinRead
    extent: TwinExtentRead
    acquisition_date_range: TwinAcquisitionDateRangeRead
    layers_by_category: dict[str, list[TwinLayerResult]]
    missing_recommended_layers: list[str]
    limitations: list[str]
