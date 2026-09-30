"""Minimal seed script for CRISIS-X demo data.

Creates a project, digital twin, and basic synthetic datasets.
This is a starting point — enhance with full synthetic data later.

Usage:
    cd apps/api
    python scripts/seed_demo_data.py
"""

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.sqlite_pragma import enable_sqlite_foreign_keys
from app.models import Dataset, DigitalTwin, Project, TwinLayer

# Import all models to register them
import app.models  # noqa: F401


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seed():
    """Create minimal demo data."""
    settings = get_settings()
    print(f"Database URL: {settings.database_url}")
    print(f"Storage root: {settings.data_storage_root}")

    # Create tables
    Base.metadata.create_all(bind=engine)
    print("Tables created.")

    # Create storage directory
    storage_root = Path(settings.data_storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        # Check if project already exists
        existing = db.query(Project).filter(Project.name == "Chennai Demo").first()
        if existing:
            print(f"Project already exists: {existing.id}")
            project_id = existing.id
        else:
            # Create project
            project_id = _uuid()
            project = Project(
                id=project_id,
                name="Chennai Demo",
                description="Synthetic demo data for CRISIS-X",
                created_at=_utcnow(),
            )
            db.add(project)
            db.commit()
            print(f"Created project: {project_id}")

        # Check if twin already exists
        existing_twin = db.query(DigitalTwin).filter(DigitalTwin.project_id == project_id).first()
        if existing_twin:
            print(f"Digital twin already exists: {existing_twin.id}")
            twin_id = existing_twin.id
        else:
            # Create digital twin
            twin_id = _uuid()
            twin = DigitalTwin(
                id=twin_id,
                project_id=project_id,
                name="Chennai Demo Twin",
                description="Synthetic demo twin",
                version=1,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(twin)
            db.commit()
            print(f"Created digital twin: {twin_id}")

        # Create a simple AOI dataset
        aoi_id = _uuid()
        aoi_dir = storage_root / project_id / aoi_id
        aoi_dir.mkdir(parents=True, exist_ok=True)

        # Create a simple GeoJSON AOI (Chennai area)
        aoi_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Chennai AOI"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [80.20, 13.05],
                                [80.30, 13.05],
                                [80.30, 13.15],
                                [80.20, 13.15],
                                [80.20, 13.05],
                            ]
                        ],
                    },
                }
            ],
        }

        aoi_path = aoi_dir / "aoi.geojson"
        with open(aoi_path, "w") as f:
            json.dump(aoi_geojson, f)

        aoi_dataset = Dataset(
            id=aoi_id,
            project_id=project_id,
            name="Chennai AOI",
            dataset_type="study_area",
            origin="uploaded",
            source_filename="aoi.geojson",
            storage_path=str(aoi_path),
            file_format="geojson",
            crs="EPSG:4326",
            bbox_min_x=80.20,
            bbox_min_y=13.05,
            bbox_max_x=80.30,
            bbox_max_y=13.15,
            file_size_bytes=aoi_path.stat().st_size,
            checksum_sha256=hashlib.sha256(aoi_path.read_bytes()).hexdigest(),
            status="validated",
            metadata_json={"feature_count": 1, "geometry_types": ["Polygon"]},
            provenance={"synthetic": True, "description": "Synthetic AOI for demo"},
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(aoi_dataset)
        db.commit()
        print(f"Created AOI dataset: {aoi_id}")

        # Register AOI as twin layer
        layer = TwinLayer(
            id=_uuid(),
            twin_id=twin_id,
            dataset_id=aoi_id,
            dataset_type="study_area",
            category="observation",
            status="active",
            registered_at_version=1,
            provenance={"registered_at_version": 1, "synthetic": True},
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(layer)

        # Update twin version
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
        twin.version = 2
        db.commit()
        print(f"Registered AOI as twin layer")

        print("\n" + "=" * 50)
        print("SEED COMPLETE")
        print("=" * 50)
        print(f"Project ID: {project_id}")
        print(f"Twin ID: {twin_id}")
        print(f"AOI Dataset ID: {aoi_id}")
        print(f"\nAPI endpoints:")
        print(f"  GET /projects")
        print(f"  GET /projects/{project_id}")
        print(f"  GET /projects/{project_id}/digital-twin")
        print(f"  GET /digital-twins/{twin_id}/layers")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
