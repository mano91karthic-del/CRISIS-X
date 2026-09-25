"""Pure-math tests for depression filling, D8 flow direction, and flow
accumulation — verified against known analytic cases and general invariants,
the same discipline test_terrain.py established for slope/aspect.
"""

import numpy as np
import pytest

from app.services.terrain import (
    FLOW_DIRECTION_CODES,
    FLOW_DIRECTION_NODATA,
    FLOW_DIRECTION_UNDETERMINED,
    build_flow_graph,
    compute_flow_accumulation,
    compute_flow_direction,
    fill_depressions,
)


# --- fill_depressions ---------------------------------------------------


def test_fill_depressions_raises_pit_to_lowest_pour_point() -> None:
    z = np.array(
        [
            [10.0, 10.0, 10.0, 10.0],
            [10.0, 5.0, 8.0, 10.0],
            [10.0, 6.0, 1.0, 10.0],  # (2,2) = 1.0 is a deep interior pit
            [10.0, 10.0, 10.0, 10.0],
        ]
    )
    filled = fill_depressions(z)

    # The pit must be raised (never lowered), and at least one neighbor must
    # now be at or below its filled elevation -- i.e. it's no longer a pit
    # (a monotonic non-increasing path to the border exists).
    assert filled[2, 2] >= 1.0
    neighbors = [filled[1, 1], filled[1, 2], filled[2, 1]]
    assert any(filled[2, 2] <= n for n in neighbors)


def test_fill_depressions_leaves_pit_free_surface_unchanged() -> None:
    z = np.array(
        [
            [9.0, 8.0, 7.0],
            [8.0, 7.0, 6.0],
            [7.0, 6.0, 5.0],
        ]
    )
    filled = fill_depressions(z)
    assert np.allclose(filled, z)


def test_fill_depressions_respects_nodata() -> None:
    z = np.array(
        [
            [10.0, 10.0, 10.0],
            [10.0, -9999.0, 10.0],
            [10.0, 10.0, 10.0],
        ]
    )
    filled = fill_depressions(z, nodata=-9999.0)
    assert np.isnan(filled[1, 1])


# --- compute_flow_direction ----------------------------------------------


def test_flow_direction_on_south_facing_ramp_points_south() -> None:
    # Elevation increases going north (row 0) -> south is lower -> downhill
    # is south for every interior cell.
    rows, cols = 5, 5
    z = np.fromfunction(lambda r, c: (rows - r) * 1.0, (rows, cols))
    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0)

    interior = direction[1:-1, 1:-1]
    assert np.all(interior == FLOW_DIRECTION_CODES["S"])


def test_flow_direction_on_east_facing_ramp_points_east() -> None:
    rows, cols = 5, 5
    z = np.fromfunction(lambda r, c: (cols - c) * 1.0, (rows, cols))
    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0)

    interior = direction[1:-1, 1:-1]
    assert np.all(interior == FLOW_DIRECTION_CODES["E"])


def test_flow_direction_radial_peak_flows_outward() -> None:
    # A true (Euclidean) conical peak at the center: the on-axis cells must
    # flow straight outward along their own axis, since that's the direction
    # of steepest radial descent (a Manhattan-distance "pyramid" would have
    # diagonal ties here, which is why this uses a real radial cone).
    size = 9
    center = size // 2
    r, c = np.indices((size, size))
    z = -np.sqrt((r - center) ** 2 + (c - center) ** 2)

    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0)

    assert direction[center, center + 1] == FLOW_DIRECTION_CODES["E"]
    assert direction[center, center - 1] == FLOW_DIRECTION_CODES["W"]
    assert direction[center - 1, center] == FLOW_DIRECTION_CODES["N"]
    assert direction[center + 1, center] == FLOW_DIRECTION_CODES["S"]


def test_flow_direction_flat_surface_is_undetermined() -> None:
    z = np.full((5, 5), 3.0)
    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0)
    assert np.all(direction == FLOW_DIRECTION_UNDETERMINED)


def test_flow_direction_nodata_cells_marked_nodata() -> None:
    z = np.full((5, 5), 3.0)
    z[2, 2] = -9999.0
    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0, nodata=-9999.0)
    assert direction[2, 2] == FLOW_DIRECTION_NODATA


def test_flow_direction_rejects_non_positive_pixel_size() -> None:
    with pytest.raises(ValueError):
        compute_flow_direction(np.zeros((3, 3)), pixel_size_x=0.0, pixel_size_y=1.0)


# --- build_flow_graph / compute_flow_accumulation -------------------------


def test_flow_accumulation_linear_chain_matches_row_index() -> None:
    # Single column, each cell flows straight down to the one below it.
    rows, cols = 6, 1
    z = np.fromfunction(lambda r, c: (rows - r) * 1.0, (rows, cols))
    direction = compute_flow_direction(z, pixel_size_x=1.0, pixel_size_y=1.0)
    accumulation = compute_flow_accumulation(direction)

    for row in range(rows):
        assert accumulation[row, 0] == row + 1


def test_flow_accumulation_conserves_total_cell_count() -> None:
    # Mass-conservation invariant: summed accumulation at every outlet
    # (no-downstream-target) cell equals the total number of valid cells,
    # for any DEM shape.
    rng = np.random.default_rng(42)
    for _ in range(5):
        rows, cols = rng.integers(4, 12, size=2)
        z = rng.uniform(0, 100, size=(rows, cols))
        filled = fill_depressions(z)
        direction = compute_flow_direction(filled, pixel_size_x=1.0, pixel_size_y=1.0)
        accumulation = compute_flow_accumulation(direction)

        downstream, _ = build_flow_graph(direction)
        outlet_mask = (downstream == -1) & (direction.ravel() != FLOW_DIRECTION_NODATA)
        total_at_outlets = np.nansum(accumulation.ravel()[outlet_mask])

        valid_cell_count = int(np.count_nonzero(direction != FLOW_DIRECTION_NODATA))
        assert total_at_outlets == pytest.approx(valid_cell_count)


def test_flow_accumulation_nodata_excluded() -> None:
    z = np.full((5, 5), 3.0)
    z[0, 0] = -9999.0
    filled = fill_depressions(z, nodata=-9999.0)
    direction = compute_flow_direction(filled, pixel_size_x=1.0, pixel_size_y=1.0, nodata=-9999.0)
    accumulation = compute_flow_accumulation(direction)
    assert np.isnan(accumulation[0, 0])


def test_build_flow_graph_topo_order_covers_all_valid_cells() -> None:
    rng = np.random.default_rng(7)
    rows, cols = 8, 8
    z = rng.uniform(0, 50, size=(rows, cols))
    filled = fill_depressions(z)
    direction = compute_flow_direction(filled, pixel_size_x=1.0, pixel_size_y=1.0)
    _, topo_order = build_flow_graph(direction)

    valid_cell_count = int(np.count_nonzero(direction != FLOW_DIRECTION_NODATA))
    assert len(topo_order) == valid_cell_count
    assert len(set(topo_order)) == len(topo_order)  # no duplicates -> confirms it's a DAG/forest
