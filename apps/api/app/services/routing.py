"""Phase 8: Response + Hazard-Aware Routing.

Builds a routable graph from a user-uploaded road-network Dataset, costs
its edges against a Phase 4/5 hazard raster (optionally combined with a
Phase 7 RiskAnalysis's declared vulnerability_weight/consequence_weight),
and computes both a plain shortest route and a hazard-aware route via
Dijkstra over the same blocked-edge-filtered graph.

Hazard-class -> intensity is never a new, undocumented number: it reuses
Phase 7's exact, already-documented mapping
(`app.services.risk.compute_hazard_intensity_weight` /
`compute_risk_score`), and hazard-raster classification/polygonization
reuses Phase 6's exact functions
(`app.services.exposure.classify_hazard_array` /
`polygonize_hazard_raster`). Neither is duplicated; both are imported.

IMPORTANT -- what "hazard-aware/safest feasible route" is NOT: it is not a
guarantee of physical safety, and it is NOT the route with the lowest raw
hazard_component_m. The hazard-aware route minimizes the single COMBINED
objective `distance_m + hazard_penalty_weight * hazard_component_m` --
distance and hazard cost are traded off against each other, not hazard
minimized in isolation, so a valid hazard-aware route can have a slightly
higher raw hazard_component_m than some other route while still having a
lower combined cost overall. It does not reflect real-time traffic, road
closures, or emergency-service availability -- none of that data exists in
this system.

Graph algorithm: plain Dijkstra, hand-rolled with stdlib `heapq` -- same
precedent as `app.services.terrain`'s `build_flow_graph`/
`compute_flow_accumulation`, which already hand-roll graph algorithms
rather than adding a dependency (no networkx in requirements.txt).

Connectivity model (the single biggest simplification here, see ADR 0009):
road-network nodes come ONLY from line endpoints, snapped together via a
coordinate-grid key if within `node_snap_tolerance_m` of each other. Two
road features that geometrically cross without sharing an endpoint vertex
there are NOT treated as connected -- no topological noding (splitting
lines at true intersections) is performed.
"""

import heapq
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.services.exposure import classify_hazard_array, polygonize_hazard_raster
from app.services.risk import HAZARD_CLASS_MAX_CODE, compute_hazard_intensity_weight, compute_risk_score
from app.services.terrain import pick_utm_crs

NodeKey = tuple[int, int]

METHOD = "dijkstra_hazard_aware_routing"

ROUTING_LIMITATIONS = [
    "This is a modeled routing screening based on uploaded road geometry and a modeled hazard/risk "
    "layer; it is NOT a guarantee of physical safety, and does NOT reflect real-time conditions, "
    "traffic, emergency-service availability, or road closures.",
    "Road-network connectivity is derived only from shared/snapped endpoint coordinates (within "
    "node_snap_tolerance_m); two road features that cross without sharing an endpoint vertex there are "
    "not treated as connected.",
    "'hazard-aware/safest feasible route' means the route minimizing the combined cost "
    "distance_m + hazard_penalty_weight * hazard_component_m in this network model -- it does not "
    "independently minimize raw hazard exposure, and it is not proven to be free of hazard.",
    "Areas outside the hazard raster's extent, or masked as nodata, are not assessed by this analysis "
    "and are not assumed safe.",
]

UTM_REPROJECTION_LIMITATION = (
    "The hazard raster's CRS was geographic and was automatically reprojected to a single "
    "auto-selected UTM zone; a single UTM zone is assumed adequate for the combined road+hazard extent."
)

_SUPPORTED_ROAD_GEOM_TYPES = {"LineString", "MultiLineString"}


# --- graph construction -------------------------------------------------------


@dataclass
class RoadNode:
    key: NodeKey
    x: float
    y: float


@dataclass
class RoadEdge:
    id: int
    u: NodeKey
    v: NodeKey
    geometry: Any
    length_m: float
    hazard_length_by_class_m: dict[int, float] = field(default_factory=dict)
    hazard_component_m: float = 0.0
    max_class_intensity: float = 0.0
    blocked: bool = False


@dataclass
class RoadGraph:
    nodes: dict[NodeKey, RoadNode]
    edges: list[RoadEdge]
    # node -> list of incident edge ids (both directions -- every edge is
    # treated as bidirectional; no directionality/one-way data exists in
    # the current road-dataset contract).
    adjacency: dict[NodeKey, list[int]]


def _snap_key(x: float, y: float, tolerance_m: float) -> NodeKey:
    return (round(x / tolerance_m), round(y / tolerance_m))


def build_road_graph(roads_gdf: Any, node_snap_tolerance_m: float) -> RoadGraph:
    """Builds a routable graph from a road-network GeoDataFrame (already in
    a metric CRS). Nodes are line endpoints, snapped together via a
    coordinate-grid key if within `node_snap_tolerance_m` of each other.
    Edges are one per (exploded) LineString feature, carrying its full
    original geometry -- not straightened to a chord, needed both for
    accurate length_m and for the hazard overlay.

    Raises ValueError for non-line geometry or a non-positive tolerance --
    fails closed rather than guessing.
    """
    if node_snap_tolerance_m <= 0:
        raise ValueError("node_snap_tolerance_m must be > 0")

    geom_types = set(roads_gdf.geom_type.dropna().unique())
    unsupported = geom_types - _SUPPORTED_ROAD_GEOM_TYPES
    if unsupported:
        raise ValueError(
            f"Unsupported road geometry types: {sorted(unsupported)}. "
            f"Only {sorted(_SUPPORTED_ROAD_GEOM_TYPES)} are supported."
        )
    if not geom_types:
        raise ValueError("Road dataset has no geometries.")

    exploded = roads_gdf.explode(index_parts=False, ignore_index=True)

    nodes: dict[NodeKey, RoadNode] = {}
    edges: list[RoadEdge] = []
    adjacency: dict[NodeKey, list[int]] = {}

    def _get_or_create_node(x: float, y: float) -> NodeKey:
        key = _snap_key(x, y, node_snap_tolerance_m)
        if key not in nodes:
            nodes[key] = RoadNode(key=key, x=x, y=y)
        return key

    for geom in exploded.geometry:
        if geom is None or geom.is_empty:
            continue
        coords = list(geom.coords)
        if len(coords) < 2:
            continue
        u = _get_or_create_node(coords[0][0], coords[0][1])
        v = _get_or_create_node(coords[-1][0], coords[-1][1])
        edge_id = len(edges)
        edges.append(RoadEdge(id=edge_id, u=u, v=v, geometry=geom, length_m=float(geom.length)))
        adjacency.setdefault(u, []).append(edge_id)
        adjacency.setdefault(v, []).append(edge_id)

    return RoadGraph(nodes=nodes, edges=edges, adjacency=adjacency)


# --- hazard overlay / edge costing --------------------------------------------


def overlay_edges_with_hazard(edges: list[RoadEdge], hazard_polygons_gdf: Any) -> dict[int, dict[int, float]]:
    """Geometric intersection of edge geometries against hazard-class
    polygons (from `app.services.exposure.polygonize_hazard_raster`),
    keeping per-edge granularity. Phase 6's `overlay_lines` dissolves the
    same kind of intersection across the WHOLE network; this instead groups
    by (edge_id, hazard_class_code) to answer "how much of THIS edge falls
    in each class" -- the same underlying `gpd.overlay(..., how=
    "intersection")` primitive, one additional groupby.

    Returns {edge_id: {hazard_class_code: length_m}}. An edge with no entry
    for a class, or missing entirely, had no length in that class --
    outside every hazard polygon (unassessed, not assumed safe).
    """
    import geopandas as gpd

    if not edges or hazard_polygons_gdf.empty:
        return {}

    edges_gdf = gpd.GeoDataFrame(
        {"edge_id": [e.id for e in edges]}, geometry=[e.geometry for e in edges], crs=hazard_polygons_gdf.crs
    )
    overlaid = gpd.overlay(
        edges_gdf, hazard_polygons_gdf[["hazard_class_code", "geometry"]], how="intersection"
    )
    if overlaid.empty:
        return {}

    overlaid["length_m"] = overlaid.geometry.length
    result: dict[int, dict[int, float]] = {}
    for (edge_id, class_code), group in overlaid.groupby(["edge_id", "hazard_class_code"]):
        result.setdefault(int(edge_id), {})[int(class_code)] = float(group["length_m"].sum())
    return result


def resolve_blocked_edge_ids(graph: RoadGraph, blocked_geometries: list[Any], match_buffer_m: float) -> set[int]:
    """Phase 10 Scenario Lab: maps caller-supplied blocker geometries (e.g.
    "this segment is impassable in this what-if") onto the graph's edge
    ids by buffering each blocker geometry by `match_buffer_m` and marking
    any edge whose geometry intersects the buffered area -- the same
    `geopandas` intersection primitive already used by
    `overlay_edges_with_hazard`, not a new modeling algorithm. Edge ids are
    only meaningful for the RoadGraph they were resolved against (they are
    positions in a per-run exploded edge list, not stable dataset
    identifiers), so this is always called fresh within a single
    `run_route_analysis` invocation, never persisted as a standalone id set.
    """
    import geopandas as gpd

    if not blocked_geometries or not graph.edges:
        return set()

    edges_gdf = gpd.GeoDataFrame(
        {"edge_id": [e.id for e in graph.edges]}, geometry=[e.geometry for e in graph.edges]
    )
    blocked_ids: set[int] = set()
    for geom in blocked_geometries:
        buffered = geom.buffer(match_buffer_m)
        hits = edges_gdf[edges_gdf.intersects(buffered)]
        blocked_ids.update(int(edge_id) for edge_id in hits["edge_id"])
    return blocked_ids


def apply_hazard_costs(
    edges: list[RoadEdge],
    hazard_by_edge: dict[int, dict[int, float]],
    intensity_fn: Callable[[int], float],
    block_threshold: float,
) -> list[RoadEdge]:
    """Returns NEW RoadEdge objects with hazard_length_by_class_m/
    hazard_component_m/max_class_intensity/blocked populated -- pure, does
    not mutate the input edges.

    An edge is blocked if ANY portion of it, however short, touches a
    hazard class at or above block_threshold -- a deliberately
    conservative rule (see ADR 0009): partial exposure to a critical
    hazard class is never quietly averaged away.
    """
    result: list[RoadEdge] = []
    for edge in edges:
        by_class = hazard_by_edge.get(edge.id, {})
        hazard_component_m = 0.0
        max_intensity = 0.0
        for class_code, length_m in by_class.items():
            intensity = intensity_fn(class_code)
            hazard_component_m += length_m * intensity
            max_intensity = max(max_intensity, intensity)
        result.append(
            replace(
                edge,
                hazard_length_by_class_m=by_class,
                hazard_component_m=hazard_component_m,
                max_class_intensity=max_intensity,
                blocked=max_intensity >= block_threshold,
            )
        )
    return result


# --- node snapping -------------------------------------------------------------


def find_nearest_node(
    nodes: dict[NodeKey, RoadNode], x: float, y: float, max_distance_m: float
) -> tuple[NodeKey, float] | None:
    """Brute-force nearest-node search (pure NumPy, no spatial-index
    dependency needed at hackathon-scale road-network node counts).
    Returns (node_key, distance_m), or None if the nearest node is farther
    than max_distance_m -- never silently snaps to a far-away point.
    """
    if not nodes:
        return None
    items = list(nodes.items())
    xs = np.array([n.x for _, n in items])
    ys = np.array([n.y for _, n in items])
    distances = np.sqrt((xs - x) ** 2 + (ys - y) ** 2)
    idx = int(np.argmin(distances))
    distance = float(distances[idx])
    if distance > max_distance_m:
        return None
    return items[idx][0], distance


# --- Dijkstra --------------------------------------------------------------------


@dataclass
class RouteResult:
    node_path: list[NodeKey]
    edge_ids: list[int]
    total_cost: float


def dijkstra(
    graph: RoadGraph,
    source: NodeKey,
    target: NodeKey,
    weight_fn: Callable[[RoadEdge], float],
    excluded_edge_ids: set[int] | None = None,
) -> RouteResult | None:
    """Standard Dijkstra with a binary heap. `excluded_edge_ids` lets the
    same graph be queried with blocked edges removed without rebuilding it.
    Returns None if source/target are unknown or disconnected -- never
    raises for "no route found", since that's a valid, expected outcome.
    """
    excluded = excluded_edge_ids or set()
    if source not in graph.nodes or target not in graph.nodes:
        return None
    if source == target:
        return RouteResult(node_path=[source], edge_ids=[], total_cost=0.0)

    edge_by_id = {e.id: e for e in graph.edges}
    dist: dict[NodeKey, float] = {source: 0.0}
    prev: dict[NodeKey, tuple[NodeKey, int]] = {}
    visited: set[NodeKey] = set()
    heap: list[tuple[float, NodeKey]] = [(0.0, source)]

    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == target:
            break
        for edge_id in graph.adjacency.get(node, []):
            if edge_id in excluded:
                continue
            edge = edge_by_id[edge_id]
            other = edge.v if edge.u == node else edge.u
            if other in visited:
                continue
            weight = weight_fn(edge)
            if weight < 0:
                raise ValueError("edge weight must be non-negative for Dijkstra")
            new_dist = d + weight
            if new_dist < dist.get(other, math.inf):
                dist[other] = new_dist
                prev[other] = (node, edge_id)
                heapq.heappush(heap, (new_dist, other))

    if target not in dist:
        return None

    node_path = [target]
    edge_ids: list[int] = []
    cur = target
    while cur != source:
        prev_node, edge_id = prev[cur]
        edge_ids.append(edge_id)
        node_path.append(prev_node)
        cur = prev_node
    node_path.reverse()
    edge_ids.reverse()
    return RouteResult(node_path=node_path, edge_ids=edge_ids, total_cost=dist[target])


def build_route_line(route: RouteResult, edge_by_id: dict[int, RoadEdge]) -> Any:
    """Concatenates a path's edge geometries in traversal order into a
    single LineString. Orients each edge's own coordinate list to match
    the path direction, rather than relying on shapely.ops.linemerge's
    exact-endpoint-coincidence matching -- node keys are SNAPPED (grid-
    rounded), so two edges sharing a node may have slightly different
    actual endpoint coordinates (within node_snap_tolerance_m), which
    linemerge would not reliably stitch.
    """
    from shapely.geometry import LineString

    coords: list[tuple[float, float]] = []
    current_node = route.node_path[0]
    for edge_id, next_node in zip(route.edge_ids, route.node_path[1:]):
        edge = edge_by_id[edge_id]
        edge_coords = list(edge.geometry.coords)
        oriented = edge_coords if edge.u == current_node else list(reversed(edge_coords))
        coords.extend(oriented if not coords else oriented[1:])
        current_node = next_node
    return LineString(coords)


# --- orchestration (I/O) --------------------------------------------------------


@dataclass
class RouteAnalysisComputation:
    results: dict[str, Any]
    working_crs: str
    shortest_route_gdf: Any | None
    hazard_aware_route_gdf: Any | None
    blocked_segments_gdf: Any | None


def run_route_analysis(
    road_path: Path,
    hazard_path: Path,
    hazard_dataset_type: str,
    *,
    origin_lon: float,
    origin_lat: float,
    destination_lon: float,
    destination_lat: float,
    hazard_penalty_weight: float,
    block_threshold: float,
    node_snap_tolerance_m: float,
    max_snap_distance_m: float,
    class_legend_override: dict[str, str] | None = None,
    vulnerability_weight: float | None = None,
    consequence_weight: float | None = None,
    blocked_segment_geometries: list[dict[str, Any]] | None = None,
    match_buffer_m: float = 5.0,
) -> RouteAnalysisComputation:
    """Full I/O + computation pipeline: load/classify/polygonize the hazard
    raster (mirrors Phase 6's own loading step), build the road graph,
    overlay it against hazard classes, cost + block edges, snap origin/
    destination, and run Dijkstra twice (shortest, hazard-aware) over the
    same blocked-edge-filtered graph.

    `vulnerability_weight`/`consequence_weight` given together select
    risk-aware costing (intensity = Phase 7's risk_score, recomputed fresh
    per class via the same formula -- see ADR 0009 for why this is not
    read back out of a RiskAnalysis.results["by_class"]); omitted, they
    select hazard-only costing (intensity = raw hazard_intensity_weight).

    `blocked_segment_geometries` (Phase 10 Scenario Lab, additive, default
    None -- every existing caller is unaffected): optional GeoJSON-like
    LineString/MultiLineString geometries in WGS84 (same coordinate
    convention as origin/destination) declaring "this road segment is
    impassable in this what-if." Resolved to edge ids via
    `resolve_blocked_edge_ids` and unioned with the hazard-derived blocked
    set before both Dijkstra calls -- a scenario-blocked edge is excluded
    from routing exactly like a hazard-blocked one, but reported
    separately in `results["scenario_blocking"]` so the two reasons for
    "this road is unusable here" are never conflated.
    """
    import geopandas as gpd
    import rasterio
    from pyproj import Transformer

    if hazard_dataset_type not in HAZARD_CLASS_MAX_CODE:
        raise ValueError(
            f"Unsupported hazard_dataset_type '{hazard_dataset_type}' for routing. "
            f"Supported: {sorted(HAZARD_CLASS_MAX_CODE)}."
        )

    # --- load + classify + polygonize hazard raster (mirrors Phase 6's own loading step) ---
    with rasterio.open(hazard_path) as src:
        hazard_crs = src.crs
        hazard_array = src.read(1)
        hazard_nodata = src.nodata
        hazard_transform = src.transform
        hazard_bounds = tuple(src.bounds)

    limitations = list(ROUTING_LIMITATIONS)
    reprojected = bool(hazard_crs.is_geographic)
    if reprojected:
        from rasterio.warp import Resampling, calculate_default_transform
        from rasterio.warp import reproject as rio_reproject

        target_crs = pick_utm_crs(hazard_bounds, hazard_crs)
        transform, width, height = calculate_default_transform(
            hazard_crs, target_crs, hazard_array.shape[1], hazard_array.shape[0], *hazard_bounds
        )
        fill_value = hazard_nodata if hazard_nodata is not None else 0.0
        new_array = np.full((height, width), fill_value, dtype="float64")
        # Nearest-neighbor, deliberately not bilinear -- same reasoning as
        # Phase 6: hazard raster values are categorical.
        rio_reproject(
            source=hazard_array,
            destination=new_array,
            src_transform=hazard_transform,
            src_crs=hazard_crs,
            dst_transform=transform,
            dst_crs=target_crs,
            resampling=Resampling.nearest,
            src_nodata=hazard_nodata,
            dst_nodata=hazard_nodata,
        )
        hazard_array = new_array
        hazard_transform = transform
        hazard_crs = target_crs
        limitations.append(UTM_REPROJECTION_LIMITATION)

    codes, valid, labels = classify_hazard_array(
        hazard_dataset_type, hazard_array, hazard_nodata, class_legend_override
    )
    hazard_polygons = polygonize_hazard_raster(codes, valid, hazard_transform, hazard_crs)

    # --- load roads; hazard raster's CRS is the fixed reference (same convention as Phase 6/7) ---
    roads_gdf = gpd.read_file(road_path)
    if roads_gdf.crs is None:
        raise ValueError("Road dataset has no CRS.")
    if roads_gdf.crs != hazard_crs:
        roads_gdf = roads_gdf.to_crs(hazard_crs)

    graph = build_road_graph(roads_gdf, node_snap_tolerance_m)
    hazard_by_edge = overlay_edges_with_hazard(graph.edges, hazard_polygons)

    # Reused for both blocked-segment reprojection (below) and origin/
    # destination reprojection (further down) -- origin/destination/
    # blocked-segment geometries are always WGS84 in the request.
    transformer = Transformer.from_crs("EPSG:4326", hazard_crs, always_xy=True)

    scenario_blocked_edge_ids: set[int] = set()
    if blocked_segment_geometries:
        from shapely.geometry import shape as shapely_shape
        from shapely.ops import transform as shapely_transform

        parsed_geometries = []
        for geom_dict in blocked_segment_geometries:
            geom = shapely_shape(geom_dict)
            if geom.geom_type not in _SUPPORTED_ROAD_GEOM_TYPES:
                raise ValueError(
                    f"Unsupported blocked_segment_geometries geometry type '{geom.geom_type}'. "
                    f"Only {sorted(_SUPPORTED_ROAD_GEOM_TYPES)} are supported."
                )
            parsed_geometries.append(shapely_transform(lambda x, y: transformer.transform(x, y), geom))
        scenario_blocked_edge_ids = resolve_blocked_edge_ids(graph, parsed_geometries, match_buffer_m)

    if vulnerability_weight is not None and consequence_weight is not None:
        hazard_source = "risk_analysis"

        def intensity_fn(code: int, _v=vulnerability_weight, _c=consequence_weight) -> float:
            return compute_risk_score(compute_hazard_intensity_weight(hazard_dataset_type, code), _v, _c)
    else:
        hazard_source = "hazard_only"

        def intensity_fn(code: int) -> float:
            return compute_hazard_intensity_weight(hazard_dataset_type, code)

    costed_edges = apply_hazard_costs(graph.edges, hazard_by_edge, intensity_fn, block_threshold)
    graph = replace(graph, edges=costed_edges)
    edge_by_id = {e.id: e for e in costed_edges}

    hazard_blocked_edge_ids = {e.id for e in costed_edges if e.blocked}
    blocked_edge_ids = hazard_blocked_edge_ids | scenario_blocked_edge_ids
    blocked_edges = [e for e in costed_edges if e.id in blocked_edge_ids]

    # --- reproject origin/destination (always WGS84 in the request) into the working CRS, snap ---
    ox, oy = transformer.transform(origin_lon, origin_lat)
    dx, dy = transformer.transform(destination_lon, destination_lat)

    origin_snap = find_nearest_node(graph.nodes, ox, oy, max_snap_distance_m)
    if origin_snap is None:
        raise ValueError(
            f"Origin point is farther than max_snap_distance_m={max_snap_distance_m} from any road "
            "network vertex."
        )
    destination_snap = find_nearest_node(graph.nodes, dx, dy, max_snap_distance_m)
    if destination_snap is None:
        raise ValueError(
            f"Destination point is farther than max_snap_distance_m={max_snap_distance_m} from any "
            "road network vertex."
        )
    origin_node, origin_snap_distance = origin_snap
    destination_node, destination_snap_distance = destination_snap

    def length_weight(edge: RoadEdge) -> float:
        return edge.length_m

    def hazard_weight(edge: RoadEdge) -> float:
        return edge.length_m + hazard_penalty_weight * edge.hazard_component_m

    shortest = dijkstra(graph, origin_node, destination_node, length_weight, blocked_edge_ids)
    hazard_aware = dijkstra(graph, origin_node, destination_node, hazard_weight, blocked_edge_ids)

    feasible = shortest is not None
    disconnected_due_to_blocking = False
    if not feasible and blocked_edge_ids:
        full_graph_route = dijkstra(graph, origin_node, destination_node, length_weight, set())
        disconnected_due_to_blocking = full_graph_route is not None

    def _route_summary(route: RouteResult | None) -> dict[str, Any]:
        if route is None:
            return {"feasible": False}
        return {
            "feasible": True,
            "distance_m": sum(edge_by_id[eid].length_m for eid in route.edge_ids),
            "hazard_component_m": sum(edge_by_id[eid].hazard_component_m for eid in route.edge_ids),
            "cost_m_equivalent": route.total_cost,
            "edge_ids": route.edge_ids,
        }

    results: dict[str, Any] = {
        "method": METHOD,
        "hazard_dataset_type": hazard_dataset_type,
        "hazard_source": hazard_source,
        "reprojected_hazard_crs": reprojected,
        "crs": hazard_crs.to_string(),
        "hazard_class_labels": labels,
        "hazard_penalty_weight": hazard_penalty_weight,
        "block_threshold": block_threshold,
        "node_snap_tolerance_m": node_snap_tolerance_m,
        "max_snap_distance_m": max_snap_distance_m,
        "graph_summary": {
            "node_count": len(graph.nodes),
            "edge_count": len(costed_edges),
            "blocked_edge_count": len(blocked_edges),
            "total_road_length_m": sum(e.length_m for e in costed_edges),
            "total_blocked_length_m": sum(e.length_m for e in blocked_edges),
        },
        "origin_snapped": {"lon": origin_lon, "lat": origin_lat, "snap_distance_m": origin_snap_distance},
        "destination_snapped": {
            "lon": destination_lon, "lat": destination_lat, "snap_distance_m": destination_snap_distance,
        },
        "feasible": feasible,
        "disconnected_due_to_blocking": disconnected_due_to_blocking,
        "shortest_route": _route_summary(shortest),
        "hazard_aware_route": _route_summary(hazard_aware),
        "limitations": limitations,
    }
    if blocked_segment_geometries:
        results["scenario_blocking"] = {
            "submitted_geometry_count": len(blocked_segment_geometries),
            "match_buffer_m": match_buffer_m,
            "resolved_edge_ids": sorted(scenario_blocked_edge_ids),
            "resolved_edge_count": len(scenario_blocked_edge_ids),
        }

    shortest_gdf = None
    if shortest is not None and shortest.edge_ids:
        shortest_gdf = gpd.GeoDataFrame(
            {"route_type": ["shortest"]}, geometry=[build_route_line(shortest, edge_by_id)], crs=hazard_crs
        )
    hazard_aware_gdf = None
    if hazard_aware is not None and hazard_aware.edge_ids:
        hazard_aware_gdf = gpd.GeoDataFrame(
            {"route_type": ["hazard_aware"]},
            geometry=[build_route_line(hazard_aware, edge_by_id)],
            crs=hazard_crs,
        )
    blocked_gdf = None
    if blocked_edges:
        blocked_gdf = gpd.GeoDataFrame(
            {
                "edge_id": [e.id for e in blocked_edges],
                "max_class_intensity": [e.max_class_intensity for e in blocked_edges],
                "length_m": [e.length_m for e in blocked_edges],
            },
            geometry=[e.geometry for e in blocked_edges],
            crs=hazard_crs,
        )

    return RouteAnalysisComputation(
        results=results,
        working_crs=hazard_crs.to_string(),
        shortest_route_gdf=shortest_gdf,
        hazard_aware_route_gdf=hazard_aware_gdf,
        blocked_segments_gdf=blocked_gdf,
    )
