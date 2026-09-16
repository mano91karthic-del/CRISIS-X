"""Synchronous metadata extraction and validation for uploaded datasets.

Phase 1 runs this inline in the request (no Redis/worker yet — see ADR 0002).
The one hard rule throughout: never guess or assume a CRS. If a file has no
parseable coordinate reference system, it is marked invalid rather than
silently treated as a known projection.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.dataset import DatasetStatus

RASTER_EXTENSIONS = {".tif", ".tiff"}
VECTOR_EXTENSIONS = {".geojson", ".json", ".shp", ".gpkg"}
TABULAR_EXTENSIONS = {".csv"}


@dataclass
class ValidationResult:
    status: str
    message: str | None = None
    file_format: str | None = None
    crs: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_dataset_file(path: Path) -> ValidationResult:
    suffix = path.suffix.lower()

    if suffix in RASTER_EXTENSIONS:
        return _validate_raster(path)
    if suffix in VECTOR_EXTENSIONS:
        return _validate_vector(path)
    if suffix in TABULAR_EXTENSIONS:
        return _validate_tabular(path)

    return ValidationResult(
        status=DatasetStatus.INVALID.value,
        message=(
            f"Unsupported file extension '{suffix or '(none)'}'. "
            "Supported: GeoTIFF (.tif/.tiff), GeoJSON (.geojson/.json), "
            "Shapefile (.shp), GeoPackage (.gpkg), CSV (.csv)."
        ),
        file_format=suffix.lstrip(".") or None,
    )


def _validate_raster(path: Path) -> ValidationResult:
    import rasterio

    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                return ValidationResult(
                    status=DatasetStatus.INVALID.value,
                    message="Raster has no defined CRS. Refusing to guess a coordinate reference system.",
                    file_format="geotiff",
                )
            bounds = src.bounds
            return ValidationResult(
                status=DatasetStatus.VALIDATED.value,
                file_format="geotiff",
                crs=src.crs.to_string(),
                bbox=(bounds.left, bounds.bottom, bounds.right, bounds.top),
                metadata={
                    "width": src.width,
                    "height": src.height,
                    "band_count": src.count,
                    "dtype": str(src.dtypes[0]) if src.dtypes else None,
                    "nodata": src.nodata,
                    "driver": src.driver,
                },
            )
    except Exception as exc:  # rasterio/GDAL raise varied error types for unreadable files
        return ValidationResult(
            status=DatasetStatus.INVALID.value,
            message=f"Could not read raster file: {exc}",
            file_format="geotiff",
        )


def _validate_vector(path: Path) -> ValidationResult:
    import geopandas as gpd

    file_format = path.suffix.lstrip(".") or None

    try:
        gdf = gpd.read_file(path)
        if gdf.crs is None:
            return ValidationResult(
                status=DatasetStatus.INVALID.value,
                message="Vector file has no defined CRS. Refusing to guess a coordinate reference system.",
                file_format=file_format,
            )
        minx, miny, maxx, maxy = gdf.total_bounds
        geometry_types = sorted(gdf.geom_type.dropna().unique().tolist())
        return ValidationResult(
            status=DatasetStatus.VALIDATED.value,
            file_format=file_format,
            crs=gdf.crs.to_string(),
            bbox=(float(minx), float(miny), float(maxx), float(maxy)),
            metadata={
                "feature_count": int(len(gdf)),
                "geometry_types": geometry_types,
            },
        )
    except Exception as exc:
        return ValidationResult(
            status=DatasetStatus.INVALID.value,
            message=f"Could not read vector file: {exc}",
            file_format=file_format,
        )


def _validate_tabular(path: Path) -> ValidationResult:
    import pandas as pd

    try:
        df = pd.read_csv(path, nrows=5000)
        return ValidationResult(
            status=DatasetStatus.VALIDATED.value,
            file_format="csv",
            metadata={
                "row_count_sampled": int(len(df)),
                "columns": [str(c) for c in df.columns],
            },
        )
    except Exception as exc:
        return ValidationResult(
            status=DatasetStatus.INVALID.value,
            message=f"Could not read CSV file: {exc}",
            file_format="csv",
        )
