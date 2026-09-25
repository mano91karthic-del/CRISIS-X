import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScenarioStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Scenario(Base):
    """Phase 10: a controlled, parallel what-if state derived from one
    Digital Twin's baseline at a point in time. A Scenario never mutates
    the twin it was created from -- see ScenarioBaselineLayer for the
    frozen-at-creation snapshot mechanism.

    Unlike DigitalTwin, Scenario carries a `status` lifecycle (active ->
    archived) because a scenario is deliberately retired once exploration
    of that hypothesis is done -- but no `version` counter: once a
    scenario has any analysis run attached, its layer overrides become
    immutable (enforced at the API layer), so there is nothing left to
    count the way DigitalTwin.version counts an evolving current state.

    See docs/architecture/0011-phase-10-scenario-lab.md.
    """

    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    twin_id: Mapped[str] = mapped_column(
        ForeignKey("digital_twins.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ScenarioStatus.ACTIVE.value)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ScenarioBaselineLayer(Base):
    """A frozen, point-in-time copy (by reference, not by data) of one of
    the Digital Twin's active TwinLayers at the moment this Scenario was
    created. Never updated after creation -- this is the mechanism that
    makes a scenario's baseline immune to later twin changes (new layer
    registrations, supersessions, retirements), so a scenario means the
    same thing every time it is read or re-run. See ADR 0011 "frozen
    baseline snapshot vs. live reference" for the full justification.

    `twin_layer_id` is SET NULL (not CASCADE) on the source TwinLayer's
    deletion -- TwinLayers are never hard-deleted in this system, but the
    same lineage-survives policy applies defensively. `dataset_type`/
    `category` are denormalized copies captured at snapshot time, same
    rationale as TwinLayer's own denormalized columns.
    """

    __tablename__ = "scenario_baseline_layers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    twin_layer_id: Mapped[str | None] = mapped_column(
        ForeignKey("twin_layers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ScenarioLayerOverride(Base):
    """A scenario's declared substitution of one dataset_type's layer with
    a different, explicitly chosen validated Dataset -- e.g. "use this
    alternate flood run instead of the twin's baseline flood layer."

    Unlike TwinLayer registration (which auto-supersedes a prior active
    layer of the same dataset_type, because the twin represents an
    evolving *current* state), a second override for the same
    dataset_type on one scenario is rejected at the API layer (409) --
    a scenario represents one coherent hypothesis, so a conflicting
    override is treated as a likely mistake, not an intentional revision.
    Overrides are mutable only until the scenario's first analysis run;
    after that, the API layer rejects further add/delete (409) -- same
    "freeze once used" reasoning as TwinLayer's "supersede, never edit,"
    applied to a scenario instead of a twin. See ADR 0011.
    """

    __tablename__ = "scenario_layer_overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    override_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
