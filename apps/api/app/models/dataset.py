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
    FLOOD_SCREENING = "flood_screening"
    LANDSLIDE_SUSCEPTIBILITY_SCREENING = "landslide_susceptibility_screening"
    # ADR 0013: the canonical study-area boundary a project's other layers
    # are clipped/validated against (see DigitalTwin.study_area_dataset_id
    # and services/clip.py) -- a small vector polygon, uploaded like any
    # other dataset, never itself a computed/scientific output.
    STUDY_AREA = "study_area"
    # Superseded by Phase 3's per-asset import (see DatasetOrigin.TERRAIN_X_IMPORT
    # and TerrainXPackage) — left in place, unused, to avoid churn on the enum.
    TERRAIN_X_PACKAGE = "terrain_x_package"
    OTHER = "other"


class DatasetStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    INVALID = "invalid"


class DatasetOrigin(str, enum.Enum):
    """Explicit lineage: how this dataset came to exist. Phase 3 requirement —
    must be able to distinguish TERRAIN-X-derived from CRISIS-X-derived
    products, not just infer it from source_dataset_id being set.
    """

    UPLOADED = "uploaded"
    CRISISX_DERIVED = "crisisx_derived"
    TERRAIN_X_IMPORT = "terrain_x_import"
    # Phase 4: a hazard-scenario output (flood/landslide/...), distinct from
    # a plain terrain derivative because it carries scenario parameters and
    # assumptions, not just deterministic terrain math.
    HAZARD_MODEL = "hazard_model"
    # Phase 5: an EO change-detection analysis output.
    EO_ANALYSIS = "eo_analysis"
    # Phase 6: an exposure-analysis output (the optional feature layer).
    EXPOSURE_ANALYSIS = "exposure_analysis"
    # Phase 7: a risk-analysis output (the optional risk-classification
    # feature layer, joined onto Phase 6's exposure_features by hazard class).
    RISK_ANALYSIS = "risk_analysis"
    # Phase 8: a route-analysis output (shortest/hazard-aware route geometry,
    # or the blocked-segments layer).
    ROUTE_ANALYSIS = "route_analysis"


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
    # Holds either a DatasetType value (uploaded data) or a derived-product
    # name such as "slope"/"aspect" (see app/services/terrain.py) — these are
    # deliberately two separate Python enums so an upload can never be
    # mislabeled as a computed product.
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Set only on derived datasets; points at the DEM/DSM it was computed
    # from. SET NULL on delete: losing the live link doesn't invalidate the
    # derived raster, whose lineage is also recorded (immutably) in `provenance`.
    source_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Explicit lineage fact (see DatasetOrigin). Required, no default at the
    # DB level — every creation path must state it deliberately.
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    # Set only when origin == terrain_x_import. SET NULL on delete for the
    # same reason as source_dataset_id: losing the live link doesn't
    # invalidate the imported dataset, whose lineage is also in `provenance`.
    terrain_x_package_id: Mapped[str | None] = mapped_column(
        ForeignKey("terrain_x_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Set only when origin == hazard_model; points at the scenario run that
    # produced this dataset. One scenario may link multiple output datasets
    # (e.g. a raster now, a derived vector extent later) via this column.
    # SET NULL on delete: the dataset survives, provenance keeps the record.
    # use_alter=True: datasets <-> hazard_scenarios is a circular FK
    # (HazardScenario.rainfall_dataset_id points back at datasets.id) —
    # this defers the constraint to an ALTER TABLE so Base.metadata.create_all()
    # can order table creation correctly on any backend, not just SQLite's
    # lenient DDL.
    hazard_scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("hazard_scenarios.id", ondelete="SET NULL", use_alter=True, name="fk_datasets_hazard_scenario_id"),
        nullable=True,
        index=True,
    )
    # Set only when origin == eo_analysis; points at the change-detection run
    # that produced this dataset. Same circular-FK situation as
    # hazard_scenario_id (EOChangeAnalysis references datasets.id for its
    # before/after inputs), so use_alter=True here too.
    eo_change_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "eo_change_analyses.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_eo_change_analysis_id",
        ),
        nullable=True,
        index=True,
    )
    # Set only when origin == exposure_analysis; points at the exposure run
    # that produced this dataset (the optional feature-layer output). Same
    # circular-FK situation as hazard_scenario_id/eo_change_analysis_id
    # (ExposureAnalysis references datasets.id for its hazard/exposure
    # inputs), so use_alter=True here too.
    exposure_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "exposure_analyses.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_exposure_analysis_id",
        ),
        nullable=True,
        index=True,
    )
    # Set only when origin == risk_analysis; points at the risk-analysis run
    # that produced this dataset (the optional risk-classification feature
    # layer). Same circular-FK situation as hazard_scenario_id/
    # eo_change_analysis_id/exposure_analysis_id (RiskAnalysis references
    # datasets.id for its denormalized hazard/exposure dataset ids), so
    # use_alter=True here too.
    risk_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "risk_analyses.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_risk_analysis_id",
        ),
        nullable=True,
        index=True,
    )
    # Set only when origin == route_analysis; points at the route-analysis
    # run that produced this dataset (a route geometry or the blocked-
    # segments layer). Same circular-FK situation as hazard_scenario_id/
    # eo_change_analysis_id/exposure_analysis_id/risk_analysis_id
    # (RouteAnalysis references datasets.id for its road/hazard dataset
    # inputs), so use_alter=True here too.
    route_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "route_analyses.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_route_analysis_id",
        ),
        nullable=True,
        index=True,
    )
    # Generic (not imagery-specific): when this data was observed/recorded,
    # if known. Never fabricated — NULL when genuinely unknown. Resolution
    # policy (user-supplied vs extracted from file tags) lives in
    # app/api/datasets.py::upload_dataset.
    acquisition_date: Mapped[datetime | None] = mapped_column(nullable=True)
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
