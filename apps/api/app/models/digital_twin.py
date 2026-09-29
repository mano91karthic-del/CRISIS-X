import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TwinLayerStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class DigitalTwin(Base):
    """Phase 9: a per-project registry that composes the Dataset rows
    already produced by Phases 1-8 into one coherent, queryable "current
    state" -- it performs NO computation of its own (no raster/vector I/O,
    no new scientific values). One twin per project (enforced by a unique
    constraint on project_id). `version` is a lightweight monotonic change
    counter, not a full snapshot/branch history -- see
    docs/architecture/0010-phase-9-digital-twin.md for why full
    branching/what-if comparison belongs to a future Scenario Lab phase,
    not here.

    Deliberately has NO status/error_message columns, unlike every prior
    phase's run-record model: registering a layer is deterministic CRUD
    over already-validated Dataset rows, not a fallible computation that
    can fail partway through the way raster/vector processing can.
    """

    __tablename__ = "digital_twins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ADR 0013: the canonical study-area boundary this twin's other layers
    # are meant to spatially align with -- a Dataset of type STUDY_AREA
    # (a small vector polygon). Nullable: a twin created before this
    # concept existed, or one that never needed it, simply has none. SET
    # NULL on delete (not CASCADE), matching TwinLayer.dataset_id's own
    # "lineage survives" policy -- losing the AOI dataset shouldn't delete
    # the twin.
    study_area_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class TwinLayer(Base):
    """One registered slot in a DigitalTwin, referencing exactly one
    Dataset. The "role" a layer occupies is Dataset.dataset_type itself --
    reused directly rather than reinventing a parallel taxonomy.
    `category` is denormalized from Dataset.origin at registration time
    (verbatim reuse of the existing DatasetOrigin vocabulary). Only one
    ACTIVE layer may exist per (twin, dataset_type) at a time -- enforced
    at the API layer (app/api/digital_twin.py), not a DB constraint, same
    precedent as Phase 6's "one hazard + one exposure" being enforced by
    request shape rather than a DB constraint.

    `provenance` records ONLY registration-time facts (which twin version,
    what it superseded) -- never a copy of the Dataset's own provenance.
    The full scientific provenance (method/parameters/limitations) is
    always reachable through the nested Dataset in the API response, never
    duplicated here. See ADR 0010.
    """

    __tablename__ = "twin_layers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    twin_id: Mapped[str] = mapped_column(
        ForeignKey("digital_twins.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL (not CASCADE): a layer whose source Dataset is later deleted
    # survives as a historical record (dataset_type/category/provenance
    # still describe what it was), same lineage-survives policy as every
    # prior phase's Dataset FK columns.
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TwinLayerStatus.ACTIVE.value)
    registered_at_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)
