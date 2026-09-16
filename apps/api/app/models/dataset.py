import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DatasetType(str, enum.Enum):
    """Matches the Data Hub catalog in the CRISIS-X architecture spec."""

    DEM = "dem"
    DSM = "dsm"
    IMAGERY = "imagery"
    RAINFALL = "rainfall"
    WEATHER = "weather"
    ROADS = "roads"
    BUILDINGS = "buildings"
    POPULATION = "population"
    HOSPITALS = "hospitals"
    SHELTERS = "shelters"
    CRITICAL_INFRASTRUCTURE = "critical_infrastructure"
    SAFE_ZONES = "safe_zones"
    TERRAIN_X_PACKAGE = "terrain_x_package"
    OTHER = "other"


class DatasetStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    INVALID = "invalid"


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored as plain strings (not a DB-native enum) so the schema is identical
    # across SQLite (local dev) and Postgres (production) with no migration
    # divergence; the Python enums validate values at the API boundary.
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_format: Mapped[str | None] = mapped_column(String(64), nullable=True)

    crs: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bbox_min_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_min_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Deliberately text, not a PostGIS geometry column, until Postgres/PostGIS
    # is available again — see docs/architecture/0002-phase-1-sqlite-fallback.md
    footprint_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DatasetStatus.UPLOADED.value)
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "metadata" is reserved by SQLAlchemy's Declarative Base, hence metadata_json.
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="datasets")
