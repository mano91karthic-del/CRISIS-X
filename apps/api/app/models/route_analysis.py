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


class RouteAnalysisStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class RouteAnalysis(Base):
    """A single hazard-aware routing run (Phase 8): builds a graph from a
    user-uploaded roads Dataset, costs its edges against a Phase 4/5 hazard
    raster (optionally combined with a Phase 7 RiskAnalysis's declared
    vulnerability_weight/consequence_weight), and computes both a shortest
    route and a hazard-aware route via Dijkstra over the same blocked-edge-
    filtered graph. Does not re-derive hazard classification -- reuses
    Phase 6/7's classify/polygonize/intensity functions. The full result
    (both routes, blocked-edge summary, limitations) lives in `results`
    (JSON), same rationale as ExposureAnalysis.results/RiskAnalysis.results.
    Optional spatial outputs (route geometries, blocked-segments layer) are
    ordinary Dataset rows, linked back via Dataset.route_analysis_id.

    See docs/architecture/0009-phase-8-response-routing.md.
    """

    __tablename__ = "route_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Nullable at the DB level only so a deleted source dataset doesn't
    # cascade-delete this analysis record (SET NULL, same lineage-survives
    # policy as elsewhere) -- always populated when an analysis is created.
    road_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    hazard_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized copy of the hazard dataset's dataset_type at analysis
    # time -- resilient provenance, same rationale as ExposureAnalysis's own
    # hazard_dataset_type/exposure_dataset_type.
    hazard_dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Optional: a completed Phase 7 RiskAnalysis whose vulnerability_weight/
    # consequence_weight are reused for risk-aware costing instead of raw
    # hazard intensity alone. Must reference the SAME hazard_dataset_id
    # (enforced at the API layer) and the SAME exposure_dataset_id as
    # road_dataset_id (enforced at the API layer) -- the vulnerability/
    # consequence assumptions were declared for a specific asset type and
    # must never be silently reused for a different one (roads).
    risk_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("risk_analyses.id", ondelete="SET NULL"), nullable=True
    )

    origin_lon: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    destination_lon: Mapped[float] = mapped_column(Float, nullable=False)
    destination_lat: Mapped[float] = mapped_column(Float, nullable=False)

    # Explicit, required, caller-declared assumption -- never defaulted or
    # inferred (see app/services/routing.py). "How strongly should the
    # hazard-aware route avoid modeled hazard vs. go the short way."
    hazard_penalty_weight: Mapped[float] = mapped_column(Float, nullable=False)
    # Generic classification boundary (like Phase 4/7's breakpoints) --
    # has a defensible default, still overridable.
    block_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    node_snap_tolerance_m: Mapped[float] = mapped_column(Float, nullable=False)
    max_snap_distance_m: Mapped[float] = mapped_column(Float, nullable=False)

    # Phase 10: set only when this run was triggered through Scenario Lab,
    # never by a normal Phase 8 route-analysis run. SET NULL on delete,
    # same lineage-survives policy as every other FK here.
    scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The full route result: both routes, blocked-edge summary, graph
    # summary, and limitations. See
    # app/services/routing.py::run_route_analysis for the exact shape.
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
