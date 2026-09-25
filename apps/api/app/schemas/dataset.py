from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.dataset import DatasetStatus


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    # Plain str, not the DatasetType enum: this field also holds derived
    # product names ("slope", "aspect") that DatasetType intentionally
    # excludes — see app/models/dataset.py.
    dataset_type: str
    source_dataset_id: str | None
    origin: str
    terrain_x_package_id: str | None
    hazard_scenario_id: str | None
    eo_change_analysis_id: str | None
    exposure_analysis_id: str | None
    risk_analysis_id: str | None
    route_analysis_id: str | None
    acquisition_date: datetime | None
    source_filename: str
    file_format: str | None
    crs: str | None
    bbox_min_x: float | None
    bbox_min_y: float | None
    bbox_max_x: float | None
    bbox_max_y: float | None
    file_size_bytes: int
    checksum_sha256: str
    status: DatasetStatus
    validation_message: str | None
    metadata_json: dict[str, Any]
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime
