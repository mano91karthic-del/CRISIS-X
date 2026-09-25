"""Phase 6: exposure analysis — which assets/population spatially intersect
a modeled hazard/change layer.

This is exposure, not risk: it reports spatial intersection facts (counts,
sums, lengths, areas per hazard class), never a severity judgment,
vulnerability score, damage estimate, or monetary loss. That distinction is
enforced structurally — this module has no code path that could produce a
single collapsed "danger score."

One pipeline, reused for every asset type: polygonize the hazard raster
into per-class polygons, reproject the exposure asset into the hazard's
CRS, then overlay (GeoPandas sjoin for points, true geometric intersection
via GeoPandas overlay for lines/polygons — not centroid-only). Population
rasters are converted to centroid points first and go through the same
points path; population polygons use area-weighted apportionment. See
docs/architecture/0007-phase-6-exposure-analysis.md for full reasoning.

The pure geometry functions (`classify_hazard_array`, `polygonize_hazard_raster`,
`raster_to_population_points`, `overlay_points`, `overlay_lines`,
`overlay_polygons`) are separated from raster/vector I/O
(`run_exposure_analysis`) so each is testable against known synthetic cases,
matching the pattern established in terrain.py / hazard_flood.py / eo_change.py.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.services.validation import RASTER_EXTENSIONS

SUPPORTED_HAZARD_TYPES = {"flood_inundation", "landslide_susceptibility", "eo_change_mask"}

DEFAULT_LANDSLIDE_LABELS = {"1": "very_low", "2": "low", "3": "moderate", "4": "high", "5": "very_high"}
FLOOD_LABELS = {"1": "inundated"}
EO_CHANGE_MASK_LABELS = {"0": "no_change", "1": "changed"}

_FEATURE_ID_COL = "__exposure_feature_id__"

EXPOSURE_LIMITATIONS = [
    "This is an exposure analysis: it reports which assets/population spatially intersect the given "
    "hazard/change layer. It does not assess vulnerability, structural damage, or monetary loss, and is "
    "not a risk score.",
    "Exposure does not imply confirmed damage; the underlying hazard/change layer is itself a modeled "
    "scenario or screening, not a confirmed real-world event.",
]

POPULATION_RASTER_LIMITATION = (
    "Population raster cells are treated as points at their cell centroid for spatial overlay; a cell "
    "is counted in whichever hazard class contains its centroid, which may mis-assign cells that "
    "straddle a hazard class boundary."
)

POPULATION_POLYGON_LIMITATION = (
    "Population is assumed uniformly distributed within each source polygon (area-weighted "
    "apportionment); real intra-polygon distribution is unlikely to be perfectly uniform."
)

UTM_REPROJECTION_LIMITATION = (
    "The hazard raster's CRS was geographic and was automatically reprojected to a single "
    "auto-selected UTM zone for metric area/length computation; a single UTM zone is assumed "
    "adequate for the raster's extent."
)

FLOOD_DEPTH_STATS_LIMITATION = (
    "depth_statistics summarize the entire modeled inundation extent (all inundated cells), not "
    "specifically the cells/features found to be exposed in this analysis."
)


def classify_hazard_array(
    dataset_type: str,
    array: np.ndarray,
    nodata: float | None,
    class_legend_override: dict[str, str] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, str]]:
    """Returns ``(class_codes, valid_mask, labels)``.

    ``class_codes`` holds small positive integer class codes (meaningless
    where ``valid_mask`` is False — always masked out before use, never
    read directly). ``labels`` maps ``str(code) -> human label``.

    Raises ValueError for an unsupported dataset_type -- fails closed rather
    than guessing at unfamiliar raster value semantics, since a wrong guess
    here would silently produce a wrong exposure count.
    """
    if dataset_type not in SUPPORTED_HAZARD_TYPES:
        raise ValueError(
            f"Unsupported hazard dataset_type '{dataset_type}'. Supported: {sorted(SUPPORTED_HAZARD_TYPES)}."
        )

    valid = ~np.isclose(array, nodata) if nodata is not None else np.ones(array.shape, dtype=bool)

    if dataset_type == "flood_inundation":
        codes = np.ones(array.shape, dtype="int32")
        labels = dict(FLOOD_LABELS)
    elif dataset_type == "eo_change_mask":
        codes = np.rint(array).astype("int32")
        labels = dict(EO_CHANGE_MASK_LABELS)
    else:  # landslide_susceptibility
        codes = np.rint(array).astype("int32")
        labels = dict(class_legend_override) if class_legend_override else dict(DEFAULT_LANDSLIDE_LABELS)

    return codes, valid, labels


def compute_hazard_read_window(
    hazard_width: int,
    hazard_height: int,
    hazard_transform: Any,
    hazard_crs: Any,
    exposure_bounds: tuple[float, float, float, float],
    exposure_crs: Any,
    *,
    buffer_px: int = 2,
) -> "Any | None":
    """Computes the hazard raster's pixel window covering the exposure
    dataset's bounding box, so `run_exposure_analysis` can window-read
    only the relevant region of a large hazard raster instead of the
    whole thing (see ADR 0007 addendum / the Phase 6 performance fix).

    The exposure bbox is reprojected into the hazard raster's own SOURCE
    CRS (never the other way around -- windowed reads happen against the
    on-disk raster, which is still in its original CRS at this point),
    then converted to a pixel-space window, padded by `buffer_px` pixels,
    and clipped to the raster's own extent.

    Padding + outward rounding guarantee the returned window is always a
    SUPERSET of the exact reprojected bbox -- a feature can never lose a
    partial cell it genuinely touches because of this optimization.

    Returns None if the exposure bbox does not overlap the hazard raster
    at all (including a degenerate/non-finite bbox) -- callers must treat
    that as "no exposure possible for this pair," not an error, exactly
    matching the existing behavior for an exposure feature that happens
    to fall outside every hazard class polygon.
    """
    import math

    from rasterio.warp import transform_bounds
    from rasterio.windows import Window

    if str(exposure_crs) != str(hazard_crs):
        left, bottom, right, top = transform_bounds(exposure_crs, hazard_crs, *exposure_bounds)
    else:
        left, bottom, right, top = exposure_bounds

    if not all(np.isfinite(v) for v in (left, bottom, right, top)):
        return None

    # Affine inverse of the four corners -- pure coordinate math, not a
    # data read, so this is cheap regardless of raster size.
    inv = ~hazard_transform
    col_a, row_a = inv * (left, top)
    col_b, row_b = inv * (right, bottom)

    row_off = math.floor(min(row_a, row_b)) - buffer_px
    row_stop = math.ceil(max(row_a, row_b)) + buffer_px
    col_off = math.floor(min(col_a, col_b)) - buffer_px
    col_stop = math.ceil(max(col_a, col_b)) + buffer_px

    candidate = Window(col_off, row_off, col_stop - col_off, row_stop - row_off)
    full = Window(0, 0, hazard_width, hazard_height)

    def _overlaps(a: "Window", b: "Window") -> bool:
        return (
            a.col_off < (b.col_off + b.width)
            and (a.col_off + a.width) > b.col_off
            and a.row_off < (b.row_off + b.height)
            and (a.row_off + a.height) > b.row_off
        )

    if not _overlaps(candidate, full):
        return None

    return candidate.intersection(full)


def _reproject_if_geographic_array(
    array: np.ndarray, transform: Any, crs: Any, nodata: float | None, target_crs: Any
) -> tuple[np.ndarray, Any]:
    """Reprojects `array` (nearest-neighbor -- hazard values are
    categorical or have a sharp nodata boundary, so interpolating them
    would blend adjacent classes or blend real values with the nodata
    sentinel into meaningless intermediate numbers) from `crs` into
    `target_crs`, using `calculate_default_transform` scoped to `array`'s
    OWN shape/bounds -- so this reprojects only whatever array is handed
    to it (the full raster, or a small windowed crop of it) without ever
    assuming it's the whole dataset. Shared by both the (possibly
    windowed) classification path and flood_inundation's separate,
    always-whole-raster depth_statistics pass, so the resampling
    behavior can never drift between the two call sites.
    """
    from rasterio.transform import array_bounds
    from rasterio.warp import Resampling, calculate_default_transform
    from rasterio.warp import reproject as rio_reproject

    height, width = array.shape
    bounds = array_bounds(height, width, transform)
    new_transform, new_width, new_height = calculate_default_transform(crs, target_crs, width, height, *bounds)
    fill_value = nodata if nodata is not None else 0.0
    new_array = np.full((new_height, new_width), fill_value, dtype="float64")
    rio_reproject(
        source=array,
        destination=new_array,
        src_transform=transform,
        src_crs=crs,
        dst_transform=new_transform,
        dst_crs=target_crs,
        resampling=Resampling.nearest,
        src_nodata=nodata,
        dst_nodata=nodata,
    )
    return new_array, new_transform


def polygonize_hazard_raster(codes: np.ndarray, valid: np.ndarray, transform: Any, crs: Any) -> "Any":
    """Converts a classified hazard raster into a GeoDataFrame of
    ``(hazard_class_code, geometry)`` — one dissolved (multi)polygon per
    distinct class value. Cells where ``valid`` is False produce no
    polygon at all, so "no hazard signal here" is never silently folded
    into any class.
    """
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape

    records = [
        {"hazard_class_code": int(value), "geometry": shape(geom)}
        for geom, value in shapes(codes, mask=valid, transform=transform)
    ]

    if not records:
        return gpd.GeoDataFrame({"hazard_class_code": [], "geometry": []}, geometry="geometry", crs=crs)

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
    return gdf.dissolve(by="hazard_class_code", as_index=False)


def raster_to_population_points(array: np.ndarray, transform: Any, crs: Any, nodata: float | None) -> "Any":
    """Converts a population raster into a GeoDataFrame of points at each
    valid cell's centroid, carrying a ``population`` attribute equal to the
    cell's value. Cell values are never resampled/interpolated -- only the
    resulting points are later reprojected, which doesn't alter the count
    each point carries. Nodata and non-positive cells are skipped.
    """
    import geopandas as gpd

    rows, cols = array.shape
    row_idx, col_idx = np.indices((rows, cols))
    # North-up affine assumption, consistent with the rest of this codebase
    # (terrain.py's pixel_size_x/y use the same a/e-only simplification).
    xs = transform.c + (col_idx + 0.5) * transform.a
    ys = transform.f + (row_idx + 0.5) * transform.e

    values = array.astype("float64")
    valid = np.ones(array.shape, dtype=bool)
    if nodata is not None:
        valid &= ~np.isclose(values, nodata)
    valid &= values > 0

    if not np.any(valid):
        return gpd.GeoDataFrame({"population": [], "geometry": []}, geometry="geometry", crs=crs)

    geometry = gpd.points_from_xy(xs[valid], ys[valid])
    return gpd.GeoDataFrame({"population": values[valid], "geometry": geometry}, crs=crs)


def _geometry_family(geom_type: str) -> str:
    if geom_type in ("Point", "MultiPoint"):
        return "point"
    if geom_type in ("LineString", "MultiLineString"):
        return "line"
    if geom_type in ("Polygon", "MultiPolygon"):
        return "polygon"
    raise ValueError(f"Unsupported geometry type '{geom_type}'")


def overlay_points(points_gdf: Any, hazard_polygons_gdf: Any, value_field: str | None = None) -> tuple[dict[int, dict], Any]:
    """Points within each hazard class polygon (`predicate="within"`).
    Returns ``({class_code: {"count":..., "sum":...}}, feature_gdf)`` where
    feature_gdf is the sjoin result (usable as the optional output layer).
    """
    import geopandas as gpd

    if points_gdf.empty or hazard_polygons_gdf.empty:
        return {}, points_gdf.iloc[0:0]

    joined = gpd.sjoin(
        points_gdf, hazard_polygons_gdf[["hazard_class_code", "geometry"]], predicate="within", how="inner"
    )
    result: dict[int, dict] = {}
    for class_code, group in joined.groupby("hazard_class_code"):
        entry: dict[str, Any] = {"count": int(len(group))}
        if value_field is not None:
            entry["sum"] = float(group[value_field].sum())
        result[int(class_code)] = entry
    return result, joined


def overlay_lines(lines_gdf: Any, hazard_polygons_gdf: Any) -> tuple[dict[int, dict], Any]:
    """True geometric intersection (not centroid-only) of line features
    against each hazard class polygon. Returns
    ``({class_code: {"length_m":..., "feature_count":...}}, feature_gdf)``.
    """
    import geopandas as gpd

    if lines_gdf.empty or hazard_polygons_gdf.empty:
        return {}, lines_gdf.iloc[0:0]

    lines = lines_gdf.copy()
    lines[_FEATURE_ID_COL] = range(len(lines))
    overlaid = gpd.overlay(
        lines[[_FEATURE_ID_COL, "geometry"]],
        hazard_polygons_gdf[["hazard_class_code", "geometry"]],
        how="intersection",
    )
    if overlaid.empty:
        return {}, overlaid

    overlaid["length_m"] = overlaid.geometry.length
    result: dict[int, dict] = {}
    for class_code, group in overlaid.groupby("hazard_class_code"):
        result[int(class_code)] = {
            "length_m": float(group["length_m"].sum()),
            "feature_count": int(group[_FEATURE_ID_COL].nunique()),
        }
    return result, overlaid


def overlay_polygons(
    polygons_gdf: Any, hazard_polygons_gdf: Any, value_field: str | None = None
) -> tuple[dict[int, dict], Any]:
    """True geometric intersection (not centroid-only) of polygon features
    against each hazard class polygon. If ``value_field`` is given (e.g. a
    population count per source polygon), apportions it by intersected-area
    fraction (uniform-density assumption, disclosed as a limitation).
    Returns ``({class_code: {"area_m2":..., "feature_count":..., "population_sum":...}}, feature_gdf)``.
    """
    import geopandas as gpd

    if polygons_gdf.empty or hazard_polygons_gdf.empty:
        return {}, polygons_gdf.iloc[0:0]

    polys = polygons_gdf.copy()
    polys[_FEATURE_ID_COL] = range(len(polys))

    density_map = None
    if value_field is not None:
        total_area = polys.geometry.area
        with np.errstate(invalid="ignore", divide="ignore"):
            density = polys[value_field].astype("float64") / total_area.replace(0, np.nan)
        density_map = dict(zip(polys[_FEATURE_ID_COL], density))

    cols = [_FEATURE_ID_COL, "geometry"]
    overlaid = gpd.overlay(
        polys[cols], hazard_polygons_gdf[["hazard_class_code", "geometry"]], how="intersection"
    )
    if overlaid.empty:
        return {}, overlaid

    overlaid["area_m2"] = overlaid.geometry.area
    result: dict[int, dict] = {}
    for class_code, group in overlaid.groupby("hazard_class_code"):
        entry: dict[str, Any] = {
            "area_m2": float(group["area_m2"].sum()),
            "feature_count": int(group[_FEATURE_ID_COL].nunique()),
        }
        if density_map is not None:
            densities = group[_FEATURE_ID_COL].map(density_map)
            weighted = (densities.fillna(0.0) * group["area_m2"]).sum()
            entry["population_sum"] = float(weighted)
        result[int(class_code)] = entry
    return result, overlaid


@dataclass
class ExposureAnalysisResult:
    results: dict[str, Any]
    output_path: Path | None
    output_crs: str | None
    output_bbox: tuple[float, float, float, float] | None


def run_exposure_analysis(
    hazard_path: Path,
    hazard_dataset_type: str,
    exposure_path: Path,
    output_path: Path,
    *,
    population_field: str | None = None,
    class_legend_override: dict[str, str] | None = None,
) -> ExposureAnalysisResult:
    import rasterio
    from rasterio.windows import transform as window_transform_of

    if hazard_dataset_type not in SUPPORTED_HAZARD_TYPES:
        raise ValueError(
            f"Unsupported hazard dataset_type '{hazard_dataset_type}'. Supported: {sorted(SUPPORTED_HAZARD_TYPES)}."
        )

    is_raster_exposure = exposure_path.suffix.lower() in RASTER_EXTENSIONS

    # --- determine the exposure dataset's own bounds/CRS FIRST (metadata-
    # only for a raster exposure input; a full read for vector, since the
    # overlay step needs the geometries anyway) -- this is what lets the
    # hazard raster be window-read below instead of loaded in full. See
    # the Phase 6 performance fix: a spatially small exposure dataset
    # (e.g. a few thousand building footprints) must not require reading
    # every cell of a multi-million-cell hazard raster.
    gdf: "Any | None" = None
    if is_raster_exposure:
        with rasterio.open(exposure_path) as esrc:
            exposure_bounds = tuple(esrc.bounds)
            exposure_crs = esrc.crs
        if exposure_crs is None:
            raise ValueError("Exposure raster has no CRS.")
    else:
        import geopandas as gpd

        gdf = gpd.read_file(exposure_path)
        if gdf.crs is None:
            raise ValueError("Exposure vector file has no CRS.")
        geom_types = set(gdf.geom_type.dropna().unique())
        if not geom_types:
            raise ValueError("Exposure vector file has no geometries.")
        families = {_geometry_family(g) for g in geom_types}
        if len(families) > 1:
            raise ValueError(
                f"Exposure dataset has mixed geometry types ({sorted(geom_types)}); "
                "upload separate files per geometry type."
            )
        family = next(iter(families))
        exposure_bounds = tuple(gdf.total_bounds)
        exposure_crs = gdf.crs

    # --- open the hazard raster, read ONLY the window covering the exposure bbox ---
    limitations = list(EXPOSURE_LIMITATIONS)
    with rasterio.open(hazard_path) as src:
        hazard_src_crs = src.crs
        hazard_src_transform = src.transform
        hazard_nodata = src.nodata
        hazard_bounds = tuple(src.bounds)  # the FULL raster's bounds -- used for UTM zone selection, unaffected by windowing

        window = compute_hazard_read_window(
            src.width, src.height, hazard_src_transform, hazard_src_crs, exposure_bounds, exposure_crs
        )
        if window is None:
            # No spatial overlap at all between the exposure bbox and the
            # hazard raster -- a 1x1 all-invalid array short-circuits
            # straight to an empty polygonization (via the existing
            # `hazard_polygons_gdf.empty` handling in overlay_*), exactly
            # matching today's behavior for an exposure feature that
            # happens to fall outside every hazard class polygon. Reading
            # zero actual raster cells for a definitively-non-overlapping
            # pair is itself part of the performance fix.
            hazard_array = np.zeros((1, 1), dtype="float64")
            hazard_transform = hazard_src_transform
        else:
            hazard_array = src.read(1, window=window)
            hazard_transform = window_transform_of(window, hazard_src_transform)

    hazard_crs = hazard_src_crs
    reprojected = bool(hazard_crs.is_geographic)
    if reprojected:
        from app.services.terrain import pick_utm_crs

        # Picked from the FULL raster's bounds, never the window's -- the
        # UTM zone choice must not depend on which exposure asset happens
        # to be analyzed against this hazard layer.
        target_crs = pick_utm_crs(hazard_bounds, hazard_crs)
        hazard_array, hazard_transform = _reproject_if_geographic_array(
            hazard_array, hazard_transform, hazard_crs, hazard_nodata, target_crs
        )
        hazard_crs = target_crs
        limitations.append(UTM_REPROJECTION_LIMITATION)

    if window is None:
        valid = np.zeros(hazard_array.shape, dtype=bool)
        codes = np.zeros(hazard_array.shape, dtype="int32")
        _, _, labels = classify_hazard_array(
            hazard_dataset_type, np.zeros(hazard_array.shape), None, class_legend_override
        )
    else:
        codes, valid, labels = classify_hazard_array(
            hazard_dataset_type, hazard_array, hazard_nodata, class_legend_override
        )
    hazard_polygons = polygonize_hazard_raster(codes, valid, hazard_transform, hazard_crs)

    # depth_statistics is documented as covering the ENTIRE modeled
    # inundation extent, not just the cells found to be exposed -- so,
    # unlike the classification/polygonization path above, it is always
    # computed from a separate, whole-raster read (flood rasters are not
    # the scale bottleneck this window optimization targets; landslide/
    # EO-change hazards, which have no such whole-raster-scope statistic,
    # get the full benefit of the windowed read above).
    depth_statistics = None
    if hazard_dataset_type == "flood_inundation":
        with rasterio.open(hazard_path) as full_src:
            full_array = full_src.read(1)
        full_transform = hazard_src_transform
        full_crs = hazard_src_crs
        if full_crs.is_geographic:
            from app.services.terrain import pick_utm_crs

            full_target_crs = pick_utm_crs(hazard_bounds, full_crs)
            full_array, full_transform = _reproject_if_geographic_array(
                full_array, full_transform, full_crs, hazard_nodata, full_target_crs
            )
        full_valid = ~np.isclose(full_array, hazard_nodata) if hazard_nodata is not None else np.ones(full_array.shape, dtype=bool)
        valid_depths = full_array[full_valid]
        if valid_depths.size > 0:
            depth_statistics = {
                "min_depth_m": float(np.min(valid_depths)),
                "mean_depth_m": float(np.mean(valid_depths)),
                "max_depth_m": float(np.max(valid_depths)),
            }
            limitations.append(FLOOD_DEPTH_STATS_LIMITATION)

    # --- load exposure dataset, overlay against hazard classes ---
    output_gdf = None

    if is_raster_exposure:
        with rasterio.open(exposure_path) as esrc:
            pop_array = esrc.read(1)
            pop_transform = esrc.transform
            pop_crs = esrc.crs
            pop_nodata = esrc.nodata

        points_gdf = raster_to_population_points(pop_array, pop_transform, pop_crs, pop_nodata)
        if not points_gdf.empty and points_gdf.crs != hazard_crs:
            points_gdf = points_gdf.to_crs(hazard_crs)

        method = "population_raster_centroid_overlay"
        class_breakdown, _ = overlay_points(points_gdf, hazard_polygons, value_field="population")
        limitations.append(POPULATION_RASTER_LIMITATION)

        if not hazard_polygons.empty:
            output_gdf = hazard_polygons.copy()
            output_gdf["population_within_class"] = output_gdf["hazard_class_code"].map(
                lambda c: class_breakdown.get(int(c), {}).get("sum", 0.0)
            )
    else:
        if gdf.crs != hazard_crs:
            gdf = gdf.to_crs(hazard_crs)

        if family == "point":
            method = "point_asset_overlay"
            class_breakdown, output_gdf = overlay_points(gdf, hazard_polygons, value_field=population_field)
        elif family == "line":
            if population_field is not None:
                raise ValueError("population_field is not applicable to line assets.")
            method = "line_asset_overlay"
            class_breakdown, output_gdf = overlay_lines(gdf, hazard_polygons)
        else:
            method = "polygon_asset_overlay"
            class_breakdown, output_gdf = overlay_polygons(gdf, hazard_polygons, value_field=population_field)
            if population_field is not None:
                limitations.append(POPULATION_POLYGON_LIMITATION)

    # --- assemble results (label-keyed, class code preserved for traceability) ---
    by_class: dict[str, dict] = {}
    for code, stats in class_breakdown.items():
        label = labels.get(str(code), f"class_{code}")
        by_class[label] = {"hazard_class_code": code, **stats}

    results: dict[str, Any] = {
        "method": method,
        "hazard_dataset_type": hazard_dataset_type,
        "reprojected_hazard_crs": reprojected,
        "crs": hazard_crs.to_string(),
        "hazard_class_labels": labels,
        "by_class": by_class,
        "limitations": limitations,
    }
    if depth_statistics is not None:
        results["depth_statistics"] = depth_statistics

    # --- optional output feature layer ---
    output_written_path: Path | None = None
    output_crs_str: str | None = None
    output_bbox: tuple[float, float, float, float] | None = None

    if output_gdf is not None and not output_gdf.empty:
        output_gdf.to_file(output_path, driver="GeoJSON")
        output_written_path = output_path
        output_crs_str = hazard_crs.to_string()
        bounds = output_gdf.total_bounds
        output_bbox = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))

    return ExposureAnalysisResult(
        results=results,
        output_path=output_written_path,
        output_crs=output_crs_str,
        output_bbox=output_bbox,
    )
