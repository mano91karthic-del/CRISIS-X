"""Integrate the CRISIS-X Chennai Coherent Synthetic Test Dataset.

Creates a separate test project and Digital Twin with all datasets from the
synthetic test package. Does NOT modify the existing real Chennai project.

Usage:
    cd apps/api
    .venv\Scripts\python.exe scripts/integrate_synthetic_test_data.py
"""

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- Configuration ---
API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parent.parent
DB_PATH = API_DIR / "data" / "dev" / "crisisx.db"
STORAGE_ROOT = API_DIR / "data" / "storage"
SYNTHETIC_DATA_DIR = API_DIR / "data" / "synthetic_test_data"

PROJECT_NAME = "Chennai Coherent Synthetic Test"
PROJECT_DESCRIPTION = (
    "Synthetic internally consistent dataset for CRISIS-X software integration "
    "and validation. Not authoritative real-world observations."
)
TWIN_NAME = "Chennai Coherent Synthetic Test Twin"

# Synthetic dataset provenance
SYNTHETIC_PROVENANCE = {
    "source": "CRISIS-X Chennai Coherent Synthetic Test Dataset",
    "scientific_status": "synthetic_test_data",
    "description": "Synthetic test data for software integration and validation. Not authoritative real-world observations.",
}


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _validate_raster(path: Path) -> dict:
    """Validate a raster file and extract metadata."""
    import rasterio
    import numpy as np

    with rasterio.open(path) as src:
        arr = src.read(1).astype("float64")
        valid = ~np.isnan(arr)
        if src.nodata is not None:
            valid &= ~np.isclose(arr, src.nodata)
        valid_values = arr[valid]

        bounds = src.bounds
        return {
            "crs": src.crs.to_string(),
            "bbox": (float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)),
            "width": src.width,
            "height": src.height,
            "band_count": src.count,
            "dtype": str(src.dtypes[0]) if src.dtypes else None,
            "pixel_size_x": float(src.transform.a),
            "pixel_size_y": float(-src.transform.e),
            "nodata": src.nodata,
            "elevation_min": float(valid_values.min()) if valid_values.size > 0 else None,
            "elevation_max": float(valid_values.max()) if valid_values.size > 0 else None,
            "valid_pixel_count": int(valid_values.size),
            "total_pixel_count": int(arr.size),
        }


def _validate_vector(path: Path) -> dict:
    """Validate a vector file and extract metadata."""
    import geopandas as gpd

    gdf = gpd.read_file(path)
    minx, miny, maxx, maxy = gdf.total_bounds
    return {
        "crs": gdf.crs.to_string(),
        "bbox": (float(minx), float(miny), float(maxx), float(maxy)),
        "feature_count": int(len(gdf)),
        "geometry_types": sorted(gdf.geom_type.dropna().unique().tolist()),
        "columns": list(gdf.columns),
    }


def _validate_csv(path: Path) -> dict:
    """Validate a CSV file and extract metadata."""
    import pandas as pd

    df = pd.read_csv(path)
    return {
        "row_count": int(len(df)),
        "columns": list(df.columns),
    }


def _get_file_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        return "geotiff"
    if suffix in (".geojson", ".json"):
        return "geojson"
    if suffix == ".gpkg":
        return "geopackage"
    if suffix == ".csv":
        return "csv"
    if suffix == ".png":
        return "png"
    return suffix.lstrip(".")


def _derive_category(dataset_type: str, origin: str) -> str:
    """Derive the category for a twin layer based on dataset type and origin."""
    if dataset_type == "study_area":
        return "observation"
    if dataset_type in ("dem", "dsm"):
        return "terrain"
    if dataset_type == "buildings":
        return "terrain"
    if dataset_type == "roads":
        return "observation"
    if dataset_type in ("slope", "aspect"):
        return "terrain"
    if dataset_type in ("landslide_susceptibility", "landslide_susceptibility_screening"):
        return "hazard"
    if dataset_type in ("flood_depth", "flood_inundation", "flood_screening"):
        return "hazard"
    if dataset_type in ("exposure_features",):
        return "exposure"
    if dataset_type in ("risk_classification",):
        return "risk"
    if dataset_type in ("route_shortest", "route_hazard_aware", "route_blocked_segments"):
        return "route"
    if dataset_type in ("population",):
        return "exposure"
    if dataset_type in ("critical_infrastructure", "hospitals", "shelters"):
        return "observation"
    if dataset_type in ("rainfall", "weather"):
        return "observation"
    if dataset_type in ("imagery",):
        return "observation"
    return "observation"


def register_dataset(
    conn: sqlite3.Connection,
    project_id: str,
    name: str,
    dataset_type: str,
    origin: str,
    source_path: Path,
    storage_dir: Path,
    provenance_extra: dict | None = None,
) -> str:
    """Register a dataset: copy file to storage, validate, create DB record."""
    dataset_id = _uuid()

    # Copy file to storage
    dest_dir = storage_dir / dataset_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name
    shutil.copy2(source_path, dest_path)

    # Validate and extract metadata
    file_format = _get_file_format(source_path)
    metadata = {}
    crs = None
    bbox = None

    if file_format == "geotiff":
        meta = _validate_raster(dest_path)
        crs = meta["crs"]
        bbox = meta["bbox"]
        metadata = {
            "width": meta["width"],
            "height": meta["height"],
            "band_count": meta["band_count"],
            "dtype": meta["dtype"],
            "pixel_size_x": meta["pixel_size_x"],
            "pixel_size_y": meta["pixel_size_y"],
            "nodata": meta["nodata"],
            "valid_pixel_count": meta["valid_pixel_count"],
            "total_pixel_count": meta["total_pixel_count"],
        }
        if meta["elevation_min"] is not None:
            metadata["elevation_min"] = meta["elevation_min"]
            metadata["elevation_max"] = meta["elevation_max"]
    elif file_format in ("geojson", "geopackage"):
        meta = _validate_vector(dest_path)
        crs = meta["crs"]
        bbox = meta["bbox"]
        metadata = {
            "feature_count": meta["feature_count"],
            "geometry_types": meta["geometry_types"],
        }
    elif file_format == "csv":
        meta = _validate_csv(dest_path)
        metadata = {
            "row_count": meta["row_count"],
            "columns": meta["columns"],
        }

    # Create DB record
    checksum = _checksum(dest_path)
    file_size = _file_size(dest_path)
    now = _utcnow()

    provenance = {**SYNTHETIC_PROVENANCE, **(provenance_extra or {})}

    conn.execute(
        """INSERT INTO datasets (
            id, project_id, name, dataset_type, origin, source_filename,
            storage_path, file_format, crs,
            bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
            file_size_bytes, checksum_sha256, status, validation_message,
            metadata_json, provenance, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dataset_id,
            project_id,
            name,
            dataset_type,
            origin,
            source_path.name,
            str(dest_path.relative_to(API_DIR)),
            file_format,
            crs,
            bbox[0] if bbox else None,
            bbox[1] if bbox else None,
            bbox[2] if bbox else None,
            bbox[3] if bbox else None,
            file_size,
            checksum,
            "validated",
            None,
            json.dumps(metadata),
            json.dumps(provenance),
            now,
            now,
        ),
    )

    return dataset_id


def main():
    # Ensure storage root exists
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    SYNTHETIC_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Create database tables if they don't exist
    if not DB_PATH.exists():
        print("Creating database tables...")
        import sys
        sys.path.insert(0, str(API_DIR))
        from app.db.base import Base
        from app.db.session import engine
        Base.metadata.create_all(bind=engine)
        print("Database tables created.")
        # Re-connect after table creation
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # Re-connect after table creation
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

    # Connect to DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        # Check if project already exists
        existing = conn.execute(
            "SELECT id FROM projects WHERE name = ?", (PROJECT_NAME,)
        ).fetchone()
        if existing:
            print(f"Project '{PROJECT_NAME}' already exists with ID: {existing['id']}")
            project_id = existing["id"]
        else:
            # Create project
            project_id = _uuid()
            now = _utcnow()
            conn.execute(
                "INSERT INTO projects (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (project_id, PROJECT_NAME, PROJECT_DESCRIPTION, now),
            )
            conn.commit()
            print(f"Created project: {PROJECT_NAME} (ID: {project_id})")

        # Check if twin already exists
        existing_twin = conn.execute(
            "SELECT id FROM digital_twins WHERE project_id = ?", (project_id,)
        ).fetchone()
        if existing_twin:
            print(f"Digital Twin already exists with ID: {existing_twin['id']}")
            twin_id = existing_twin["id"]
        else:
            # Create Digital Twin
            twin_id = _uuid()
            now = _utcnow()
            conn.execute(
                """INSERT INTO digital_twins (id, project_id, name, description, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (twin_id, project_id, TWIN_NAME, PROJECT_DESCRIPTION, now, now),
            )
            conn.commit()
            print(f"Created Digital Twin: {TWIN_NAME} (ID: {twin_id})")

        # Define all datasets to register
        # Format: (source_path, name, dataset_type, origin, provenance_extra)
        datasets_to_register = [
            # 01 AOI
            ("01_aoi/aoi_boundary.geojson", "Synthetic AOI Boundary", "study_area", "uploaded", {"layer_group": "01_aoi"}),
            ("01_aoi/clipping_boundary.geojson", "Synthetic Clipping Boundary", "study_area", "uploaded", {"layer_group": "01_aoi"}),

            # 02 Terrain
            ("02_terrain/dem_chennai_synthetic_20m.tif", "Synthetic DEM 20m", "dem", "uploaded", {"layer_group": "02_terrain", "resolution": "20m"}),
            ("02_terrain/dsm_chennai_synthetic_20m.tif", "Synthetic DSM 20m", "dsm", "uploaded", {"layer_group": "02_terrain", "resolution": "20m"}),

            # 03 Buildings
            ("03_buildings/buildings.geojson", "Synthetic Buildings", "buildings", "uploaded", {"layer_group": "03_buildings", "feature_count": 505}),

            # 04 Roads
            ("04_roads/roads.geojson", "Synthetic Roads", "roads", "uploaded", {"layer_group": "04_roads", "feature_count": 35}),

            # 05 Land Use
            ("05_landuse/landuse.geojson", "Synthetic Land Use", "other", "uploaded", {"layer_group": "05_landuse"}),

            # 06 Hydrology
            ("06_hydrology/waterways.geojson", "Synthetic Waterways", "other", "uploaded", {"layer_group": "06_hydrology"}),
            ("06_hydrology/floodplain.geojson", "Synthetic Floodplain", "other", "uploaded", {"layer_group": "06_hydrology"}),
            ("06_hydrology/flow_accumulation_proxy.tif", "Synthetic Flow Accumulation Proxy", "other", "uploaded", {"layer_group": "06_hydrology"}),

            # 07 Climate
            ("07_climate/rainfall_baseline_mm.tif", "Synthetic Rainfall Baseline", "rainfall", "uploaded", {"layer_group": "07_climate"}),
            ("07_climate/rainfall_extreme_mm.tif", "Synthetic Rainfall Extreme", "rainfall", "uploaded", {"layer_group": "07_climate"}),
            ("07_climate/rainfall_storm_mm.tif", "Synthetic Rainfall Storm", "rainfall", "uploaded", {"layer_group": "07_climate"}),
            ("07_climate/weather_timeseries.csv", "Synthetic Weather Timeseries", "weather", "uploaded", {"layer_group": "07_climate"}),
            ("07_climate/storm_24h.csv", "Synthetic Storm 24h", "weather", "uploaded", {"layer_group": "07_climate"}),

            # 08 Hazards - Flood
            ("08_hazards/flood/flood_depth_25yr.tif", "Synthetic Flood Depth 25yr", "flood_depth", "uploaded", {"layer_group": "08_hazards", "return_period": "25yr"}),
            ("08_hazards/flood/flood_depth_50yr.tif", "Synthetic Flood Depth 50yr", "flood_depth", "uploaded", {"layer_group": "08_hazards", "return_period": "50yr"}),
            ("08_hazards/flood/flood_depth_100yr.tif", "Synthetic Flood Depth 100yr", "flood_depth", "uploaded", {"layer_group": "08_hazards", "return_period": "100yr"}),
            ("08_hazards/flood/inundation_25yr.tif", "Synthetic Inundation 25yr", "flood_inundation", "uploaded", {"layer_group": "08_hazards", "return_period": "25yr"}),
            ("08_hazards/flood/inundation_50yr.tif", "Synthetic Inundation 50yr", "flood_inundation", "uploaded", {"layer_group": "08_hazards", "return_period": "50yr"}),
            ("08_hazards/flood/inundation_100yr.tif", "Synthetic Inundation 100yr", "flood_inundation", "uploaded", {"layer_group": "08_hazards", "return_period": "100yr"}),

            # 08 Hazards - Landslide
            ("08_hazards/landslide/slope_degrees.tif", "Synthetic Slope Degrees", "slope", "uploaded", {"layer_group": "08_hazards"}),
            ("08_hazards/landslide/aspect_degrees.tif", "Synthetic Aspect Degrees", "aspect", "uploaded", {"layer_group": "08_hazards"}),
            ("08_hazards/landslide/susceptibility.tif", "Synthetic Landslide Susceptibility", "landslide_susceptibility", "uploaded", {"layer_group": "08_hazards"}),
            ("08_hazards/landslide/susceptibility_classes.tif", "Synthetic Landslide Susceptibility Classes", "landslide_susceptibility_screening", "uploaded", {"layer_group": "08_hazards"}),

            # 09 Satellite
            ("09_satellite/sentinel_like_pre_event.tif", "Synthetic Pre-Event Satellite", "imagery", "uploaded", {"layer_group": "09_satellite"}),
            ("09_satellite/sentinel_like_post_event.tif", "Synthetic Post-Event Satellite", "imagery", "uploaded", {"layer_group": "09_satellite"}),
            ("09_satellite/change_mask.tif", "Synthetic Change Mask", "other", "uploaded", {"layer_group": "09_satellite"}),
            ("09_satellite/ndvi_pre.tif", "Synthetic NDVI Pre", "other", "uploaded", {"layer_group": "09_satellite"}),
            ("09_satellite/ndvi_post.tif", "Synthetic NDVI Post", "other", "uploaded", {"layer_group": "09_satellite"}),

            # 10 Population
            ("10_population/population_grid.geojson", "Synthetic Population Grid", "population", "uploaded", {"layer_group": "10_population"}),

            # 11 Critical Infrastructure
            ("11_critical_infrastructure/critical_infrastructure.geojson", "Synthetic Critical Infrastructure", "critical_infrastructure", "uploaded", {"layer_group": "11_critical_infrastructure"}),

            # 12 Routing
            ("12_routing/road_nodes.csv", "Synthetic Road Nodes", "other", "uploaded", {"layer_group": "12_routing"}),
            ("12_routing/road_edges.csv", "Synthetic Road Edges", "other", "uploaded", {"layer_group": "12_routing"}),

            # 13 Scenarios
            ("13_scenarios/affected_buildings_100yr.geojson", "Synthetic Affected Buildings 100yr", "other", "uploaded", {"layer_group": "13_scenarios", "return_period": "100yr"}),
        ]

        # Register all datasets
        registered_datasets = {}
        for source_rel, name, dataset_type, origin, prov_extra in datasets_to_register:
            source_path = SYNTHETIC_DATA_DIR / source_rel
            if not source_path.exists():
                print(f"  WARNING: Source file not found: {source_path}")
                continue

            # Check if already registered
            existing_ds = conn.execute(
                "SELECT id FROM datasets WHERE project_id = ? AND name = ?",
                (project_id, name),
            ).fetchone()
            if existing_ds:
                print(f"  Already registered: {name} (ID: {existing_ds['id']})")
                registered_datasets[source_rel] = existing_ds["id"]
                continue

            dataset_id = register_dataset(
                conn, project_id, name, dataset_type, origin,
                source_path, STORAGE_ROOT / project_id, prov_extra,
            )
            registered_datasets[source_rel] = dataset_id
            print(f"  Registered: {name} (ID: {dataset_id})")

        conn.commit()

        # Set study area (use the AOI boundary)
        aoi_id = registered_datasets.get("01_aoi/aoi_boundary.geojson")
        if aoi_id:
            conn.execute(
                "UPDATE digital_twins SET study_area_dataset_id = ? WHERE id = ?",
                (aoi_id, twin_id),
            )
            conn.commit()
            print(f"\nSet study area: {aoi_id}")

        # Register twin layers for key datasets
        key_layers = [
            "01_aoi/aoi_boundary.geojson",
            "02_terrain/dem_chennai_synthetic_20m.tif",
            "02_terrain/dsm_chennai_synthetic_20m.tif",
            "03_buildings/buildings.geojson",
            "04_roads/roads.geojson",
            "05_landuse/landuse.geojson",
            "06_hydrology/waterways.geojson",
            "06_hydrology/floodplain.geojson",
            "07_climate/rainfall_baseline_mm.tif",
            "07_climate/rainfall_storm_mm.tif",
            "08_hazards/flood/flood_depth_25yr.tif",
            "08_hazards/flood/flood_depth_50yr.tif",
            "08_hazards/flood/flood_depth_100yr.tif",
            "08_hazards/flood/inundation_25yr.tif",
            "08_hazards/flood/inundation_50yr.tif",
            "08_hazards/flood/inundation_100yr.tif",
            "08_hazards/landslide/slope_degrees.tif",
            "08_hazards/landslide/aspect_degrees.tif",
            "08_hazards/landslide/susceptibility.tif",
            "08_hazards/landslide/susceptibility_classes.tif",
            "09_satellite/sentinel_like_pre_event.tif",
            "09_satellite/sentinel_like_post_event.tif",
            "09_satellite/change_mask.tif",
            "10_population/population_grid.geojson",
            "11_critical_infrastructure/critical_infrastructure.geojson",
        ]

        for source_rel in key_layers:
            dataset_id = registered_datasets.get(source_rel)
            if not dataset_id:
                continue

            # Get dataset info
            ds_row = conn.execute(
                "SELECT dataset_type, origin FROM datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            if not ds_row:
                continue

            dataset_type = ds_row["dataset_type"]
            origin = ds_row["origin"]
            category = _derive_category(dataset_type, origin)

            # Check if layer already exists
            existing_layer = conn.execute(
                "SELECT id FROM twin_layers WHERE twin_id = ? AND dataset_id = ?",
                (twin_id, dataset_id),
            ).fetchone()
            if existing_layer:
                print(f"  Layer already exists for: {source_rel}")
                continue

            # Get current twin version
            twin_row = conn.execute(
                "SELECT version FROM digital_twins WHERE id = ?", (twin_id,)
            ).fetchone()
            version = twin_row["version"] + 1

            layer_id = _uuid()
            now = _utcnow()
            conn.execute(
                """INSERT INTO twin_layers (
                    id, twin_id, dataset_id, dataset_type, category,
                    status, registered_at_version, provenance, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
                (
                    layer_id,
                    twin_id,
                    dataset_id,
                    dataset_type,
                    category,
                    version,
                    json.dumps({"registered_at_version": version, "synthetic_test_data": True}),
                    now,
                    now,
                ),
            )

            # Update twin version
            conn.execute(
                "UPDATE digital_twins SET version = ? WHERE id = ?",
                (version, twin_id),
            )
            print(f"  Registered layer: {dataset_type} ({source_rel})")

        conn.commit()

        # Print summary
        print("\n" + "=" * 60)
        print("INTEGRATION COMPLETE")
        print("=" * 60)
        print(f"Project ID: {project_id}")
        print(f"Digital Twin ID: {twin_id}")
        print(f"Total datasets registered: {len(registered_datasets)}")
        print(f"Total twin layers registered: {len(key_layers)}")
        print(f"\nProject Name: {PROJECT_NAME}")
        print(f"Twin Name: {TWIN_NAME}")
        print(f"\nAll datasets include synthetic provenance metadata.")
        print("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
