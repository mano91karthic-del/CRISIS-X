import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EOChangeMethod(str, enum.Enum):
    CHANGE_VECTOR_ANALYSIS = "change_vector_analysis"
    NORMALIZED_DIFFERENCE = "normalized_difference"


class ThresholdMethod(str, enum.Enum):
    MANUAL = "manual"
    OTSU = "otsu"


class EOChangeAnalysisStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class EOChangeAnalysis(Base):
    """A single before/after EO change-detection run (Phase 5). Package-level
    metadata only — outputs (change magnitude + mask) are ordinary Dataset
    rows, same pattern as HazardScenario/TerrainXPackage, linked back via
    Dataset.eo_change_analysis_id.

    `source_format` records the input format this run used ("geotiff" for
    every v1 run) so the record is self-describing if/when other EO source
    formats or live imagery feeds are added later — the model needs no
    schema change for that, only a wider set of accepted values.

    See docs/architecture/0006-phase-5-eo-change-detection.md.
    """

    __tablename__ = "eo_change_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Nullable at the DB level only so a deleted source dataset doesn't
    # cascade-delete this analysis record (SET NULL, same lineage-survives
    # policy as elsewhere) — always populated when an analysis is created.
    before_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    after_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )

    source_format: Mapped[str] = mapped_column(String(32), nullable=False, default="geotiff")
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_method: Mapped[str] = mapped_column(String(32), nullable=False)
    # The exact parameter values used, including any applied defaults (e.g.
    # the default band_indices range for CVA) — always fully recorded.
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
