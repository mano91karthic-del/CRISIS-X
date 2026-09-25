"""Geospatial Processing (Phase 2, extended in Phase 4): slope, aspect, D8
flow direction, and flow accumulation derivation from a validated DEM/DSM
dataset.

Flow direction/accumulation were deliberately deferred out of Phase 2 (see
docs/architecture/0003-phase-2-geospatial-processing.md) until a concrete
consumer needed them. Phase 4's flood hazard engine is that consumer, but
these remain general terrain-characterization products — deterministic,
scenario-independent — so they live here with slope/aspect, not in the
hazard-modeling code. See docs/architecture/0005-phase-4-hazard-engine.md.

The pure math (`compute_slope_aspect`, `fill_depressions`,
`compute_flow_direction`, `compute_flow_accumulation`) is separated from
raster I/O (`derive_terrain_product`) so each algorithm is testable against
known analytic cases without touching a file.
"""

import heapq
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Sentinel written to the aspect raster for cells where slope is (numerically)
# zero — a flat cell has no meaningful downhill direction. Distinct from the
# raster's nodata value below.
FLAT_ASPECT_SENTINEL = -1.0

# Nodata value written to derived rasters (slope, aspect, flow_accumulation).
# Slope (>=0 degrees) and aspect (0-360, or -1 for flat) never legitimately
# take this value.
DERIVED_NODATA = -9999.0

# --- D8 flow direction encoding (ESRI convention) ---
# Each entry: (code, row_delta, col_delta). Row 0 = north, col 0 = west.
_D8_DIRECTIONS: list[tuple[int, int, int]] = [
    (1, 0, 1),  # E
    (2, 1, 1),  # SE
    (4, 1, 0),  # S
    (8, 1, -1),  # SW
    (16, 0, -1),  # W
    (32, -1, -1),  # NW
    (64, -1, 0),  # N
    (128, -1, 1),  # NE
]
FLOW_DIRECTION_CODES = {
    "E": 1, "SE": 2, "S": 4, "SW": 8, "W": 16, "NW": 32, "N": 64, "NE": 128,
}
# A valid, non-nodata pixel value meaning "no downhill neighbor determined"
# (raster edge or an unresolved flat after filling) — distinct from nodata.
FLOW_DIRECTION_UNDETERMINED = 0
# Raster nodata value for flow_direction. Fits comfortably in int16 alongside
# the small code values above.
FLOW_DIRECTION_NODATA = -9999

_NEIGHBOR_OFFSETS_8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def compute_slope_aspect(
    elevation: np.ndarray,
    pixel_size_x: float,
    pixel_size_y: float,
    nodata: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Horn's method — the same 3x3 finite-difference kernel GDAL's
    ``gdaldem`` and QGIS use.

    ``elevation`` is a 2D array with row 0 = north, column 0 = west (the
    standard north-up raster convention). ``pixel_size_x``/``pixel_size_y``
    must be in the same real-world linear unit (typically meters) as the
    elevation values — a geographic (degree-based) CRS must be reprojected
    first, or slope will be wrong. See ``load_projected_elevation``.

    Returns ``(slope_degrees, aspect_degrees)``, both the same shape as
    ``elevation``. Border cells (no full 3x3 neighborhood) and any cell whose
    neighborhood touches nodata are ``np.nan`` — never a fabricated value.
    Flat cells (slope ~ 0) get ``FLAT_ASPECT_SENTINEL`` in the aspect output,
    since direction is undefined on flat ground.
    """
    if elevation.ndim != 2:
        raise ValueError("elevation must be a 2D array")
    if pixel_size_x <= 0 or pixel_size_y <= 0:
        raise ValueError("pixel sizes must be positive")

    z = elevation.astype("float64")
    rows, cols = z.shape

    slope = np.full((rows, cols), np.nan, dtype="float64")
    aspect = np.full((rows, cols), np.nan, dtype="float64")

    if rows < 3 or cols < 3:
        return slope, aspect

    is_nodata = np.isclose(z, nodata) if nodata is not None else np.zeros_like(z, dtype=bool)

    # Horn's classic 3x3 neighbor labeling (row-major, center z5 unused):
    #   z1 z2 z3      NW  N  NE
    #   z4    z6   =  W   .  E
    #   z7 z8 z9      SW  S  SE
    z1 = z[0:-2, 0:-2]
    z2 = z[0:-2, 1:-1]
    z3 = z[0:-2, 2:]
    z4 = z[1:-1, 0:-2]
    z6 = z[1:-1, 2:]
    z7 = z[2:, 0:-2]
    z8 = z[2:, 1:-1]
    z9 = z[2:, 2:]

    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * pixel_size_x)
    # North-positive: dzdy > 0 means elevation increases going north.
    dzdy = ((z1 + 2 * z2 + z3) - (z7 + 2 * z8 + z9)) / (8 * pixel_size_y)

    slope_interior = np.degrees(np.arctan(np.sqrt(dzdx**2 + dzdy**2)))

    # Aspect = compass bearing of the downhill (steepest descent) direction.
    # Downhill vector is -grad(z) = (downhill_east, downhill_north); bearing
    # from north, clockwise, is atan2(east_component, north_component).
    downhill_east = -dzdx
    downhill_north = -dzdy
    aspect_interior = np.degrees(np.arctan2(downhill_east, downhill_north))
    aspect_interior = np.mod(aspect_interior, 360.0)

    flat = np.isclose(slope_interior, 0.0)
    aspect_interior = np.where(flat, FLAT_ASPECT_SENTINEL, aspect_interior)

    neighborhood_nodata = (
        is_nodata[0:-2, 0:-2]
        | is_nodata[0:-2, 1:-1]
        | is_nodata[0:-2, 2:]
        | is_nodata[1:-1, 0:-2]
        | is_nodata[1:-1, 1:-1]
        | is_nodata[1:-1, 2:]
        | is_nodata[2:, 0:-2]
        | is_nodata[2:, 1:-1]
        | is_nodata[2:, 2:]
    )
    slope_interior = np.where(neighborhood_nodata, np.nan, slope_interior)
    aspect_interior = np.where(neighborhood_nodata, np.nan, aspect_interior)

    slope[1:-1, 1:-1] = slope_interior
    aspect[1:-1, 1:-1] = aspect_interior

    return slope, aspect


def fill_depressions(elevation: np.ndarray, nodata: float | None = None) -> np.ndarray:
    """Simplified priority-flood depression filling (Barnes et al.).

    Seeds a min-heap with every valid border cell, then repeatedly pops the
    lowest and raises each unvisited neighbor to at least that elevation,
    guaranteeing every interior cell has a monotonic non-increasing path to
    the border — no interior pits remain. Pure NumPy/stdlib ``heapq``.

    Limitations (documented, not silently papered over): no flat-area
    flow-resolution refinement (e.g. Garbrecht & Martz) — flats route via a
    simple deterministic tie-break downstream. Cells not 8-connected to the
    raster border through valid (non-nodata) cells — i.e. fully enclosed by
    nodata — are left undefined (NaN); this is physically reasonable (no
    basis to route flow for terrain with no connection to the AOI edge), not
    a bug.
    """
    if elevation.ndim != 2:
        raise ValueError("elevation must be a 2D array")

    rows, cols = elevation.shape
    z = elevation.astype("float64")
    valid = ~np.isclose(z, nodata) if nodata is not None else np.ones_like(z, dtype=bool)

    filled = np.full((rows, cols), np.nan, dtype="float64")
    visited = np.zeros((rows, cols), dtype=bool)
    heap: list[tuple[float, int, int]] = []

    border_cells = set()
    if rows > 0 and cols > 0:
        for c in range(cols):
            border_cells.add((0, c))
            border_cells.add((rows - 1, c))
        for r in range(rows):
            border_cells.add((r, 0))
            border_cells.add((r, cols - 1))

    for r, c in border_cells:
        if valid[r, c] and not visited[r, c]:
            visited[r, c] = True
            filled[r, c] = z[r, c]
            heapq.heappush(heap, (z[r, c], r, c))

    while heap:
        elev, r, c = heapq.heappop(heap)
        for dr, dc in _NEIGHBOR_OFFSETS_8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and valid[nr, nc] and not visited[nr, nc]:
                visited[nr, nc] = True
                nz = max(z[nr, nc], elev)
                filled[nr, nc] = nz
                heapq.heappush(heap, (nz, nr, nc))

    return filled


def compute_flow_direction(
    filled_elevation: np.ndarray,
    pixel_size_x: float,
    pixel_size_y: float,
    nodata: float | None = None,
) -> np.ndarray:
    """D8 flow direction from a filled elevation surface. Each cell points to
    whichever of its 8 neighbors has the steepest descent (elevation drop
    per real-world distance); ties keep the first direction checked.

    Returns an int16 raster of ESRI-style codes (see FLOW_DIRECTION_CODES),
    with FLOW_DIRECTION_UNDETERMINED (0) for edge/flat/no-downhill-neighbor
    cells and FLOW_DIRECTION_NODATA (-9999) for nodata input cells. ``NaN``
    in the input (as ``fill_depressions`` produces for cells it couldn't
    reach) is always treated as nodata too, regardless of ``nodata``.
    """
    if filled_elevation.ndim != 2:
        raise ValueError("filled_elevation must be a 2D array")
    if pixel_size_x <= 0 or pixel_size_y <= 0:
        raise ValueError("pixel sizes must be positive")

    rows, cols = filled_elevation.shape
    z = filled_elevation.astype("float64")
    is_nodata = np.isnan(z)
    if nodata is not None:
        is_nodata = is_nodata | np.isclose(z, nodata)

    direction = np.full((rows, cols), FLOW_DIRECTION_UNDETERMINED, dtype="int16")
    direction[is_nodata] = FLOW_DIRECTION_NODATA

    for r in range(rows):
        for c in range(cols):
            if is_nodata[r, c]:
                continue
            best_drop_per_dist = 0.0
            best_code = FLOW_DIRECTION_UNDETERMINED
            for code, dr, dc in _D8_DIRECTIONS:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols) or is_nodata[nr, nc]:
                    continue
                distance = ((dc * pixel_size_x) ** 2 + (dr * pixel_size_y) ** 2) ** 0.5
                drop_per_dist = (z[r, c] - z[nr, nc]) / distance
                if drop_per_dist > best_drop_per_dist:
                    best_drop_per_dist = drop_per_dist
                    best_code = code
            direction[r, c] = best_code

    return direction


def build_flow_graph(direction: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Given a D8 direction grid, returns ``(downstream, topo_order)``.

    ``downstream`` is a flat int64 array where ``downstream[i]`` is the
    flattened index of cell ``i``'s downstream target, or -1 if it has none
    (nodata, undetermined, or would flow off the grid edge).

    ``topo_order`` lists valid (non-nodata) cells' flat indices via Kahn's
    algorithm on the implied directed forest (each cell has at most one
    outgoing edge, and no cycles are possible since flow direction always
    requires a strict elevation drop) such that every cell appears *before*
    its downstream target. Used forwards for flow accumulation and in
    reverse for HAND (see app/services/hazard_flood.py) — one sort, two
    consumers.
    """
    rows, cols = direction.shape
    size = rows * cols
    flat_direction = direction.ravel()
    valid_mask = flat_direction != FLOW_DIRECTION_NODATA

    code_to_delta = {code: (dr, dc) for code, dr, dc in _D8_DIRECTIONS}
    downstream = np.full(size, -1, dtype="int64")
    in_degree = np.zeros(size, dtype="int64")

    for idx in np.flatnonzero(valid_mask):
        code = int(flat_direction[idx])
        if code == FLOW_DIRECTION_UNDETERMINED:
            continue
        dr, dc = code_to_delta[code]
        r, c = divmod(int(idx), cols)
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            target = nr * cols + nc
            if valid_mask[target]:
                downstream[idx] = target
                in_degree[target] += 1

    remaining_in_degree = in_degree.copy()
    queue: deque[int] = deque(
        int(i) for i in np.flatnonzero(valid_mask) if in_degree[i] == 0
    )
    topo_order: list[int] = []

    while queue:
        idx = queue.popleft()
        topo_order.append(idx)
        target = int(downstream[idx])
        if target != -1:
            remaining_in_degree[target] -= 1
            if remaining_in_degree[target] == 0:
                queue.append(target)

    return downstream, topo_order


def compute_flow_accumulation(direction: np.ndarray) -> np.ndarray:
    """D8 flow accumulation: number of cells (including itself) draining
    through each cell, via topological propagation over the flow graph.
    Cell-count convention (not an area or discharge estimate — deliberately
    not converted to one, since that would imply a precision this system
    doesn't have). NaN for nodata cells.
    """
    rows, cols = direction.shape
    size = rows * cols
    downstream, topo_order = build_flow_graph(direction)

    valid_mask = direction.ravel() != FLOW_DIRECTION_NODATA
    accumulation = np.full(size, np.nan, dtype="float64")
    accumulation[valid_mask] = 1.0

    for idx in topo_order:
        target = int(downstream[idx])
        if target != -1:
            accumulation[target] += accumulation[idx]

    return accumulation.reshape(rows, cols)


@dataclass
class ProjectedRaster:
    data: np.ndarray
    transform: Any
    crs: Any
    pixel_size_x: float
    pixel_size_y: float
    nodata: float | None
    width: int
    height: int
    reprojected: bool
    source_crs: Any


def load_projected_elevation(source_path: Path) -> ProjectedRaster:
    """Opens a DEM/DSM GeoTIFF and reprojects it to a metric CRS if it's
    geographic (degrees) — auto-selecting a UTM zone from the raster's
    centroid via pyproj. Deterministic: the same source always produces the
    same output grid, which is what lets independently derived products
    (slope, flow_direction, flow_accumulation, ...) of the same DEM share an
    identical grid with no extra alignment step.

    Limitation: a single UTM zone is assumed adequate for the raster's
    extent — fine for local/regional DEMs, not for continental-scale rasters
    spanning multiple zones.
    """
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    with rasterio.open(source_path) as src:
        source_crs = src.crs
        if source_crs is None:
            raise ValueError("Source raster has no CRS; cannot derive terrain products.")

        reprojected = bool(source_crs.is_geographic)

        if reprojected:
            target_crs = pick_utm_crs(tuple(src.bounds), source_crs)
            transform, width, height = calculate_default_transform(
                source_crs, target_crs, src.width, src.height, *src.bounds
            )
            fill_value = src.nodata if src.nodata is not None else 0.0
            data = np.full((height, width), fill_value, dtype="float64")
            reproject(
                source=rasterio.band(src, 1),
                destination=data,
                src_transform=src.transform,
                src_crs=source_crs,
                dst_transform=transform,
                dst_crs=target_crs,
                resampling=Resampling.bilinear,
                dst_nodata=src.nodata,
            )
            pixel_size_x = transform.a
            pixel_size_y = -transform.e
            nodata = src.nodata
            out_transform = transform
            out_crs = target_crs
            out_width, out_height = width, height
        else:
            data = src.read(1).astype("float64")
            pixel_size_x = src.transform.a
            pixel_size_y = -src.transform.e
            nodata = src.nodata
            out_transform = src.transform
            out_crs = source_crs
            out_width, out_height = src.width, src.height

    return ProjectedRaster(
        data=data,
        transform=out_transform,
        crs=out_crs,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        nodata=nodata,
        width=out_width,
        height=out_height,
        reprojected=reprojected,
        source_crs=source_crs,
    )


def pick_utm_crs(bounds: tuple[float, float, float, float], source_crs: Any) -> Any:
    """Auto-selects a UTM CRS from a raster's extent (via its centroid).
    Public: reused by app/services/exposure.py (Phase 6) for reprojecting a
    geographic-CRS hazard raster before area/length computation, not just
    for DEMs.
    """
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_utm_crs_info
    from rasterio.crs import CRS
    from rasterio.warp import transform_bounds

    if source_crs.is_geographic:
        lon_min, lat_min, lon_max, lat_max = bounds
    else:
        lon_min, lat_min, lon_max, lat_max = transform_bounds(source_crs, "EPSG:4326", *bounds)

    utm_candidates = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(
            west_lon_degree=lon_min,
            south_lat_degree=lat_min,
            east_lon_degree=lon_max,
            north_lat_degree=lat_max,
        ),
    )
    if not utm_candidates:
        raise ValueError("Could not determine a UTM zone for this raster's extent")
    return CRS.from_epsg(int(utm_candidates[0].code))


@dataclass
class DerivationResult:
    output_path: Path
    crs: str
    bbox: tuple[float, float, float, float]
    metadata: dict


def derive_terrain_product(source_path: Path, product: str, output_path: Path) -> DerivationResult:
    """Reads a DEM/DSM GeoTIFF, reprojects to a metric CRS if needed, computes
    the requested product, and writes the result as a new single-band
    GeoTIFF. Supported products: slope, aspect, flow_direction,
    flow_accumulation.
    """
    if product not in ("slope", "aspect", "flow_direction", "flow_accumulation"):
        raise ValueError(f"Unknown product '{product}'")

    import rasterio
    from rasterio.transform import array_bounds

    projected = load_projected_elevation(source_path)
    method = "horn"
    extra_metadata: dict[str, Any] = {}

    if product in ("slope", "aspect"):
        slope, aspect = compute_slope_aspect(
            projected.data, projected.pixel_size_x, projected.pixel_size_y, projected.nodata
        )
        raw = slope if product == "slope" else aspect
        output_array = np.where(np.isnan(raw), DERIVED_NODATA, raw).astype("float32")
        output_dtype = "float32"
        output_nodata: float = DERIVED_NODATA
    else:
        filled = fill_depressions(projected.data, projected.nodata)
        direction = compute_flow_direction(
            filled, projected.pixel_size_x, projected.pixel_size_y, projected.nodata
        )
        method = "d8_priority_flood"
        extra_metadata["flow_direction_codes"] = {
            **FLOW_DIRECTION_CODES,
            "undetermined": FLOW_DIRECTION_UNDETERMINED,
        }

        if product == "flow_direction":
            output_array = direction.astype("int16")
            output_dtype = "int16"
            output_nodata = FLOW_DIRECTION_NODATA
        else:
            accumulation = compute_flow_accumulation(direction)
            output_array = np.where(np.isnan(accumulation), DERIVED_NODATA, accumulation).astype("float32")
            output_dtype = "float32"
            output_nodata = DERIVED_NODATA

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=projected.height,
        width=projected.width,
        count=1,
        dtype=output_dtype,
        crs=projected.crs,
        transform=projected.transform,
        nodata=output_nodata,
    ) as dst:
        dst.write(output_array, 1)

    bbox = array_bounds(projected.height, projected.width, projected.transform)

    return DerivationResult(
        output_path=output_path,
        crs=projected.crs.to_string(),
        bbox=tuple(bbox),
        metadata={
            "product": product,
            "method": method,
            "reprojected": projected.reprojected,
            "source_crs": projected.source_crs.to_string(),
            "pixel_size_x": projected.pixel_size_x,
            "pixel_size_y": projected.pixel_size_y,
            "width": projected.width,
            "height": projected.height,
            "nodata": output_nodata,
            **extra_metadata,
        },
    )
