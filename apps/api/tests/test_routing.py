"""Pure-function tests for Phase 8 routing: road-graph construction
(endpoint snapping), the hazard/edge overlay, edge cost/blocking rules,
Dijkstra, node snapping, and route-geometry assembly -- verified against
known synthetic cases with hand-computable ground truth, same discipline
as test_exposure.py/test_risk.py/test_terrain.py.
"""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from app.services.risk import compute_hazard_intensity_weight, compute_risk_score
from app.services.routing import (
    RoadEdge,
    RoadGraph,
    RoadNode,
    apply_hazard_costs,
    build_road_graph,
    build_route_line,
    dijkstra,
    find_nearest_node,
    overlay_edges_with_hazard,
)

CRS = "EPSG:32643"


# --- build_road_graph ----------------------------------------------------------


def test_build_road_graph_plus_shape_shares_exact_endpoint() -> None:
    # "+"-shaped network: 4 edges meeting exactly at (0,0).
    lines = [
        LineString([(-10, 0), (0, 0)]),
        LineString([(0, 0), (10, 0)]),
        LineString([(0, -10), (0, 0)]),
        LineString([(0, 0), (0, 10)]),
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs=CRS)
    graph = build_road_graph(gdf, node_snap_tolerance_m=1.0)

    assert len(graph.nodes) == 5  # 4 arm-tips + 1 shared hub
    assert len(graph.edges) == 4
    hub_key = graph.edges[0].v if graph.edges[0].u != graph.edges[1].u else graph.edges[0].u
    # every edge must touch the same hub node
    hub_candidates = {graph.edges[0].u, graph.edges[0].v}
    for edge in graph.edges[1:]:
        assert (edge.u in hub_candidates) or (edge.v in hub_candidates)


def test_build_road_graph_snaps_near_miss_endpoint_within_tolerance() -> None:
    # Second edge's start is 0.3m off the hub -- within a 1.0m tolerance,
    # must snap to the SAME node as the other three edges' shared endpoint.
    lines = [
        LineString([(-10, 0), (0, 0)]),
        LineString([(0.3, 0), (10, 0)]),
        LineString([(0, -10), (0, 0)]),
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs=CRS)
    graph = build_road_graph(gdf, node_snap_tolerance_m=1.0)

    assert len(graph.nodes) == 4  # 3 arm-tips + 1 shared (snapped) hub
    assert len(graph.edges) == 3


def test_build_road_graph_does_not_snap_endpoint_outside_tolerance() -> None:
    # Same 0.3m offset, but a tight 0.01m tolerance -- must NOT snap;
    # the second edge's start becomes its own isolated node.
    lines = [
        LineString([(-10, 0), (0, 0)]),
        LineString([(0.3, 0), (10, 0)]),
        LineString([(0, -10), (0, 0)]),
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs=CRS)
    graph = build_road_graph(gdf, node_snap_tolerance_m=0.01)

    assert len(graph.nodes) == 5  # the near-miss endpoint is now its own node
    assert len(graph.edges) == 3


def test_build_road_graph_explodes_multilinestring() -> None:
    from shapely.geometry import MultiLineString

    multi = MultiLineString([[(0, 0), (10, 0)], [(20, 0), (30, 0)]])
    gdf = gpd.GeoDataFrame({"geometry": [multi]}, crs=CRS)
    graph = build_road_graph(gdf, node_snap_tolerance_m=1.0)
    assert len(graph.edges) == 2  # each part becomes its own edge
    assert len(graph.nodes) == 4  # no shared endpoints between the two parts


def test_build_road_graph_rejects_non_line_geometry() -> None:
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs=CRS)
    with pytest.raises(ValueError, match="Unsupported road geometry types"):
        build_road_graph(gdf, node_snap_tolerance_m=1.0)


def test_build_road_graph_rejects_non_positive_tolerance() -> None:
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 0)])]}, crs=CRS)
    with pytest.raises(ValueError, match="node_snap_tolerance_m must be > 0"):
        build_road_graph(gdf, node_snap_tolerance_m=0.0)


# --- overlay_edges_with_hazard ---------------------------------------------------


def test_overlay_edges_with_hazard_splits_edge_by_class() -> None:
    # Edge crosses two adjacent 10x20 hazard-class squares: class 1 covers
    # x:[0,10], class 2 covers x:[10,20]; edge runs (0,5)->(20,5), 5m into
    # each, hand-computed intersection = 10.0m per class.
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 5), (20, 5)]), length_m=20.0)
    hazard_polygons = gpd.GeoDataFrame(
        {
            "hazard_class_code": [1, 2],
            "geometry": [
                Polygon([(0, 0), (10, 0), (10, 20), (0, 20)]),
                Polygon([(10, 0), (20, 0), (20, 20), (10, 20)]),
            ],
        },
        crs=CRS,
    )
    result = overlay_edges_with_hazard([edge], hazard_polygons)
    assert result[0][1] == pytest.approx(10.0)
    assert result[0][2] == pytest.approx(10.0)


def test_overlay_edges_with_hazard_empty_when_no_hazard_polygons() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (10, 0)]), length_m=10.0)
    empty = gpd.GeoDataFrame({"hazard_class_code": [], "geometry": []}, geometry="geometry", crs=CRS)
    assert overlay_edges_with_hazard([edge], empty) == {}


def test_overlay_edges_with_hazard_edge_outside_any_polygon_gets_no_entry() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(100, 100), (110, 100)]), length_m=10.0)
    hazard_polygons = gpd.GeoDataFrame(
        {"hazard_class_code": [1], "geometry": [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])]}, crs=CRS
    )
    assert overlay_edges_with_hazard([edge], hazard_polygons) == {}


# --- apply_hazard_costs (edge costing + blocking) --------------------------------


def test_apply_hazard_costs_computes_component_and_intensity_matches_risk_module() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1.0)
    hazard_by_edge = {0: {3: 10.0}}  # landslide class 3, 10m of exposure

    def intensity_fn(code: int) -> float:
        return compute_hazard_intensity_weight("landslide_susceptibility", code)

    result = apply_hazard_costs([edge], hazard_by_edge, intensity_fn, block_threshold=1.0)
    expected_intensity = compute_hazard_intensity_weight("landslide_susceptibility", 3)
    assert expected_intensity == pytest.approx(0.6)
    assert result[0].hazard_component_m == pytest.approx(10.0 * 0.6)
    assert result[0].max_class_intensity == pytest.approx(0.6)


def test_apply_hazard_costs_boundary_blocks_at_exactly_threshold() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1.0)
    hazard_by_edge = {0: {5: 5.0}}  # landslide class 5 -> intensity exactly 1.0

    result = apply_hazard_costs(
        [edge], hazard_by_edge, lambda c: compute_hazard_intensity_weight("landslide_susceptibility", c),
        block_threshold=1.0,
    )
    assert result[0].blocked is True  # left-inclusive: intensity == threshold blocks


def test_apply_hazard_costs_just_below_threshold_not_blocked() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1.0)
    hazard_by_edge = {0: {4: 5.0}}  # intensity 0.8 < threshold 1.0

    result = apply_hazard_costs(
        [edge], hazard_by_edge, lambda c: compute_hazard_intensity_weight("landslide_susceptibility", c),
        block_threshold=1.0,
    )
    assert result[0].blocked is False


def test_apply_hazard_costs_does_not_mutate_input_edges() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1.0)
    apply_hazard_costs([edge], {0: {5: 5.0}}, lambda c: 1.0, block_threshold=1.0)
    assert edge.blocked is False  # original dataclass instance untouched
    assert edge.hazard_component_m == 0.0


def test_apply_hazard_costs_risk_aware_matches_phase7_formula_directly() -> None:
    edge = RoadEdge(id=0, u=(0, 0), v=(1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1.0)
    hazard_by_edge = {0: {2: 8.0}}
    vulnerability_weight, consequence_weight = 0.5, 0.9

    def intensity_fn(code: int) -> float:
        return compute_risk_score(
            compute_hazard_intensity_weight("landslide_susceptibility", code), vulnerability_weight, consequence_weight
        )

    result = apply_hazard_costs([edge], hazard_by_edge, intensity_fn, block_threshold=1.0)
    expected = compute_risk_score(compute_hazard_intensity_weight("landslide_susceptibility", 2), 0.5, 0.9)
    assert result[0].hazard_component_m == pytest.approx(8.0 * expected)


# --- find_nearest_node -------------------------------------------------------------


def test_find_nearest_node_snaps_within_max_distance() -> None:
    nodes = {(0, 0): RoadNode(key=(0, 0), x=0.0, y=0.0), (100, 100): RoadNode(key=(100, 100), x=100.0, y=100.0)}
    result = find_nearest_node(nodes, 1.0, 1.0, max_distance_m=50.0)
    assert result is not None
    key, distance = result
    assert key == (0, 0)
    assert distance == pytest.approx((1.0**2 + 1.0**2) ** 0.5)


def test_find_nearest_node_returns_none_outside_max_distance() -> None:
    nodes = {(0, 0): RoadNode(key=(0, 0), x=0.0, y=0.0)}
    assert find_nearest_node(nodes, 1000.0, 1000.0, max_distance_m=50.0) is None


def test_find_nearest_node_empty_graph_returns_none() -> None:
    assert find_nearest_node({}, 0.0, 0.0, max_distance_m=50.0) is None


# --- dijkstra ----------------------------------------------------------------------


def _diamond_graph() -> RoadGraph:
    # N1 --(direct, len=15, hazard=50)--> N4
    # N1 --(E0, len=10, hazard=0)--> N2 --(E1, len=10, hazard=0)--> N4
    # Shortest-by-distance: direct (15). Hazard-aware (penalty>=2.75): detour (20).
    nodes = {k: RoadNode(key=k, x=0, y=0) for k in [(1,), (2,), (4,)]}
    e_direct = RoadEdge(id=0, u=(1,), v=(4,), geometry=None, length_m=15.0, hazard_component_m=50.0, max_class_intensity=1.0)
    e0 = RoadEdge(id=1, u=(1,), v=(2,), geometry=None, length_m=10.0)
    e1 = RoadEdge(id=2, u=(2,), v=(4,), geometry=None, length_m=10.0)
    adjacency = {(1,): [0, 1], (2,): [1, 2], (4,): [0, 2]}
    return RoadGraph(nodes=nodes, edges=[e_direct, e0, e1], adjacency=adjacency)


def test_dijkstra_shortest_by_distance_takes_direct_edge() -> None:
    graph = _diamond_graph()
    result = dijkstra(graph, (1,), (4,), weight_fn=lambda e: e.length_m)
    assert result is not None
    assert result.edge_ids == [0]
    assert result.total_cost == pytest.approx(15.0)


def test_dijkstra_hazard_aware_takes_detour_when_penalty_is_large() -> None:
    graph = _diamond_graph()
    penalty = 5.0

    def hazard_weight(e: RoadEdge) -> float:
        return e.length_m + penalty * e.hazard_component_m

    result = dijkstra(graph, (1,), (4,), weight_fn=hazard_weight)
    assert result is not None
    assert result.edge_ids == [1, 2]  # the detour, not the direct edge
    assert result.total_cost == pytest.approx(20.0)


def test_dijkstra_excludes_blocked_edges() -> None:
    graph = _diamond_graph()
    result = dijkstra(graph, (1,), (4,), weight_fn=lambda e: e.length_m, excluded_edge_ids={0})
    assert result is not None
    assert result.edge_ids == [1, 2]  # forced onto the detour


def test_dijkstra_disconnected_graph_returns_none() -> None:
    nodes = {(1,): RoadNode(key=(1,), x=0, y=0), (2,): RoadNode(key=(2,), x=0, y=0)}
    graph = RoadGraph(nodes=nodes, edges=[], adjacency={})
    assert dijkstra(graph, (1,), (2,), weight_fn=lambda e: e.length_m) is None


def test_dijkstra_source_equals_target_zero_cost() -> None:
    graph = _diamond_graph()
    result = dijkstra(graph, (1,), (1,), weight_fn=lambda e: e.length_m)
    assert result is not None
    assert result.edge_ids == []
    assert result.total_cost == 0.0


def test_dijkstra_unknown_node_returns_none() -> None:
    graph = _diamond_graph()
    assert dijkstra(graph, (1,), (999,), weight_fn=lambda e: e.length_m) is None


# --- build_route_line ---------------------------------------------------------------


def test_build_route_line_concatenates_in_traversal_order_without_duplicate_joints() -> None:
    edge_a = RoadEdge(id=0, u=(0,), v=(1,), geometry=LineString([(0, 0), (10, 0)]), length_m=10.0)
    edge_b = RoadEdge(id=1, u=(1,), v=(2,), geometry=LineString([(10, 0), (20, 0)]), length_m=10.0)
    from app.services.routing import RouteResult

    route = RouteResult(node_path=[(0,), (1,), (2,)], edge_ids=[0, 1], total_cost=20.0)
    line = build_route_line(route, {0: edge_a, 1: edge_b})
    assert list(line.coords) == [(0, 0), (10, 0), (20, 0)]


def test_build_route_line_reverses_edge_oriented_against_path_direction() -> None:
    # edge_b's own geometry runs (20,0)->(10,0), OPPOSITE to path direction
    # (1,)->(2,) which physically goes from x=10 to x=20 -- must be reversed.
    edge_a = RoadEdge(id=0, u=(0,), v=(1,), geometry=LineString([(0, 0), (10, 0)]), length_m=10.0)
    edge_b = RoadEdge(id=1, u=(2,), v=(1,), geometry=LineString([(20, 0), (10, 0)]), length_m=10.0)
    from app.services.routing import RouteResult

    route = RouteResult(node_path=[(0,), (1,), (2,)], edge_ids=[0, 1], total_cost=20.0)
    line = build_route_line(route, {0: edge_a, 1: edge_b})
    assert list(line.coords) == [(0, 0), (10, 0), (20, 0)]
