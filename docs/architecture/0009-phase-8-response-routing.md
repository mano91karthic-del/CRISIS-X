# ADR 0009: Phase 8 Response + Hazard-Aware Routing

Date: 2026-09-17
Status: Accepted

## Context

Phase 4 produces classified hazard rasters; Phase 7 produces per-hazard-
class risk scores (`hazard_intensity_weight × vulnerability_weight ×
consequence_weight`, ADR 0008). Neither phase touches road networks or
pathfinding. Phase 8 is the first phase to perform genuine graph
computation: build a routable graph from user-uploaded road geometry, cost
its edges against a modeled hazard/risk layer, and compute both a shortest
route and a hazard-aware route via a real shortest-path algorithm — while
being explicit that "safest" means "minimum modeled hazard cost in this
network model," never a guarantee of physical safety, and never inventing
live conditions (traffic, closures, emergency-service availability) the
system has no data for.

## Decision

### Graph algorithm: Dijkstra, not networkx

`app/services/routing.py::dijkstra` is a hand-rolled binary-heap Dijkstra
(stdlib `heapq`), the same precedent `app/services/terrain.py` already set
with `build_flow_graph`/`compute_flow_accumulation`. `requirements.txt` has
no networkx/scipy/rtree; at hackathon-scale road networks (hundreds to
low-thousands of edges), `O((V+E) log V)` Dijkstra is trivially fast, and
adding a graph-library dependency for this would be disproportionate. A*
was considered (a Euclidean-distance heuristic remains admissible for both
weight functions, since every edge weight is `>= length_m` given
`hazard_penalty_weight >= 0`) but not implemented — no performance need at
current scale, and plain Dijkstra is simpler to keep correct.

### Connectivity model: endpoint-snapping only (no topological noding)

`build_road_graph` derives nodes from line endpoints, snapped together via
a coordinate-grid key (`round(x/tolerance), round(y/tolerance)`) if within
`node_snap_tolerance_m` of each other. This is the single biggest
simplification in Phase 8: **two road features that geometrically cross
without sharing an endpoint vertex there are NOT treated as connected** —
no splitting of lines at true intersections is performed. This holds for
most authoritative road datasets (OSM extracts, government GIS layers,
anything already edited for network analysis) but not for arbitrary
hand-drawn lines. It is stated in every response's `limitations`, not
silently assumed. Full topological noding is deferred (see Deferred).

### Hazard-raster-CRS-is-reference, continued unbroken from Phase 6/7

The hazard raster's CRS (after its own UTM auto-resolution, same
`pick_utm_crs` reuse as Phase 2/6/7) is the fixed reference; the road
network is reprojected to match it via `.to_crs()`. This keeps the
convention identical across Phase 6 (exposure reprojected to match hazard),
Phase 7 (inherits Phase 6's CRS), and now Phase 8 — never flipped.

### Hazard-class → intensity: reused from Phase 6/7, never reinvented

`build_road_graph`'s edges are overlaid against hazard-class polygons using
the **exact same** `classify_hazard_array`/`polygonize_hazard_raster`
(Phase 6, imported not duplicated) as every other phase that touches a
hazard raster. Per-edge, per-class intersection length comes from the same
`gpd.overlay(..., how="intersection")` primitive `overlay_lines` (Phase 6)
already uses for line-asset exposure — Phase 8 just keeps `edge_id`
through the groupby instead of dissolving network-wide, to get per-edge
rather than per-network totals.

Hazard-class intensity is **the same, already-documented Phase 7 mapping**:
`compute_hazard_intensity_weight` (hazard-only mode) or
`compute_hazard_intensity_weight` + `compute_risk_score` (risk-aware mode),
imported directly from `app/services/risk.py` — never a new, undocumented
number. This satisfies "do not turn a hazard raster class into a
meaningless arbitrary number without documenting the mapping" by
construction: there is no separate Phase 8 mapping to document, because
there isn't one.

**Important, discovered empirically while building the test fixtures**:
landslide susceptibility classifies the *entire* valid raster extent, not
just visibly "hazardous" cells — even class 1 ("very_low") carries a
nonzero baseline intensity (`1/5 = 0.2`), because `hazard_intensity_weight
= class_code / max_class_code` and there is no "class 0 = no hazard" in the
Phase 4 scheme. One consequence: a longer detour around a higher hazard
class does **not** automatically reduce total hazard exposure — it can
easily accumulate *more* total exposure than a shorter route through worse
terrain, since every meter of the detour still costs at least the
baseline-class intensity. Only genuinely **unassessed** ground (outside the
hazard raster's valid/classified extent — nodata or out of bounds) 
contributes zero. This is not a bug; it is the honest behavior of the
formula, and it's exactly why the `limitations` list states "areas outside
the hazard raster's extent... are not assessed... and are not assumed
safe" rather than "avoiding the raster is always safer."

### Risk-aware mode: recomputed fresh per class, and gated to the SAME exposure asset

When `risk_analysis_id` is supplied, intensity per class is recomputed via
`compute_risk_score(compute_hazard_intensity_weight(...), risk_analysis
.vulnerability_weight, risk_analysis.consequence_weight)` for every class
actually present in the road network's hazard overlay — **not** read back
out of `RiskAnalysis.results["by_class"]`, which only contains classes that
happened to overlap whatever asset that risk analysis's *original* exposure
run used. Since `risk_score` is a pure function of
`(hazard_class_code, vulnerability_weight, consequence_weight)`, recomputing
it is mathematically identical to Phase 7's own output for any class it
covers, and correctly extends to classes it didn't happen to cover.

This was **not** enough on its own, and review of the initial plan added a
second, required gate: `risk_analysis.exposure_dataset_id` must equal the
route's `road_dataset_id` — literally the same `Dataset` row must have been
used both as the roads dataset being routed over and as the exposure input
to the `ExposureAnalysis` underlying that `RiskAnalysis`. Reasoning: Phase
7's `vulnerability_weight`/`consequence_weight` are assumptions declared
for a *specific exposure asset type* (see ADR 0008 — "how susceptible is
this exposed asset type," "how severe is it if this asset type is
affected"). Recomputing the formula per-class is mathematically sound
regardless of geometry, but the *assumption itself* is not — a
vulnerability/consequence pair declared while thinking about hospitals must
never be silently reused for roads. The required pipeline is therefore:
roads → Phase 6 exposure analysis using those same roads as the exposure
input → Phase 7 risk analysis on that exposure analysis → Phase 8
risk-aware routing referencing that same roads dataset + hazard dataset +
risk analysis. Enforced in `app/api/routing.py::_get_risk_analysis_or_400`
as two checks: `risk_analysis.hazard_dataset_id == hazard_dataset_id` and
`risk_analysis.exposure_dataset_id == road_dataset_id`, both 400 on
mismatch, both covered by dedicated tests
(`test_risk_analysis_hazard_mismatch_rejected`,
`test_risk_analysis_from_different_exposure_dataset_rejected`).

### Blocking is conservative and structurally separate from the penalty

An edge is blocked if **any** portion of it, however short, touches a
hazard class at or above `block_threshold` (default `1.0`, i.e. only the
single worst class at full intensity blocks by default — landslide's class
5 or flood's single "inundated" class or EO's "changed" class, all of which
normalize to exactly `1.0`). Blocked edges are removed from **both** graphs
before either route is computed — blocking is an availability constraint
("this segment cannot be used"), never a preference, so a "shortest route"
running straight through a blocked segment would be meaningless.
`hazard_penalty_weight` (distance-vs-hazard tradeoff) and `block_threshold`
(hard exclusion) are two structurally separate mechanisms, never conflated:
lowering the penalty never un-blocks an edge, and raising the penalty never
blocks one.

### "Safest feasible" ≠ guaranteed safe, and ≠ lowest raw hazard exposure

The hazard-aware route minimizes a single **combined** objective per edge,
`distance_m + hazard_penalty_weight × hazard_component_m` — it does not
independently minimize `hazard_component_m`. Distance and hazard cost are
traded off against each other in one weighted sum, so a valid hazard-aware
route can legitimately end up with a slightly *higher* raw
`hazard_component_m` than some other candidate route while still having a
lower combined cost overall (e.g. a marginally shorter detour that isn't
quite as hazard-free as a longer alternative can still win if the extra
distance of the longer alternative outweighs its hazard savings once
`hazard_penalty_weight` is applied). The only property the algorithm
actually guarantees is: **the hazard-aware route's combined cost is `<=`
the combined cost of evaluating the shortest-by-distance route under the
same `hazard_penalty_weight`** (Dijkstra found the minimum over the same
weight function across the same graph, and the shortest-by-distance path is
just one candidate path within it). Every route result's `limitations`
states, verbatim: *"'hazard-aware/safest feasible route' means the route
minimizing the combined cost `distance_m + hazard_penalty_weight *
hazard_component_m` in this network model — it does not independently
minimize raw hazard exposure, and it is not proven to be free of hazard."*
Plus: no guarantee of physical safety, no real-time conditions, no
traffic/closures/emergency-service data (none of which exists anywhere in
this system) — stated once, structurally, not left to documentation alone.

### Node/point snapping: brute-force NumPy, not a new spatial-index dependency

`find_nearest_node` computes distances to every graph node with plain
NumPy array arithmetic. At hackathon-scale node counts this is trivially
fast and needs no new dependency (`geopandas`/`shapely`'s built-in STRtree
was considered but wasn't even necessary once the brute-force approach
proved simple and fast enough).

### Database models and lineage

`RouteAnalysis` (`app/models/route_analysis.py`) follows the exact
`HazardScenario`/`ExposureAnalysis`/`RiskAnalysis` shape: no
`relationship()` to `Project` (FK column only). It stores `road_dataset_id`,
`hazard_dataset_id` (+ denormalized `hazard_dataset_type`), optional
`risk_analysis_id`, origin/destination as plain lon/lat floats, all four
routing parameters as first-class columns (not buried only in JSON), and
`results` (JSON) for the full route/graph/limitations payload — same
"parameters as columns, full breakdown as JSON" split `RiskAnalysis`
already established.

`Dataset` gains `origin = "route_analysis"` and a `route_analysis_id` FK
(`ondelete="SET NULL"`, `use_alter=True`,
`name="fk_datasets_route_analysis_id"`) — the same circular-FK situation as
the four prior additions (`RouteAnalysis.hazard_dataset_id`/
`road_dataset_id` point at `datasets.id`; `datasets.route_analysis_id`
points back). Migration `cad56025e1f1` follows the exact two-step recipe
(`op.create_table('route_analyses', ...)` with forward FKs, then a
`batch_alter_table('datasets', ...)` adding the deferred circular FK)
established by `84bfd6d203a3`/`2dcc1df5f1a5`/`3fbdc30da2cb`/`0aa54f44eedb`.
It was **hand-written**, not autogenerated, because the local dev SQLite DB
was still at the Phase 6 migration (applying Phase 7's migration was
blocked as a shared-resource modification in an earlier session) — the
migration's correctness was instead verified structurally (`alembic
heads`/`history` show a clean single-head chain) and functionally (the full
test suite, which builds its schema via `Base.metadata.create_all()`
independent of Alembic, exercises every column/constraint this migration
declares).

### Output datasets

Up to three optional `Dataset` rows per analysis: `route_shortest`,
`route_hazard_aware` (each a GeoJSON LineString built by
`build_route_line`, which concatenates edge geometries in path order rather
than relying on `shapely.ops.linemerge` — snapped nodes can have slightly
different *actual* edge-endpoint coordinates within `node_snap_tolerance_m`
of each other, which `linemerge`'s exact-coincidence matching would not
reliably stitch), and `route_blocked_segments` (produced whenever
`blocked_edge_count > 0`, independent of route feasibility). All
free-string derived-product names, not `DatasetType` enum members, same
convention as `flood_inundation`/`exposure_features`/`risk_classification`.

## Consequences

- No new dependencies — `heapq` (stdlib) for Dijkstra, plain NumPy for node
  snapping, everything else (`rasterio`, `geopandas`, `shapely`, `pyproj`)
  already installed since Phase 1/2/6/7.
- No RQ/Redis — graph construction + one geometric overlay + Dijkstra over
  a hackathon-scale network is well within a synchronous request, same
  reasoning every prior phase used.
- 25 new pure-function tests (`test_routing.py`) + 17 new API integration
  tests (`test_routing_api.py`, including one full DEM→hazard→exposure→
  risk→route pipeline smoke test walking lineage purely from response ids,
  and dedicated tests proving both the hazard-dataset-match and
  exposure-dataset-match gates on `risk_analysis_id`). 280 total in the
  suite, all passing.
- Frontend unchanged — API-only, inspectable via `/docs`, same as Phases
  2-7. Route visualization belongs to Phase 11 (Command Dashboard).
- Deferred: full topological noding (auto-splitting road lines at true
  geometric crossings that don't share a vertex); multi-stop routing,
  isochrones, evacuation-zone-wide batch routing; accepting an existing
  point Dataset (hospital/shelter) as origin/destination instead of raw
  lon/lat; A*/other performance optimizations; live traffic, real-time road
  closures, emergency-service availability (no data source exists for any
  of these — explicitly excluded, not approximated); directionality/
  one-way streets (every edge is bidirectional — no attribute in the roads
  dataset contract currently carries this); road capacity/width/vehicle-
  class constraints; external routing/map API integration; multi-hazard
  route costing (v1 stays single-hazard, consistent with Phase 6/7's
  1×1(×1) scope discipline).
