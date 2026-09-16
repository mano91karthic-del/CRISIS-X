from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.dataset import DatasetStatus, DatasetType


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    dataset_type: DatasetType
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
