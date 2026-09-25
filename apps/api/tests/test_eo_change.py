"""Pure-math tests for Phase 5 EO change detection: CVA, normalized
difference, and Otsu automatic thresholding — verified against known
analytic cases and general invariants.
"""

import numpy as np
import pytest

from app.services.eo_change import (
    compute_change_vector_magnitude,
    compute_normalized_difference_change,
    compute_normalized_difference_index,
    compute_otsu_threshold,
)


# --- Change Vector Analysis ----------------------------------------------


def test_cva_identical_images_have_zero_magnitude() -> None:
    before = np.full((3, 4, 4), 50.0)
    after = before.copy()
    magnitude = compute_change_vector_magnitude(before, after)
    assert np.allclose(magnitude, 0.0)


def test_cva_single_band_known_delta() -> None:
    before = np.full((1, 3, 3), 10.0)
    after = before.copy()
    after[0, 1, 1] = 25.0  # a single pixel changes by 15
    magnitude = compute_change_vector_magnitude(before, after)

    expected = np.zeros((3, 3))
    expected[1, 1] = 15.0
    assert np.allclose(magnitude, expected)


def test_cva_multiband_matches_euclidean_norm() -> None:
    before = np.zeros((3, 1, 1))
    after = np.array([[[3.0]], [[4.0]], [[0.0]]])  # diff vector (3,4,0) -> magnitude 5
    magnitude = compute_change_vector_magnitude(before, after)
    assert magnitude[0, 0] == pytest.approx(5.0)


def test_cva_nodata_in_either_image_propagates() -> None:
    before = np.full((1, 2, 2), 10.0)
    after = np.full((1, 2, 2), 20.0)
    before[0, 0, 0] = -9999.0
    magnitude = compute_change_vector_magnitude(before, after, nodata_before=-9999.0)
    assert np.isnan(magnitude[0, 0])
    assert not np.isnan(magnitude[0, 1])

    after[0, 1, 1] = -9999.0
    magnitude = compute_change_vector_magnitude(
        before, after, nodata_before=-9999.0, nodata_after=-9999.0
    )
    assert np.isnan(magnitude[0, 0])
    assert np.isnan(magnitude[1, 1])
    assert not np.isnan(magnitude[0, 1])


def test_cva_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        compute_change_vector_magnitude(np.zeros((2, 3, 3)), np.zeros((3, 3, 3)))


def test_cva_rejects_non_3d_input() -> None:
    with pytest.raises(ValueError):
        compute_change_vector_magnitude(np.zeros((3, 3)), np.zeros((3, 3)))


# --- normalized difference -------------------------------------------------


def test_normalized_difference_index_known_values() -> None:
    a = np.array([[0.8, 0.2]])
    b = np.array([[0.2, 0.8]])
    index = compute_normalized_difference_index(a, b)
    # (0.8-0.2)/(0.8+0.2) = 0.6 ; (0.2-0.8)/(0.2+0.8) = -0.6
    assert np.allclose(index, [[0.6, -0.6]])


def test_normalized_difference_index_guards_divide_by_zero() -> None:
    a = np.array([[1.0, 0.0]])
    b = np.array([[-1.0, 0.0]])  # a+b == 0 for both cells
    index = compute_normalized_difference_index(a, b)
    assert np.all(np.isnan(index))


def test_normalized_difference_index_respects_nodata() -> None:
    a = np.array([[-9999.0, 5.0]])
    b = np.array([[3.0, 2.0]])
    index = compute_normalized_difference_index(a, b, nodata=-9999.0)
    assert np.isnan(index[0, 0])
    assert not np.isnan(index[0, 1])


def test_normalized_difference_change_no_change_when_identical() -> None:
    before_a = np.array([[0.7]])
    before_b = np.array([[0.3]])
    change = compute_normalized_difference_change(before_a, before_b, before_a.copy(), before_b.copy())
    assert change[0, 0] == pytest.approx(0.0)


def test_normalized_difference_change_known_shift() -> None:
    before_a, before_b = np.array([[0.5]]), np.array([[0.5]])  # index = 0
    after_a, after_b = np.array([[0.9]]), np.array([[0.1]])  # index = 0.8
    change = compute_normalized_difference_change(before_a, before_b, after_a, after_b)
    assert change[0, 0] == pytest.approx(0.8)


def test_normalized_difference_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        compute_normalized_difference_index(np.zeros((2, 2)), np.zeros((3, 3)))


# --- Otsu automatic thresholding -------------------------------------------


def test_otsu_separates_bimodal_distribution() -> None:
    # Otsu's real guarantee is near-perfect separation, not a threshold at
    # the gap's exact midpoint or boundary: ties in an empty histogram gap
    # are broken by picking the first bin, and the returned threshold is a
    # bin *center* (histogram discretization), so it can land a hair inside
    # one cluster. Assert separation is effectively complete rather than an
    # exact boundary.
    rng = np.random.default_rng(0)
    low = rng.normal(loc=1.0, scale=0.1, size=500)
    high = rng.normal(loc=9.0, scale=0.1, size=500)
    values = np.concatenate([low, high])

    threshold = compute_otsu_threshold(values)
    misclassified = np.count_nonzero(low >= threshold) + np.count_nonzero(high < threshold)
    assert misclassified / values.size < 0.01


def test_otsu_rejects_zero_variation() -> None:
    values = np.full(100, 5.0)
    with pytest.raises(ValueError):
        compute_otsu_threshold(values)


def test_otsu_rejects_all_nonfinite_input() -> None:
    values = np.full(10, np.nan)
    with pytest.raises(ValueError):
        compute_otsu_threshold(values)


def test_otsu_ignores_nonfinite_values_when_mixed() -> None:
    rng = np.random.default_rng(1)
    low = rng.normal(loc=0.0, scale=0.05, size=200)
    high = rng.normal(loc=5.0, scale=0.05, size=200)
    values = np.concatenate([low, high, [np.nan, np.inf]])
    threshold = compute_otsu_threshold(values)
    misclassified = np.count_nonzero(low >= threshold) + np.count_nonzero(high < threshold)
    assert misclassified / (low.size + high.size) < 0.01
