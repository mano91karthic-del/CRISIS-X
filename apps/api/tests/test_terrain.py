"""Pure-math tests for Horn's-method slope/aspect against known analytic
cases — verifying our own implementation against ground truth, not against
itself.
"""

import numpy as np
import pytest

from app.services.terrain import FLAT_ASPECT_SENTINEL, compute_slope_aspect


def test_flat_plane_has_zero_slope_and_flat_aspect() -> None:
    z = np.full((7, 7), 100.0)
    slope, aspect = compute_slope_aspect(z, pixel_size_x=1.0, pixel_size_y=1.0)

    interior_slope = slope[1:-1, 1:-1]
    interior_aspect = aspect[1:-1, 1:-1]

    assert np.allclose(interior_slope, 0.0)
    assert np.all(interior_aspect == FLAT_ASPECT_SENTINEL)


@pytest.mark.parametrize("gradient", [0.5, 1.0, 2.0])
def test_north_south_ramp_matches_analytic_slope(gradient: float) -> None:
    # Elevation increases with row index -> south is higher -> downhill is
    # north -> aspect should be ~0 degrees (north).
    rows, cols = 9, 9
    z = np.fromfunction(lambda r, c: r * gradient, (rows, cols))

    slope, aspect = compute_slope_aspect(z, pixel_size_x=1.0, pixel_size_y=1.0)

    interior_slope = slope[1:-1, 1:-1]
    interior_aspect = aspect[1:-1, 1:-1]

    expected_slope_deg = np.degrees(np.arctan(gradient))
    assert np.allclose(interior_slope, expected_slope_deg, atol=1e-9)
    assert np.allclose(interior_aspect, 0.0, atol=1e-6)


def test_east_west_ramp_faces_east() -> None:
    # Elevation decreases going east -> east is lower -> downhill is east ->
    # aspect should be ~90 degrees (east).
    rows, cols = 9, 9
    gradient = 1.0
    z = np.fromfunction(lambda r, c: -c * gradient, (rows, cols))

    slope, aspect = compute_slope_aspect(z, pixel_size_x=1.0, pixel_size_y=1.0)

    interior_slope = slope[1:-1, 1:-1]
    interior_aspect = aspect[1:-1, 1:-1]

    expected_slope_deg = np.degrees(np.arctan(gradient))
    assert np.allclose(interior_slope, expected_slope_deg, atol=1e-9)
    assert np.allclose(interior_aspect, 90.0, atol=1e-6)


def test_border_cells_are_nan() -> None:
    z = np.arange(49, dtype="float64").reshape(7, 7)
    slope, aspect = compute_slope_aspect(z, pixel_size_x=1.0, pixel_size_y=1.0)

    assert np.all(np.isnan(slope[0, :]))
    assert np.all(np.isnan(slope[-1, :]))
    assert np.all(np.isnan(slope[:, 0]))
    assert np.all(np.isnan(slope[:, -1]))
    assert np.all(np.isnan(aspect[0, :]))


def test_nodata_neighborhood_propagates_as_nan() -> None:
    z = np.full((7, 7), 100.0)
    z[3, 3] = -9999.0  # a single nodata cell in the middle

    slope, aspect = compute_slope_aspect(z, pixel_size_x=1.0, pixel_size_y=1.0, nodata=-9999.0)

    # Every cell whose 3x3 neighborhood includes (3,3) must be NaN, i.e. rows
    # 2-4, cols 2-4 in the interior.
    assert np.all(np.isnan(slope[2:5, 2:5]))
    assert np.all(np.isnan(aspect[2:5, 2:5]))
    # A cell far away from the nodata cell should still compute cleanly.
    assert not np.isnan(slope[1, 1])
    assert slope[1, 1] == 0.0


def test_rejects_non_2d_array() -> None:
    with pytest.raises(ValueError):
        compute_slope_aspect(np.zeros(9), pixel_size_x=1.0, pixel_size_y=1.0)


def test_rejects_non_positive_pixel_size() -> None:
    z = np.zeros((5, 5))
    with pytest.raises(ValueError):
        compute_slope_aspect(z, pixel_size_x=0.0, pixel_size_y=1.0)
