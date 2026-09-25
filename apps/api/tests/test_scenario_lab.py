"""Pure-function tests for Phase 10 Scenario Lab: effective-layer
resolution, the layer-override immutability predicate, blocked-edge
geometry resolution, and comparison/diff aggregation -- verified against
known synthetic cases, same discipline as test_digital_twin.py.
"""

import pytest
from shapely.geometry import LineString

from app.services.routing import RoadEdge, RoadGraph, RoadNode, resolve_blocked_edge_ids
from app.services.scenario import (
    LayerRef,
    can_mutate_overrides,
    compute_comparison,
    diff_by_class,
    diff_route_analysis,
    resolve_effective_layers,
)


# --- resolve_effective_layers -----------------------------------------------------


def test_resolve_effective_layers_override_wins_over_baseline() -> None:
    baseline = [LayerRef(dataset_type="flood_inundation", category="hazard", dataset_id="base-1", source="baseline")]
    overrides = [LayerRef(dataset_type="flood_inundation", category="hazard", dataset_id="override-1", source="override")]
    effective = resolve_effective_layers(baseline, overrides)
    assert effective["flood_inundation"].dataset_id == "override-1"
    assert effective["flood_inundation"].source == "override"


def test_resolve_effective_layers_baseline_only() -> None:
    baseline = [LayerRef(dataset_type="roads", category="observation", dataset_id="roads-1", source="baseline")]
    effective = resolve_effective_layers(baseline, [])
    assert effective["roads"].dataset_id == "roads-1"
    assert effective["roads"].source == "baseline"


def test_resolve_effective_layers_override_only_dataset_type_absent_from_baseline() -> None:
    overrides = [LayerRef(dataset_type="landslide_susceptibility", category="hazard", dataset_id="ls-1", source="override")]
    effective = resolve_effective_layers([], overrides)
    assert effective["landslide_susceptibility"].dataset_id == "ls-1"


def test_resolve_effective_layers_empty_inputs_returns_empty() -> None:
    assert resolve_effective_layers([], []) == {}


# --- can_mutate_overrides ---------------------------------------------------------


def test_can_mutate_overrides_true_when_dataset_type_not_yet_consumed() -> None:
    assert can_mutate_overrides("landslide_susceptibility", set()) is True
    assert can_mutate_overrides("landslide_susceptibility", {"dem"}) is True


def test_can_mutate_overrides_false_once_that_dataset_type_was_consumed() -> None:
    assert can_mutate_overrides("landslide_susceptibility", {"landslide_susceptibility"}) is False
    assert can_mutate_overrides("dem", {"dem", "landslide_susceptibility"}) is False


# --- resolve_blocked_edge_ids -------------------------------------------------------


def _simple_graph() -> RoadGraph:
    # Two collinear edges along the x-axis: (0,0)-(10,0) and (10,0)-(20,0).
    nodes = {
        (0, 0): RoadNode(key=(0, 0), x=0.0, y=0.0),
        (10, 0): RoadNode(key=(10, 0), x=10.0, y=0.0),
        (20, 0): RoadNode(key=(20, 0), x=20.0, y=0.0),
    }
    edges = [
        RoadEdge(id=0, u=(0, 0), v=(10, 0), geometry=LineString([(0, 0), (10, 0)]), length_m=10.0),
        RoadEdge(id=1, u=(10, 0), v=(20, 0), geometry=LineString([(10, 0), (20, 0)]), length_m=10.0),
    ]
    adjacency = {(0, 0): [0], (10, 0): [0, 1], (20, 0): [1]}
    return RoadGraph(nodes=nodes, edges=edges, adjacency=adjacency)


def test_resolve_blocked_edge_ids_matches_intersecting_edge_only() -> None:
    graph = _simple_graph()
    # A short perpendicular blocker crossing edge 0 (near x=5) but nowhere
    # near edge 1 (x in [10, 20]).
    blocker = LineString([(5, -1), (5, 1)])
    blocked = resolve_blocked_edge_ids(graph, [blocker], match_buffer_m=0.5)
    assert blocked == {0}


def test_resolve_blocked_edge_ids_empty_geometries_returns_empty_set() -> None:
    graph = _simple_graph()
    assert resolve_blocked_edge_ids(graph, [], match_buffer_m=5.0) == set()


def test_resolve_blocked_edge_ids_empty_graph_returns_empty_set() -> None:
    empty_graph = RoadGraph(nodes={}, edges=[], adjacency={})
    blocker = LineString([(5, -1), (5, 1)])
    assert resolve_blocked_edge_ids(empty_graph, [blocker], match_buffer_m=5.0) == set()


def test_resolve_blocked_edge_ids_buffer_distance_sensitivity() -> None:
    graph = _simple_graph()
    # A blocker parallel to edge 0 at y=3, spanning x in [0, 9] -- exactly
    # 3m from edge 0 (vertical distance) but slightly farther (sqrt(10) ~=
    # 3.16m) from edge 1's nearest endpoint (10, 0), so a buffer between
    # those two distances isolates edge 0 only. Not caught by a too-small
    # buffer, caught once the buffer reaches edge 0's distance.
    blocker = LineString([(0, 3), (9, 3)])
    assert resolve_blocked_edge_ids(graph, [blocker], match_buffer_m=1.0) == set()
    assert resolve_blocked_edge_ids(graph, [blocker], match_buffer_m=3.1) == {0}


# --- diff_by_class (exposure/risk comparison) ---------------------------------------


def test_diff_by_class_computes_delta_and_pct_change() -> None:
    left = {"inundated": {"hazard_class_code": 1, "count": 10, "sum": 100.0}}
    right = {"inundated": {"hazard_class_code": 1, "count": 15, "sum": 100.0}}
    diff = diff_by_class(left, right)
    assert diff["inundated"]["count"] == {"left": 10.0, "right": 15.0, "delta": 5.0, "pct_change": 0.5}
    assert diff["inundated"]["sum"]["delta"] == 0.0


def test_diff_by_class_treats_class_present_only_on_one_side_as_zero() -> None:
    left = {"very_low": {"hazard_class_code": 1, "count": 5}}
    right = {"very_high": {"hazard_class_code": 5, "count": 3}}
    diff = diff_by_class(left, right)
    assert set(diff) == {"very_low", "very_high"}
    assert diff["very_low"]["count"] == {"left": 5.0, "right": 0.0, "delta": -5.0, "pct_change": -1.0}
    assert diff["very_high"]["count"] == {"left": 0.0, "right": 3.0, "delta": 3.0, "pct_change": None}


def test_diff_by_class_reports_risk_class_change_not_numeric_delta() -> None:
    left = {"very_low": {"hazard_class_code": 1, "risk_score": 0.1, "risk_class": "low"}}
    right = {"very_low": {"hazard_class_code": 1, "risk_score": 0.5, "risk_class": "moderate"}}
    diff = diff_by_class(left, right)
    assert diff["very_low"]["risk_class"] == {"left": "low", "right": "moderate", "changed": True}
    assert diff["very_low"]["risk_score"]["delta"] == pytest.approx(0.4)


def test_diff_by_class_empty_inputs_returns_empty() -> None:
    assert diff_by_class({}, {}) == {}


# --- diff_route_analysis --------------------------------------------------------


def test_diff_route_analysis_computes_metric_deltas() -> None:
    left = {
        "shortest_route": {"feasible": True, "distance_m": 100.0, "hazard_component_m": 10.0, "cost_m_equivalent": 110.0},
        "hazard_aware_route": {"feasible": True, "distance_m": 120.0, "hazard_component_m": 2.0, "cost_m_equivalent": 122.0},
        "graph_summary": {"blocked_edge_count": 1, "total_blocked_length_m": 5.0},
    }
    right = {
        "shortest_route": {"feasible": True, "distance_m": 100.0, "hazard_component_m": 20.0, "cost_m_equivalent": 120.0},
        "hazard_aware_route": {"feasible": True, "distance_m": 130.0, "hazard_component_m": 1.0, "cost_m_equivalent": 131.0},
        "graph_summary": {"blocked_edge_count": 3, "total_blocked_length_m": 15.0},
    }
    diff = diff_route_analysis(left, right)
    assert diff["shortest_route"]["hazard_component_m"]["delta"] == pytest.approx(10.0)
    assert diff["graph_summary"]["blocked_edge_count"]["delta"] == pytest.approx(2.0)


def test_diff_route_analysis_infeasible_route_treated_as_zero_metrics() -> None:
    left = {"shortest_route": {"feasible": False}, "hazard_aware_route": {"feasible": False}, "graph_summary": {}}
    right = {
        "shortest_route": {"feasible": True, "distance_m": 50.0, "hazard_component_m": 0.0, "cost_m_equivalent": 50.0},
        "hazard_aware_route": {"feasible": True, "distance_m": 50.0, "hazard_component_m": 0.0, "cost_m_equivalent": 50.0},
        "graph_summary": {},
    }
    diff = diff_route_analysis(left, right)
    assert diff["shortest_route"]["feasible"] == {"left": False, "right": True}
    assert diff["shortest_route"]["distance_m"]["left"] == 0.0
    assert diff["shortest_route"]["distance_m"]["delta"] == 50.0


# --- compute_comparison (dispatcher) ------------------------------------------------


def test_compute_comparison_exposure_no_warning_when_hazard_type_matches() -> None:
    left = {"by_class": {"inundated": {"hazard_class_code": 1, "count": 10}}}
    right = {"by_class": {"inundated": {"hazard_class_code": 1, "count": 20}}}
    result = compute_comparison("exposure", left, right, "flood_inundation", "flood_inundation")
    assert result.comparability_warnings == []
    assert result.diff["inundated"]["count"]["delta"] == 10.0


def test_compute_comparison_warns_on_hazard_dataset_type_mismatch() -> None:
    left = {"by_class": {}}
    right = {"by_class": {}}
    result = compute_comparison("risk", left, right, "flood_inundation", "landslide_susceptibility")
    assert len(result.comparability_warnings) == 1
    assert "hazard_dataset_type differs" in result.comparability_warnings[0]


def test_compute_comparison_route_warns_on_feasibility_mismatch() -> None:
    left = {"shortest_route": {"feasible": True}, "hazard_aware_route": {"feasible": True}, "graph_summary": {}}
    right = {"shortest_route": {"feasible": False}, "hazard_aware_route": {"feasible": False}, "graph_summary": {}}
    result = compute_comparison("route", left, right, "flood_inundation", "flood_inundation")
    assert any("feasibility differs" in w for w in result.comparability_warnings)


def test_compute_comparison_rejects_unsupported_analysis_type() -> None:
    with pytest.raises(ValueError, match="Unsupported analysis_type"):
        compute_comparison("hazard", {}, {}, "flood_inundation", "flood_inundation")
