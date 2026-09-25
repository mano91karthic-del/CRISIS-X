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


class ExposureAnalysisStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class ExposureAnalysis(Base):
    """A single hazard x exposure-asset overlay run (Phase 6). One hazard
    dataset + one exposure dataset per run (v1 scope). The full per-class
    breakdown lives in `results` (JSON) directly on this record -- exposure
    summaries are fundamentally tabular, not a raster, and don't warrant a
    separate normalized results schema for what this phase needs. The
    optional spatial output (a vector "exposure_features" layer) is an
    ordinary Dataset row, same pattern as TerrainXPackage/HazardScenario/
    EOChangeAnalysis, linked back via Dataset.exposure_analysis_id.

    See docs/architecture/0007-phase-6-exposure-analysis.md.
    """

    __tablename__ = "exposure_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Nullable at the DB level only so a deleted source dataset doesn't
    # cascade-delete this analysis record (SET NULL, same lineage-survives
    # policy as elsewhere) — always populated when an analysis is created.
    hazard_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    exposure_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized copies of the two input datasets' dataset_type at
    # analysis time -- resilient provenance even if the source dataset is
    # later changed/deleted, same rationale as TerrainXPackage.source_system.
    hazard_dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    exposure_dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Required only for vector population exposure data -- which attribute
    # column holds the population count. Never guessed. Null otherwise.
    population_field: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Phase 10: set only when this run was triggered through Scenario Lab,
    # never by a normal Phase 6 exposure-analysis run. SET NULL on delete,
    # same lineage-survives policy as every other FK here.
    scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The full per-class breakdown + method/CRS/limitations. See
    # app/services/exposure.py::run_exposure_analysis for the exact shape.
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
