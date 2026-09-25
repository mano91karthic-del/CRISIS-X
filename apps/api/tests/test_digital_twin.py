"""Pure-function tests for Phase 9 Digital Twin composition/aggregation:
category derivation, reference-CRS selection, extent union +
reprojection + mismatch flagging, missing-recommended-layers, and
acquisition-date-range aggregation -- verified against known synthetic
cases with hand-computable ground truth, same discipline as
test_exposure.py/test_risk.py/test_routing.py.
"""

from datetime import datetime, timezone

import pytest

from app.services.digital_twin import (
    CATEGORY_BY_ORIGIN,
    RECOMMENDED_CATEGORIES,
    AcquisitionDateInput,
    LayerExtentInput,
    compute_acquisition_date_range,
    compute_twin_extent,
    derive_layer_category,
    missing_recommended_layers,
    pick_reference_crs,
)


# --- derive_layer_category -----------------------------------------------------


def test_derive_layer_category_covers_every_known_origin() -> None:
    assert derive_layer_category("uploaded") == "observation"
    assert derive_layer_category("uploaded", "dem") == "terrain"
    assert derive_layer_category("uploaded", "dsm") == "terrain"
    assert derive_layer_category("uploaded", "roads") == "observation"
    assert derive_layer_category("uploaded", "buildings") == "observation"
    assert derive_layer_category("crisisx_derived") == "terrain"
    assert derive_layer_category("terrain_x_import") == "terrain"
    assert derive_layer_category("hazard_model") == "hazard"
    assert derive_layer_category("eo_analysis") == "hazard"
    assert derive_layer_category("exposure_analysis") == "exposure"
    assert derive_layer_category("risk_analysis") == "risk"
    assert derive_layer_category("route_analysis") == "route"


def test_derive_layer_category_matches_every_dataset_origin_enum_value() -> None:
    from app.models.dataset import DatasetOrigin

    for origin in DatasetOrigin:
        assert origin.value in CATEGORY_BY_ORIGIN, f"{origin.value} missing from CATEGORY_BY_ORIGIN"


def test_derive_layer_category_rejects_unknown_origin() -> None:
    with pytest.raises(ValueError, match="Unrecognized Dataset.origin"):
        derive_layer_category("something_new")


# --- missing_recommended_layers -------------------------------------------------


def test_missing_recommended_layers_reports_all_when_empty() -> None:
    assert missing_recommended_layers(set()) == RECOMMENDED_CATEGORIES


def test_missing_recommended_layers_reports_none_when_all_present() -> None:
    assert missing_recommended_layers({"terrain", "hazard", "exposure", "risk"}) == []


def test_missing_recommended_layers_reports_exact_difference() -> None:
    assert missing_recommended_layers({"terrain", "risk"}) == ["hazard", "exposure"]


def test_missing_recommended_layers_ignores_non_recommended_categories() -> None:
    # "route" and "observation" are deliberately not on the checklist.
    assert missing_recommended_layers({"route", "observation"}) == RECOMMENDED_CATEGORIES


# --- pick_reference_crs -----------------------------------------------------------


def test_pick_reference_crs_prefers_terrain_over_hazard() -> None:
    layers = [
        LayerExtentInput(layer_id="hazard1", category="hazard", crs="EPSG:4326", bbox=None),
        LayerExtentInput(layer_id="terrain1", category="terrain", crs="EPSG:32643", bbox=None),
    ]
    assert pick_reference_crs(layers) == "EPSG:32643"


def test_pick_reference_crs_falls_back_down_priority_list() -> None:
    layers = [LayerExtentInput(layer_id="route1", category="route", crs="EPSG:32643", bbox=None)]
    assert pick_reference_crs(layers) == "EPSG:32643"


def test_pick_reference_crs_none_when_no_layer_has_crs() -> None:
    layers = [LayerExtentInput(layer_id="obs1", category="observation", crs=None, bbox=None)]
    assert pick_reference_crs(layers) is None


def test_pick_reference_crs_empty_list_returns_none() -> None:
    assert pick_reference_crs([]) is None


# --- compute_twin_extent -----------------------------------------------------------


def test_compute_twin_extent_unions_same_crs_boxes() -> None:
    layers = [
        LayerExtentInput(layer_id="a", category="terrain", crs="EPSG:32643", bbox=(0.0, 0.0, 10.0, 10.0)),
        LayerExtentInput(layer_id="b", category="hazard", crs="EPSG:32643", bbox=(5.0, 5.0, 20.0, 20.0)),
    ]
    result = compute_twin_extent(layers)
    assert result.reference_crs == "EPSG:32643"
    assert result.bbox == pytest.approx((0.0, 0.0, 20.0, 20.0))
    assert result.crs_mismatch_layer_ids == []
    assert result.layers_without_extent == []


def test_compute_twin_extent_flags_and_reprojects_mismatched_crs() -> None:
    # Reference is the terrain layer's EPSG:32643. A hazard layer supplied
    # in WGS84 must be reprojected into the union AND flagged as a
    # mismatch (never silently folded in without disclosure).
    layers = [
        LayerExtentInput(layer_id="terrain1", category="terrain", crs="EPSG:32643", bbox=(0.0, 0.0, 10.0, 10.0)),
        LayerExtentInput(layer_id="hazard1", category="hazard", crs="EPSG:4326", bbox=(77.0, 13.0, 77.001, 13.001)),
    ]
    result = compute_twin_extent(layers)
    assert result.reference_crs == "EPSG:32643"
    assert result.crs_mismatch_layer_ids == ["hazard1"]
    # union must have grown to include the reprojected hazard layer, far
    # outside the terrain layer's tiny local 0-10 box.
    assert result.bbox[2] > 10.0 or result.bbox[3] > 10.0


def test_compute_twin_extent_excludes_and_reports_layers_without_bbox() -> None:
    layers = [
        LayerExtentInput(layer_id="terrain1", category="terrain", crs="EPSG:32643", bbox=(0.0, 0.0, 10.0, 10.0)),
        LayerExtentInput(layer_id="csv1", category="observation", crs=None, bbox=None),
    ]
    result = compute_twin_extent(layers)
    assert result.bbox == pytest.approx((0.0, 0.0, 10.0, 10.0))
    assert result.layers_without_extent == ["csv1"]


def test_compute_twin_extent_no_crs_anywhere_returns_null_extent() -> None:
    layers = [LayerExtentInput(layer_id="csv1", category="observation", crs=None, bbox=None)]
    result = compute_twin_extent(layers)
    assert result.reference_crs is None
    assert result.bbox is None
    assert result.layers_without_extent == ["csv1"]


def test_compute_twin_extent_empty_layers_returns_null() -> None:
    result = compute_twin_extent([])
    assert result.reference_crs is None
    assert result.bbox is None


# --- compute_acquisition_date_range --------------------------------------------------


def test_acquisition_date_range_mixed_present_and_absent() -> None:
    d1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    d2 = datetime(2025, 6, 15, tzinfo=timezone.utc)
    layers = [
        AcquisitionDateInput(layer_id="a", acquisition_date=d1),
        AcquisitionDateInput(layer_id="b", acquisition_date=d2),
        AcquisitionDateInput(layer_id="c", acquisition_date=None),
    ]
    result = compute_acquisition_date_range(layers)
    assert result.earliest == d1
    assert result.latest == d2
    assert result.layers_without_acquisition_date == ["c"]


def test_acquisition_date_range_all_absent() -> None:
    layers = [AcquisitionDateInput(layer_id="a", acquisition_date=None)]
    result = compute_acquisition_date_range(layers)
    assert result.earliest is None
    assert result.latest is None
    assert result.layers_without_acquisition_date == ["a"]


def test_acquisition_date_range_empty_input() -> None:
    result = compute_acquisition_date_range([])
    assert result.earliest is None
    assert result.latest is None
    assert result.layers_without_acquisition_date == []
