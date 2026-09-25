"""Pure-math tests for HAND computation — isolating the classification step
from DEM/flow computation by hand-building small direction/accumulation
grids directly, plus a couple of tests built from the real terrain.py flow
functions for an end-to-end analytic case.
"""

import numpy as np
import pytest

from app.services.terrain import (
    FLOW_DIRECTION_CODES,
    compute_flow_accumulation,
    compute_flow_direction,
    fill_depressions,
)
from app.services.hazard_flood import compute_hand


def test_hand_exact_for_simple_ramp_to_known_channel() -> None:
    # Linear chain: column of 5 cells, each flows to the one below it.
    # elevation: 40, 30, 20, 10, 0 (top to bottom). accumulation: 1,2,3,4,5.
    # With channel_threshold_cells=4, only the bottom two cells (accum 4, 5)
    # are channel cells; the bottom-most (accum=5, elevation=0) is the
    # "deepest" channel cell reached.
    rows = 5
    elevation = np.array([[40.0], [30.0], [20.0], [10.0], [0.0]])
    direction = np.full((rows, 1), FLOW_DIRECTION_CODES["S"], dtype="int16")
    direction[-1, 0] = 0  # bottom cell: no downstream (it's the outlet)
    accumulation = np.array([[1.0], [2.0], [3.0], [4.0], [5.0]])

    hand = compute_hand(elevation, direction, accumulation, channel_threshold_cells=4.0)

    # Channel cells (accum >= 4): HAND = 0 (their own elevation minus itself)
    assert hand[3, 0] == pytest.approx(0.0)
    assert hand[4, 0] == pytest.approx(0.0)
    # Row 2 (accum=3, elevation=20) flows to row 3 (accum=4, elevation=10,
    # first channel cell reached) -> HAND = 20 - 10 = 10
    assert hand[2, 0] == pytest.approx(10.0)
    # Row 1 (elevation=30) also reaches the same first channel cell (row 3, elevation=10)
    assert hand[1, 0] == pytest.approx(20.0)
    assert hand[0, 0] == pytest.approx(30.0)


def test_hand_is_nonnegative_wherever_defined() -> None:
    rng = np.random.default_rng(3)
    rows, cols = 10, 10
    z = rng.uniform(0, 100, size=(rows, cols))
    filled = fill_depressions(z)
    direction = compute_flow_direction(filled, pixel_size_x=1.0, pixel_size_y=1.0)
    accumulation = compute_flow_accumulation(direction)

    hand = compute_hand(z, direction, accumulation, channel_threshold_cells=5.0)
    defined = ~np.isnan(hand)
    assert np.all(hand[defined] >= -1e-9)


def test_hand_undefined_when_no_channel_reached() -> None:
    # Every cell in a tiny basin with a very high channel threshold never
    # reaches a channel -> HAND undefined everywhere.
    rows = 4
    elevation = np.array([[30.0], [20.0], [10.0], [0.0]])
    direction = np.full((rows, 1), FLOW_DIRECTION_CODES["S"], dtype="int16")
    direction[-1, 0] = 0
    accumulation = np.array([[1.0], [2.0], [3.0], [4.0]])

    hand = compute_hand(elevation, direction, accumulation, channel_threshold_cells=1000.0)
    assert np.all(np.isnan(hand))


def test_hand_rejects_mismatched_shapes() -> None:
    elevation = np.zeros((3, 3))
    direction = np.zeros((3, 3), dtype="int16")
    accumulation = np.zeros((2, 2))
    with pytest.raises(ValueError):
        compute_hand(elevation, direction, accumulation, channel_threshold_cells=1.0)


def test_hand_rejects_non_positive_threshold() -> None:
    elevation = np.zeros((3, 3))
    direction = np.zeros((3, 3), dtype="int16")
    accumulation = np.zeros((3, 3))
    with pytest.raises(ValueError):
        compute_hand(elevation, direction, accumulation, channel_threshold_cells=0.0)


def test_inundation_classification_matches_hand_threshold() -> None:
    # Directly test the classification arithmetic used by run_flood_scenario
    # (replicated here at the array level, since run_flood_scenario itself
    # needs real files -- covered by the API-level tests).
    hand = np.array([[0.0, 1.0, 2.5], [np.nan, 5.0, 10.0]])
    depth_above_drainage_m = 2.0

    inundated = (~np.isnan(hand)) & (hand < depth_above_drainage_m)
    depth = np.where(inundated, depth_above_drainage_m - hand, np.nan)

    assert inundated.tolist() == [[True, True, False], [False, False, False]]
    assert depth[0, 0] == pytest.approx(2.0)
    assert depth[0, 1] == pytest.approx(1.0)
    assert np.isnan(depth[0, 2])
    assert np.isnan(depth[1, 0])
