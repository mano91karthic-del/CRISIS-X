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


class HazardType(str, enum.Enum):
    FLOOD = "flood"
    LANDSLIDE = "landslide"


class HazardScenarioStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class HazardScenario(Base):
    """A single parameterized run of a hazard model (Phase 4). Package-level
    metadata only — the output raster(s) are ordinary Dataset rows (same
    pattern as TerrainXPackage/its assets), linked back via
    Dataset.hazard_scenario_id.

    See docs/architecture/0005-phase-4-hazard-engine.md.
    """

    __tablename__ = "hazard_scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    hazard_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which datasets fed this run, keyed by role (e.g. {"dem": id,
    # "flow_direction": id, "flow_accumulation": id}).
    input_datasets: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The exact parameter values used, including any applied defaults —
    # always fully recorded, never just implied.
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Optional reference-only rainfall dataset (see hazard_flood.py /
    # hazard_landslide.py docstrings for why rainfall never drives the
    # computation itself).
    rainfall_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    # Phase 10: set only when this run was triggered through Scenario Lab
    # (a what-if re-run with overridden parameters/dataset), never by a
    # normal Phase 4 hazard-scenario run. SET NULL on delete -- the run
    # record and its output Dataset survive losing the scenario link, same
    # lineage-survives policy as every other FK here.
    scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
