import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TerrainPackageStatus(str, enum.Enum):
    IMPORTED = "imported"
    PARTIALLY_INVALID = "partially_invalid"


class TerrainXPackage(Base):
    """A single TERRAIN-X terrain package import (Phase 3). Package-level
    metadata only — the individual terrain products it contains are ordinary
    Dataset rows (origin=terrain_x_import, terrain_x_package_id=this row's id).

    See docs/data-contracts/terrainx-package-v1.md for the contract this
    validates against, and docs/architecture/0004-phase-3-terrainx-integration.md
    for the design rationale.
    """

    __tablename__ = "terrain_x_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # TERRAIN-X's own opaque identifier for this export, if it provided one.
    # Distinct from `id` above, which is CRISIS-X's own primary key.
    package_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The complete, unmodified manifest as TERRAIN-X provided it — kept
    # separately from each asset's independently-verified Dataset fields, so
    # what TERRAIN-X claimed stays visible even where it differs from what
    # CRISIS-X actually found.
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    imported_at: Mapped[datetime] = mapped_column(default=_utcnow)
