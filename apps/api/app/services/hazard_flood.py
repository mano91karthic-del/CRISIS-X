"""Phase 4: HAND (Height Above Nearest Drainage) flood screening.

Not a hydraulic simulation. This is a scenario/screening tool, not a
deterministic prediction. `depth_above_drainage_m` is always a direct,
user-supplied scenario assumption — it is never derived from rainfall or
any rainfall-runoff model. See
docs/architecture/0005-phase-4-hazard-engine.md for the full methodology
and reasoning.

The pure math (`compute_hand`) is separated from raster I/O
(`run_flood_scenario`) so it's testable against known cases without files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.services.terrain import (
    DERIVED_NODATA,
    FLOW_DIRECTION_NODATA,
    build_flow_graph,
    load_projected_elevation,
)

DEFAULT_CHANNEL_THRESHOLD_CELLS = 50.0

FLOOD_LIMITATIONS = [
    "This is a HAND (Height Above Nearest Drainage) based flood screening, not a hydraulic simulation.",
    "Assumes spatially uniform depth above drainage; does not model flow velocity, duration, or channel conveyance.",
    "Depression filling uses a simplified priority-flood algorithm; flat-area flow routing may be approximate.",
    "depth_above_drainage_m is a direct, user-supplied scenario assumption, not derived from rainfall "
    "or any rainfall-runoff model.",
    "This is a modeled scenario/screening result, not a guaranteed deterministic prediction of flooding.",
]


def compute_hand(
    elevation: np.ndarray,
    direction: np.ndarray,
    accumulation: np.ndarray,
    channel_threshold_cells: float,
) -> np.ndarray:
    """HAND(cell) = elevation(cell) - elevation(nearest downstream channel
    cell), walked along the D8 flow path. A "channel" cell is one whose flow
    accumulation meets ``channel_threshold_cells``. Uses the *original*
    (unfilled) elevation for the subtraction — filling is for flow-routing
    topology only and would distort real depth-above-channel values.

    Returns NaN where undefined: nodata cells, or cells whose flow path
    never reaches a channel cell before the outlet/edge (small basin, or a
    channel_threshold_cells set too high for this DEM).
    """
    if elevation.ndim != 2:
        raise ValueError("elevation must be a 2D array")
    if direction.shape != elevation.shape or accumulation.shape != elevation.shape:
        raise ValueError("elevation, direction, and accumulation rasters must have matching dimensions")
    if channel_threshold_cells <= 0:
        raise ValueError("channel_threshold_cells must be greater than zero")

    rows, cols = elevation.shape
    downstream, topo_order = build_flow_graph(direction)

    flat_elevation = elevation.astype("float64").ravel()
    flat_accumulation = accumulation.astype("float64").ravel()
    flat_direction = direction.ravel()

    is_channel = flat_accumulation >= channel_threshold_cells
    nearest_channel_elevation = np.full(flat_elevation.shape, np.nan, dtype="float64")

    # Reverse topological order: outlets first, ridges last -- so a cell's
    # downstream target (which appears later in forward topo_order, i.e.
    # earlier here) is always resolved before the cell itself.
    for idx in reversed(topo_order):
        if flat_direction[idx] == FLOW_DIRECTION_NODATA:
            continue
        if is_channel[idx]:
            nearest_channel_elevation[idx] = flat_elevation[idx]
        else:
            target = int(downstream[idx])
            if target != -1:
                nearest_channel_elevation[idx] = nearest_channel_elevation[target]
            # else: no downstream target and not itself a channel cell ->
            # this cell's basin never reaches a channel; stays NaN.

    hand = flat_elevation - nearest_channel_elevation
    return hand.reshape(rows, cols)


@dataclass
class FloodScenarioResult:
    output_path: Path
    crs: str
    bbox: tuple[float, float, float, float]
    metadata: dict[str, Any]


def run_flood_scenario(
    dem_path: Path,
    flow_direction_path: Path,
    flow_accumulation_path: Path,
    output_path: Path,
    *,
    depth_above_drainage_m: float,
    channel_threshold_cells: float = DEFAULT_CHANNEL_THRESHOLD_CELLS,
) -> FloodScenarioResult:
    if depth_above_drainage_m <= 0:
        raise ValueError("depth_above_drainage_m must be greater than zero")
    if channel_threshold_cells <= 0:
        raise ValueError("channel_threshold_cells must be greater than zero")

    import rasterio
    from rasterio.transform import array_bounds

    # Re-derives the DEM's own projected grid (deterministic -- see
    # load_projected_elevation) independently of reading the two
    # already-derived rasters below; all three land on an identical grid.
    projected = load_projected_elevation(dem_path)

    with rasterio.open(flow_direction_path) as dsrc:
        if (dsrc.height, dsrc.width) != (projected.height, projected.width):
            raise ValueError("flow_direction raster dimensions do not match the DEM's projected grid")
        direction = dsrc.read(1)

    with rasterio.open(flow_accumulation_path) as asrc:
        if (asrc.height, asrc.width) != (projected.height, projected.width):
            raise ValueError("flow_accumulation raster dimensions do not match the DEM's projected grid")
        accumulation_raw = asrc.read(1).astype("float64")
        accumulation = np.where(np.isclose(accumulation_raw, DERIVED_NODATA), np.nan, accumulation_raw)

    hand = compute_hand(projected.data, direction, accumulation, channel_threshold_cells)

    inundated = (~np.isnan(hand)) & (hand < depth_above_drainage_m)
    depth = np.where(inundated, depth_above_drainage_m - hand, np.nan)
    output_array = np.where(np.isnan(depth), DERIVED_NODATA, depth).astype("float32")

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=projected.height,
        width=projected.width,
        count=1,
        dtype="float32",
        crs=projected.crs,
        transform=projected.transform,
        nodata=DERIVED_NODATA,
    ) as dst:
        dst.write(output_array, 1)

    bbox = array_bounds(projected.height, projected.width, projected.transform)

    return FloodScenarioResult(
        output_path=output_path,
        crs=projected.crs.to_string(),
        bbox=tuple(bbox),
        metadata={
            "hazard_type": "flood",
            "method": "hand_screening",
            "depth_above_drainage_m": depth_above_drainage_m,
            "channel_threshold_cells": channel_threshold_cells,
            "inundated_cell_count": int(np.count_nonzero(inundated)),
            "hand_defined_cell_count": int(np.count_nonzero(~np.isnan(hand))),
            "crs": projected.crs.to_string(),
            "limitations": FLOOD_LIMITATIONS,
        },
    )
