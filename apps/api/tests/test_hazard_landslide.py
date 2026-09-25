import numpy as np
import pytest

from app.services.hazard_landslide import DEFAULT_SLOPE_BREAKPOINTS_DEG, classify_slope


def test_classify_slope_matches_expected_classes() -> None:
    slope = np.array([[0.0, 4.9, 5.0, 14.9], [15.0, 24.9, 25.0, 34.9], [35.0, 90.0, 0.0, 0.0]])
    classes = classify_slope(slope, DEFAULT_SLOPE_BREAKPOINTS_DEG)

    expected = np.array([[1, 1, 2, 2], [3, 3, 4, 4], [5, 5, 1, 1]], dtype="float64")
    assert np.array_equal(classes, expected)


def test_classify_slope_boundary_values_go_to_higher_class() -> None:
    # Exactly-on-breakpoint values fall into the class above, not below.
    slope = np.array([[5.0, 15.0, 25.0, 35.0]])
    classes = classify_slope(slope, DEFAULT_SLOPE_BREAKPOINTS_DEG)
    assert classes.tolist() == [[2.0, 3.0, 4.0, 5.0]]


def test_classify_slope_nodata_and_nan_become_nan() -> None:
    slope = np.array([[10.0, -9999.0, np.nan]])
    classes = classify_slope(slope, DEFAULT_SLOPE_BREAKPOINTS_DEG, nodata=-9999.0)
    assert not np.isnan(classes[0, 0])
    assert np.isnan(classes[0, 1])
    assert np.isnan(classes[0, 2])


def test_classify_slope_custom_breakpoints() -> None:
    slope = np.array([[1.0, 50.0]])
    classes = classify_slope(slope, (10.0, 20.0, 30.0, 40.0))
    assert classes.tolist() == [[1.0, 5.0]]


def test_classify_slope_rejects_wrong_breakpoint_count() -> None:
    with pytest.raises(ValueError):
        classify_slope(np.zeros((2, 2)), (1.0, 2.0, 3.0))


def test_classify_slope_rejects_non_ascending_breakpoints() -> None:
    with pytest.raises(ValueError):
        classify_slope(np.zeros((2, 2)), (5.0, 15.0, 10.0, 35.0))


def test_classify_slope_rejects_non_2d() -> None:
    with pytest.raises(ValueError):
        classify_slope(np.zeros(4), DEFAULT_SLOPE_BREAKPOINTS_DEG)
