"""Phase 11: Command Dashboard preview conversion.

Converts already-computed Dataset files (Phase 1 uploads, Phase 2/4/5/6/
7/8 derived/analysis outputs) into browser-renderable formats for the
MapLibre/Three.js dashboard: WGS84 GeoJSON for vector data, and
reprojected/downsampled/colorized PNGs for raster data. This module
performs NO new scientific computation -- it never classifies, scores, or
derives a value that doesn't already exist on the source Dataset; it only
reprojects, resamples, and recolors what Phases 1-10 already produced.

Colorization always keys off each dataset's own already-stored class
legend (`metadata_json["class_legend"]`) or one of the fixed label
vocabularies Phase 4/6/7 already define (never a new, independently
invented classification) -- see LEGEND_COLORS below, which is the single
shared label -> color table also mirrored (must stay in sync manually)
by the frontend's `legendColors.ts`, so backend preview colors and
frontend legend swatches can never silently disagree about what a color
means.

See docs/architecture/0012-phase-11-command-dashboard.md.
"""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Single shared label -> hex color table. Every label string used
# anywhere in this system's fixed legends (hazard_landslide.CLASS_LEGEND,
# exposure.FLOOD_LABELS/EO_CHANGE_MASK_LABELS, risk.RISK_CLASS_LEGEND)
# must have an entry here -- see test_preview.py's coverage assertion.
LEGEND_COLORS: dict[str, str] = {
    "very_low": "#2c7bb6",
    "low": "#abd9e9",
    "moderate": "#ffffbf",
    "high": "#fdae61",
    "very_high": "#d7191c",
    "inundated": "#2b8cbe",
    "changed": "#e6550d",
    "no_change": "#bdbdbd",
}
UNKNOWN_LABEL_COLOR = "#9e9e9e"  # a label present in a dataset's own legend but not in LEGEND_COLORS -- never crash, always render something plus a flag

DEFAULT_GEOJSON_FEATURE_LIMIT = 20_000
DEFAULT_COORDINATE_PRECISION = 6
DEFAULT_MAX_PREVIEW_DIM = 1024

# Raster dataset_types whose values are class codes, not continuous
# measurements -- resampled nearest-neighbor only, never averaged, so a
# preview pixel is never an invented intermediate class.
CLASSIFIED_RASTER_DATASET_TYPES = {"flood_inundation", "landslide_susceptibility", "eo_change_mask", "flow_direction"}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class UnprojectableDatasetError(ValueError):
    """Raised when a dataset has no CRS, or its file cannot be read --
    fails closed, mirroring `validate_dataset_file`'s own "never guess a
    CRS" rule rather than silently assuming WGS84.
    """


# --- vector: GeoJSON -----------------------------------------------------------


def _round_coordinates(node: Any, precision: int) -> Any:
    if isinstance(node, float):
        return round(node, precision)
    if isinstance(node, list):
        return [_round_coordinates(child, precision) for child in node]
    return node


def build_geojson_preview(
    path: Path,
    *,
    limit: int = DEFAULT_GEOJSON_FEATURE_LIMIT,
    precision: int = DEFAULT_COORDINATE_PRECISION,
) -> dict[str, Any]:
    """Reads a vector Dataset file, reprojects to EPSG:4326, rounds
    coordinates, and caps the feature count -- returns an envelope dict
    (never a bare FeatureCollection) so truncation is always visible to
    the caller, never silently dropped.
    """
    import geopandas as gpd

    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        raise UnprojectableDatasetError(f"Could not read vector file: {exc}") from exc

    if gdf.crs is None:
        raise UnprojectableDatasetError("Vector dataset has no CRS; cannot generate a WGS84 preview.")

    source_crs = gdf.crs.to_string()
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    total_feature_count = int(len(gdf))
    truncated = total_feature_count > limit
    if truncated:
        gdf = gdf.iloc[:limit]

    raw = json.loads(gdf.to_json())
    features = raw.get("features", [])
    for feature in features:
        geometry = feature.get("geometry")
        if geometry is not None and "coordinates" in geometry:
            geometry["coordinates"] = _round_coordinates(geometry["coordinates"], precision)

    return {
        "type": "FeatureCollection",
        "features": features,
        "truncated": truncated,
        "total_feature_count": total_feature_count,
        "source_crs": source_crs,
    }


# --- raster: colorized WGS84 preview PNG ----------------------------------------


_CONTINUOUS_RAMP_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (43, 131, 186)),
    (0.5, (255, 255, 191)),
    (1.0, (215, 25, 28)),
]


def _continuous_ramp_rgb(normalized: np.ndarray) -> np.ndarray:
    stops_t = np.array([s[0] for s in _CONTINUOUS_RAMP_STOPS])
    channels = []
    for band in range(3):
        stops_c = np.array([s[1][band] for s in _CONTINUOUS_RAMP_STOPS])
        channels.append(np.interp(normalized, stops_t, stops_c))
    return np.stack(channels, axis=-1)


def colorize_array(
    array: np.ndarray,
    nodata: float | None,
    *,
    class_legend: dict[str, str] | None,
) -> np.ndarray:
    """Returns an (H, W, 4) uint8 RGBA array. Classified rasters (a
    class_legend is supplied) are colored per-class from LEGEND_COLORS,
    keyed by the dataset's OWN label strings -- never a new
    classification. Continuous rasters are normalized min/max and mapped
    through a fixed sequential ramp. Nodata/invalid cells are fully
    transparent (alpha 0), never silently colored as if they were data.
    """
    height, width = array.shape
    rgba = np.zeros((height, width, 4), dtype="uint8")
    valid = ~np.isnan(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)

    if class_legend:
        for code_str, label in class_legend.items():
            try:
                code = float(code_str)
            except (TypeError, ValueError):
                continue
            mask = valid & np.isclose(array, code)
            if not np.any(mask):
                continue
            color_hex = LEGEND_COLORS.get(label, UNKNOWN_LABEL_COLOR)
            r, g, b = _hex_to_rgb(color_hex)
            rgba[mask, 0] = r
            rgba[mask, 1] = g
            rgba[mask, 2] = b
            rgba[mask, 3] = 255
    else:
        valid_values = array[valid]
        if valid_values.size > 0:
            vmin, vmax = float(valid_values.min()), float(valid_values.max())
            span = (vmax - vmin) or 1.0
            normalized = np.clip((array - vmin) / span, 0.0, 1.0)
            rgb = _continuous_ramp_rgb(normalized[valid])
            rgba[valid, 0:3] = rgb.astype("uint8")
            rgba[valid, 3] = 255

    rgba[~valid, 3] = 0
    return rgba


def compute_raster_bounds_wgs84(path: Path) -> tuple[tuple[float, float, float, float], bool]:
    """Cheap WGS84 bounds computation for `/preview-bounds` -- uses
    `calculate_default_transform` alone (no pixel resampling), so it's
    fast regardless of `max_dim` and gives the same bounds the full
    preview would, up to integer-pixel-dimension rounding.
    """
    import rasterio
    from rasterio.transform import array_bounds
    from rasterio.warp import calculate_default_transform

    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise UnprojectableDatasetError("Raster dataset has no CRS; cannot compute WGS84 bounds.")
            transform, width, height = calculate_default_transform(
                src.crs, "EPSG:4326", src.width, src.height, *src.bounds
            )
            reprojection_applied = src.crs.to_epsg() != 4326
    except UnprojectableDatasetError:
        raise
    except Exception as exc:
        raise UnprojectableDatasetError(f"Could not read raster file: {exc}") from exc

    bounds = array_bounds(height, width, transform)
    return (bounds[0], bounds[1], bounds[2], bounds[3]), reprojection_applied


@dataclass
class RasterPreviewResult:
    png_bytes: bytes
    bounds_wgs84: tuple[float, float, float, float]  # (west, south, east, north)
    reprojection_applied: bool
    nodata_present: bool


def build_raster_preview(
    path: Path,
    *,
    dataset_type: str,
    class_legend: dict[str, str] | None,
    max_dim: int = DEFAULT_MAX_PREVIEW_DIM,
) -> RasterPreviewResult:
    """Reprojects a raster Dataset to EPSG:4326, downsamples to at most
    `max_dim` pixels per side in one `rasterio.warp.reproject` call
    (avoiding a separate hand-rolled downsampling pass), colorizes it,
    and PNG-encodes it. Resampling is nearest-neighbor for classified
    rasters (never averages class codes into an invented intermediate
    class) and bilinear for continuous rasters -- the same
    classified-vs-continuous distinction Phase 6/8's own reprojection
    code already makes.
    """
    import rasterio
    from rasterio.transform import array_bounds
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    categorical = dataset_type in CLASSIFIED_RASTER_DATASET_TYPES or class_legend is not None
    resampling = Resampling.nearest if categorical else Resampling.bilinear

    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise UnprojectableDatasetError("Raster dataset has no CRS; cannot generate a WGS84 preview.")
            src_array = src.read(1).astype("float64")
            src_nodata = src.nodata
            reprojection_applied = src.crs.to_epsg() != 4326

            dst_transform, dst_width, dst_height = calculate_default_transform(
                src.crs, "EPSG:4326", src.width, src.height, *src.bounds
            )
            scale = 1.0
            if max(dst_width, dst_height) > max_dim:
                scale = max_dim / max(dst_width, dst_height)
            out_width = max(1, round(dst_width * scale))
            out_height = max(1, round(dst_height * scale))
            out_transform = dst_transform * dst_transform.scale(dst_width / out_width, dst_height / out_height)

            fill_value = src_nodata if src_nodata is not None else np.nan
            dst_array = np.full((out_height, out_width), fill_value, dtype="float64")
            reproject(
                source=src_array,
                destination=dst_array,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=out_transform,
                dst_crs="EPSG:4326",
                resampling=resampling,
                src_nodata=src_nodata,
                dst_nodata=fill_value,
            )
    except UnprojectableDatasetError:
        raise
    except Exception as exc:
        raise UnprojectableDatasetError(f"Could not read/reproject raster file: {exc}") from exc

    rgba = colorize_array(dst_array, src_nodata, class_legend=class_legend)
    nodata_present = bool(np.any(np.isnan(dst_array)) or (src_nodata is not None and np.any(np.isclose(dst_array, src_nodata))))

    bounds = array_bounds(out_height, out_width, out_transform)  # (left, bottom, right, top)

    return RasterPreviewResult(
        png_bytes=_encode_rgba_png(rgba),
        bounds_wgs84=(bounds[0], bounds[1], bounds[2], bounds[3]),
        reprojection_applied=reprojection_applied,
        nodata_present=nodata_present,
    )


def _encode_rgba_png(rgba: np.ndarray) -> bytes:
    from PIL import Image

    image = Image.fromarray(rgba, mode="RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _encode_grayscale_png(array_uint8: np.ndarray) -> bytes:
    from PIL import Image

    image = Image.fromarray(array_uint8, mode="L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- raster: native-CRS heightmap for the Three.js terrain mesh -------------------


@dataclass
class HeightmapResult:
    png_bytes: bytes
    native_crs: str
    bounds_native: tuple[float, float, float, float]  # (west, south, east, north) in native_crs
    pixel_size_x_m: float
    pixel_size_y_m: float
    elevation_min_m: float
    elevation_max_m: float
    nodata_present: bool


@dataclass
class HeightmapBoundsInfo:
    native_crs: str
    bounds_native: tuple[float, float, float, float]
    pixel_size_x_m: float
    pixel_size_y_m: float
    elevation_min_m: float
    elevation_max_m: float
    nodata_present: bool


def _geographic_pixel_size_m(transform: Any, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    """Converts a geographic (degree-based) affine transform's per-pixel
    spacing into real meters, via an accurate WGS84 geodesic distance at
    the raster's own center latitude -- never a flat-Earth
    degrees-times-111320 approximation. Used ONLY to correct the
    pixel_size_x_m/pixel_size_y_m SCALAR reported alongside the heightmap;
    the array itself is never reprojected (see ADR 0012's native-CRS
    heightmap decision) -- a geographic CRS's "degrees" were simply never
    meters to begin with, and reporting them as such silently shrinks a
    DEM's real-world footprint by several orders of magnitude (see the
    Phase 11 "3D terrain invisible" bug fix -- a 111km-wide SRTM tile was
    being built as a ~1-meter-wide Three.js mesh).

    Pixel size is uniform in degrees across a simple affine-transform
    raster, so one representative (center) latitude is sufficient -- the
    same centroid-anchoring convention `pick_utm_crs` already uses
    elsewhere in this codebase. Longitude spacing genuinely does vary
    with latitude (a degree of longitude is shorter near the poles), so
    unlike latitude spacing this cannot be a single global constant --
    that's exactly why this is computed per-raster from its own bounds,
    not hardcoded.
    """
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    west, south, east, north = bounds
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0

    degree_size_x = abs(transform.a)
    degree_size_y = abs(transform.e)

    _, _, dist_x_m = geod.inv(center_lon, center_lat, center_lon + degree_size_x, center_lat)
    _, _, dist_y_m = geod.inv(center_lon, center_lat, center_lon, center_lat + degree_size_y)

    return abs(dist_x_m), abs(dist_y_m)


def _heightmap_output_grid(height: int, width: int, transform: Any, max_dim: int) -> tuple[Any, int, int]:
    """Computes the exact downsampled output transform/height/width that
    `build_heightmap()` produces for a raster exceeding `max_dim` pixels
    per side -- pure affine math, no raster I/O or resampling. Shared by
    `compute_heightmap_bounds()` so its reported bounds/pixel-size always
    describe the SAME pixel grid as the actual `/heightmap.png` for the
    same `max_dim`, instead of the two endpoints silently disagreeing
    (bounds computed at native resolution, PNG downsampled) whenever a
    DEM exceeds `DEFAULT_MAX_PREVIEW_DIM` -- e.g. a real 3601x3601 SRTM
    tile. Below `max_dim`, this is a no-op (native transform/dimensions).
    """
    if max(height, width) <= max_dim:
        return transform, height, width
    scale = max_dim / max(height, width)
    out_height = max(1, round(height * scale))
    out_width = max(1, round(width * scale))
    out_transform = transform * transform.scale(width / out_width, height / out_height)
    return out_transform, out_height, out_width


def compute_heightmap_bounds(path: Path, *, max_dim: int = DEFAULT_MAX_PREVIEW_DIM) -> HeightmapBoundsInfo:
    """Native-CRS bounds + pixel size for `/heightmap-bounds`, computed on
    the SAME (possibly downsampled) pixel grid `build_heightmap()` will
    actually encode for this `max_dim` -- see `_heightmap_output_grid()`.
    elevation_min_m/elevation_max_m are still the raster's true full-
    resolution min/max (reading at native resolution costs nothing extra
    here, and downsampling should never narrow the reported elevation
    range).
    """
    import rasterio
    from rasterio.transform import array_bounds

    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise UnprojectableDatasetError("Raster dataset has no CRS; cannot compute heightmap bounds.")
            array = src.read(1).astype("float64")
            nodata = src.nodata
            out_transform, out_height, out_width = _heightmap_output_grid(src.height, src.width, src.transform, max_dim)
            bounds = array_bounds(out_height, out_width, out_transform)
            if src.crs.is_geographic:
                pixel_size_x_m, pixel_size_y_m = _geographic_pixel_size_m(out_transform, bounds)
            else:
                pixel_size_x_m = float(out_transform.a)
                pixel_size_y_m = float(-out_transform.e)
            native_crs = src.crs.to_string()
    except UnprojectableDatasetError:
        raise
    except Exception as exc:
        raise UnprojectableDatasetError(f"Could not read raster file: {exc}") from exc

    valid = ~np.isnan(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)
    valid_values = array[valid]
    if valid_values.size == 0:
        raise UnprojectableDatasetError("Raster has no valid elevation data to compute bounds from.")

    return HeightmapBoundsInfo(
        native_crs=native_crs,
        bounds_native=(bounds[0], bounds[1], bounds[2], bounds[3]),
        pixel_size_x_m=pixel_size_x_m,
        pixel_size_y_m=pixel_size_y_m,
        elevation_min_m=float(valid_values.min()),
        elevation_max_m=float(valid_values.max()),
        nodata_present=bool((~valid).any()),
    )


def build_heightmap(path: Path, *, max_dim: int = DEFAULT_MAX_PREVIEW_DIM) -> HeightmapResult:
    """Builds an 8-bit grayscale heightmap PNG in the DEM/DSM's OWN
    (already-metric) CRS -- deliberately not reprojected to WGS84 (see
    ADR 0012 "native-CRS heightmap" decision): the 3D scene only needs a
    locally-planar XY grid, and a second reprojection of a continuous
    elevation surface would only add resampling error for no benefit.

    8-bit precision is a provisional choice pending a browser-canvas
    16-bit-decode spike (see ADR 0012) -- 256 elevation levels across the
    raster's actual min/max is treated as adequate for a screening-level
    command dashboard, not a survey instrument.

    Pixel values are linearly normalized between the raster's own actual
    min/max elevation (returned alongside for client-side
    denormalization: elevation_m = min + (pixel/255)*(max-min)). Nodata
    cells are encoded as 0 and must be treated as "no data," never as
    sea-level, by any consumer -- `nodata_present` flags this explicitly.
    """
    import rasterio
    from rasterio.transform import array_bounds
    from rasterio.warp import Resampling, reproject

    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise UnprojectableDatasetError("Raster dataset has no CRS; cannot generate a heightmap.")
            array = src.read(1).astype("float64")
            nodata = src.nodata
            transform = src.transform
            height, width = src.height, src.width
            crs = src.crs
    except UnprojectableDatasetError:
        raise
    except Exception as exc:
        raise UnprojectableDatasetError(f"Could not read raster file: {exc}") from exc

    out_transform, out_height, out_width = _heightmap_output_grid(height, width, transform, max_dim)
    if (out_height, out_width) != (height, width):
        fill_value = nodata if nodata is not None else np.nan
        out_array = np.full((out_height, out_width), fill_value, dtype="float64")
        reproject(
            source=array,
            destination=out_array,
            src_transform=transform,
            src_crs=crs,
            dst_transform=out_transform,
            dst_crs=crs,
            resampling=Resampling.bilinear,
            src_nodata=nodata,
            dst_nodata=fill_value,
        )
        array, transform, height, width = out_array, out_transform, out_height, out_width

    valid = ~np.isnan(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)

    valid_values = array[valid]
    if valid_values.size == 0:
        raise UnprojectableDatasetError("Raster has no valid elevation data to build a heightmap from.")

    elevation_min = float(valid_values.min())
    elevation_max = float(valid_values.max())
    span = (elevation_max - elevation_min) or 1.0

    normalized = np.zeros(array.shape, dtype="uint8")
    normalized[valid] = np.clip((array[valid] - elevation_min) / span, 0.0, 1.0).astype("float64") * 255
    normalized = normalized.astype("uint8")

    bounds = array_bounds(height, width, transform)
    if crs.is_geographic:
        pixel_size_x_m, pixel_size_y_m = _geographic_pixel_size_m(transform, bounds)
    else:
        pixel_size_x_m = float(transform.a)
        pixel_size_y_m = float(-transform.e)

    return HeightmapResult(
        png_bytes=_encode_grayscale_png(normalized),
        native_crs=crs.to_string(),
        bounds_native=(bounds[0], bounds[1], bounds[2], bounds[3]),
        pixel_size_x_m=pixel_size_x_m,
        pixel_size_y_m=pixel_size_y_m,
        elevation_min_m=elevation_min,
        elevation_max_m=elevation_max,
        nodata_present=bool((~valid).any()),
    )
