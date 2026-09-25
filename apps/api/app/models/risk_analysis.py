import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskAnalysisStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class RiskAnalysis(Base):
    """A single risk-scoring run (Phase 7): combines a completed Phase 6
    ExposureAnalysis with two explicit assumptions (vulnerability_weight,
    consequence_weight) into a per-hazard-class risk_score/risk_class. Does
    not re-derive hazard classification or re-run the spatial overlay --
    both already happened in Phase 4/5 and Phase 6. The full per-class
    breakdown lives in `results` (JSON), same rationale as
    ExposureAnalysis.results. The optional spatial output (risk_score/
    risk_class joined onto Phase 6's feature layer) is an ordinary Dataset
    row, linked back via Dataset.risk_analysis_id.

    See docs/architecture/0008-phase-7-risk-engine.md.
    """

    __tablename__ = "risk_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Primary input -- the exposure run this risk analysis builds on.
    exposure_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("exposure_analyses.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized copies of the exposure analysis's own inputs, captured at
    # creation time -- resilient even if exposure_analysis_id or the
    # datasets it names are later deleted, same rationale as
    # ExposureAnalysis's own hazard_dataset_type/exposure_dataset_type.
    hazard_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    exposure_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    hazard_dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    exposure_dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Explicit, required, caller-declared assumptions -- never defaulted or
    # inferred (see app/services/risk.py). Both in [0, 1].
    vulnerability_weight: Mapped[float] = mapped_column(Float, nullable=False)
    consequence_weight: Mapped[float] = mapped_column(Float, nullable=False)
    risk_breakpoints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Phase 10: set only when this run was triggered through Scenario Lab,
    # never by a normal Phase 7 risk-analysis run. SET NULL on delete, same
    # lineage-survives policy as every other FK here.
    scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The full per-class risk breakdown + method/legend/limitations. See
    # app/services/risk.py::run_risk_analysis for the exact shape.
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
