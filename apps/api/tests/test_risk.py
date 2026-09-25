"""Pure-function tests for Phase 7 risk scoring: hazard-intensity weight
derivation, risk-score classification, the risk-score formula, per-class
breakdown building, and the per-feature risk join — verified against known
synthetic cases with hand-computable ground truth, same discipline as
test_exposure.py.
"""

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.services.risk import (
    HAZARD_CLASS_MAX_CODE,
    RISK_CLASS_LEGEND,
    attach_risk_to_features,
    build_risk_by_class,
    classify_risk_score,
    compute_hazard_intensity_weight,
    compute_risk_score,
    run_risk_analysis,
)

CRS = "EPSG:32643"


# --- compute_hazard_intensity_weight ----------------------------------------


def test_flood_hazard_intensity_weight_is_always_one() -> None:
    assert compute_hazard_intensity_weight("flood_inundation", 1) == pytest.approx(1.0)


def test_eo_change_mask_weights_no_change_zero_changed_one() -> None:
    assert compute_hazard_intensity_weight("eo_change_mask", 0) == pytest.approx(0.0)
    assert compute_hazard_intensity_weight("eo_change_mask", 1) == pytest.approx(1.0)


def test_landslide_hazard_intensity_weight_scales_by_fixed_max_class() -> None:
    # Anchored to the fixed 5-class scheme (HAZARD_CLASS_MAX_CODE), not the
    # classes present in any one analysis.
    assert compute_hazard_intensity_weight("landslide_susceptibility", 1) == pytest.approx(0.2)
    assert compute_hazard_intensity_weight("landslide_susceptibility", 2) == pytest.approx(0.4)
    assert compute_hazard_intensity_weight("landslide_susceptibility", 3) == pytest.approx(0.6)
    assert compute_hazard_intensity_weight("landslide_susceptibility", 4) == pytest.approx(0.8)
    assert compute_hazard_intensity_weight("landslide_susceptibility", 5) == pytest.approx(1.0)


def test_hazard_intensity_weight_rejects_unsupported_hazard_type() -> None:
    with pytest.raises(ValueError, match="Unsupported hazard_dataset_type"):
        compute_hazard_intensity_weight("eo_change_magnitude", 1)


def test_hazard_class_max_code_covers_exactly_the_supported_hazard_types() -> None:
    assert set(HAZARD_CLASS_MAX_CODE) == {"flood_inundation", "eo_change_mask", "landslide_susceptibility"}


# --- compute_risk_score ------------------------------------------------------


def test_risk_score_is_hand_computed_product() -> None:
    assert compute_risk_score(0.6, 0.5, 0.8) == pytest.approx(0.6 * 0.5 * 0.8)


def test_risk_score_is_zero_when_either_explicit_weight_is_zero() -> None:
    assert compute_risk_score(1.0, 0.0, 0.9) == pytest.approx(0.0)
    assert compute_risk_score(1.0, 0.9, 0.0) == pytest.approx(0.0)


def test_risk_score_is_zero_when_hazard_intensity_weight_is_zero() -> None:
    assert compute_risk_score(0.0, 1.0, 1.0) == pytest.approx(0.0)


def test_risk_score_is_max_when_all_factors_are_one() -> None:
    assert compute_risk_score(1.0, 1.0, 1.0) == pytest.approx(1.0)


# --- classify_risk_score ------------------------------------------------------


def test_classify_risk_score_boundary_falls_into_higher_class() -> None:
    breakpoints = (0.2, 0.4, 0.6, 0.8)
    # Left-inclusive intervals: a value exactly at a breakpoint falls above it.
    assert classify_risk_score(0.0, breakpoints) == "very_low"
    assert classify_risk_score(0.2, breakpoints) == "low"
    assert classify_risk_score(0.4, breakpoints) == "moderate"
    assert classify_risk_score(0.6, breakpoints) == "high"
    assert classify_risk_score(0.8, breakpoints) == "very_high"
    assert classify_risk_score(1.0, breakpoints) == "very_high"


def test_classify_risk_score_rejects_malformed_breakpoints() -> None:
    with pytest.raises(ValueError, match="exactly 4 values"):
        classify_risk_score(0.5, (0.2, 0.4, 0.6))
    with pytest.raises(ValueError, match="strictly ascending"):
        classify_risk_score(0.5, (0.4, 0.2, 0.6, 0.8))


def test_risk_class_legend_has_five_ordinal_labels() -> None:
    assert RISK_CLASS_LEGEND == {
        "1": "very_low", "2": "low", "3": "moderate", "4": "high", "5": "very_high",
    }


# --- build_risk_by_class ------------------------------------------------------


def test_build_risk_by_class_preserves_exposure_quantity_keys_unchanged() -> None:
    by_class = {
        "very_low": {"hazard_class_code": 1, "count": 7},
        "very_high": {"hazard_class_code": 5, "count": 3},
    }
    result = build_risk_by_class(
        by_class, "landslide_susceptibility", vulnerability_weight=0.5, consequence_weight=1.0
    )
    # Pre-existing exposure-quantity keys carried through unchanged.
    assert result["very_low"]["count"] == 7
    assert result["very_high"]["count"] == 3
    # New risk keys added, hand-computed.
    assert result["very_low"]["hazard_intensity_weight"] == pytest.approx(0.2)
    assert result["very_low"]["risk_score"] == pytest.approx(0.2 * 0.5 * 1.0)
    assert result["very_high"]["hazard_intensity_weight"] == pytest.approx(1.0)
    assert result["very_high"]["risk_score"] == pytest.approx(1.0 * 0.5 * 1.0)


def test_build_risk_by_class_empty_input_is_empty_output() -> None:
    assert build_risk_by_class({}, "flood_inundation", vulnerability_weight=1.0, consequence_weight=1.0) == {}


def test_build_risk_by_class_rejects_unsupported_hazard_type() -> None:
    by_class = {"changed": {"hazard_class_code": 1, "count": 1}}
    with pytest.raises(ValueError, match="Unsupported hazard_dataset_type"):
        build_risk_by_class(by_class, "eo_change_magnitude", vulnerability_weight=1.0, consequence_weight=1.0)


def test_build_risk_by_class_does_not_use_exposure_quantity_in_formula() -> None:
    # Two classes with identical hazard_class_code/weights but wildly
    # different exposure quantity (count) must get IDENTICAL risk_score --
    # proves risk_score is never scaled/normalized by exposure magnitude.
    by_class = {
        "a": {"hazard_class_code": 1, "count": 1},
        "b": {"hazard_class_code": 1, "count": 100_000},
    }
    result = build_risk_by_class(
        by_class, "eo_change_mask", vulnerability_weight=0.7, consequence_weight=0.4
    )
    assert result["a"]["risk_score"] == pytest.approx(result["b"]["risk_score"])
    assert result["a"]["risk_class"] == result["b"]["risk_class"]


# --- attach_risk_to_features ---------------------------------------------------


def test_attach_risk_to_features_joins_by_hazard_class_code() -> None:
    features = gpd.GeoDataFrame(
        {
            "hazard_class_code": [1, 5, 5],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs=CRS,
    )
    risk_by_class = {
        "very_low": {"hazard_class_code": 1, "risk_score": 0.1, "risk_class": "very_low"},
        "very_high": {"hazard_class_code": 5, "risk_score": 0.9, "risk_class": "very_high"},
    }
    result = attach_risk_to_features(features, risk_by_class)

    assert len(result) == 3  # row count unchanged
    assert result.iloc[0]["risk_score"] == pytest.approx(0.1)
    assert result.iloc[0]["risk_class"] == "very_low"
    assert result.iloc[1]["risk_score"] == pytest.approx(0.9)
    assert result.iloc[2]["risk_class"] == "very_high"
    # geometry column untouched
    assert result.geometry.iloc[0] == Point(0, 0)


# --- run_risk_analysis (pure orchestration) -------------------------------------


def test_run_risk_analysis_shape_and_limitations() -> None:
    exposure_results = {
        "hazard_dataset_type": "landslide_susceptibility",
        "by_class": {
            "very_low": {"hazard_class_code": 1, "count": 2},
            "very_high": {"hazard_class_code": 5, "count": 4},
        },
        "limitations": ["upstream exposure limitation"],
    }
    computation = run_risk_analysis(
        exposure_results,
        "landslide_susceptibility",
        vulnerability_weight=1.0,
        consequence_weight=1.0,
    )
    results = computation.results
    assert results["method"] == "hazard_vulnerability_consequence_product"
    assert results["hazard_dataset_type"] == "landslide_susceptibility"
    assert results["by_class"]["very_low"]["count"] == 2  # exposure quantity preserved
    assert results["by_class"]["very_high"]["risk_score"] == pytest.approx(1.0)
    assert "upstream exposure limitation" in results["limitations"]
    assert any("NOT weighted, scaled, or normalized by exposure quantity" in lim for lim in results["limitations"])
    assert computation.output_gdf is None  # no features_gdf supplied


def test_run_risk_analysis_empty_by_class_completes_with_empty_output() -> None:
    exposure_results = {"hazard_dataset_type": "flood_inundation", "by_class": {}, "limitations": []}
    computation = run_risk_analysis(
        exposure_results, "flood_inundation", vulnerability_weight=0.5, consequence_weight=0.5
    )
    assert computation.results["by_class"] == {}
    assert computation.output_gdf is None


def test_run_risk_analysis_joins_features_when_supplied() -> None:
    exposure_results = {
        "hazard_dataset_type": "eo_change_mask",
        "by_class": {"changed": {"hazard_class_code": 1, "count": 1}},
        "limitations": [],
    }
    features = gpd.GeoDataFrame({"hazard_class_code": [1], "geometry": [Point(0, 0)]}, crs=CRS)
    computation = run_risk_analysis(
        exposure_results,
        "eo_change_mask",
        vulnerability_weight=1.0,
        consequence_weight=1.0,
        features_gdf=features,
    )
    assert computation.output_gdf is not None
    assert computation.output_gdf.iloc[0]["risk_score"] == pytest.approx(1.0)
