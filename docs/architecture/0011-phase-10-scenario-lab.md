# ADR 0011: Phase 10 Scenario Lab

Date: 2026-09-19
Status: Accepted

## Context

Phase 9 gave every project one coherent, queryable Digital Twin "current
state," composed from Phases 1-8's real outputs. It deliberately does not
answer "what if": what if the flood depth assumption were higher, what if
a different DEM-derived hazard run were used instead of the twin's
current one, what if a road segment were blocked? Phase 10 (Scenario Lab)
adds exactly that -- a controlled, parallel hypothetical state built from
a twin's frozen baseline, without ever mutating the twin and without
reimplementing any of Phases 2-8's actual science.

## Decision

### Orchestration and comparison, not computation

`app/api/scenarios.py` imports and calls, verbatim, the exact Phase 4/6/7/8
service functions those phases already expose: `run_flood_scenario`,
`run_landslide_scenario`, `run_exposure_analysis`, `run_risk_analysis`,
`run_route_analysis`. No hazard, exposure, risk, or routing algorithm is
reimplemented in this phase. The one piece of genuinely new logic --
`resolve_blocked_edge_ids` in `app/services/routing.py` -- is geometric
glue (buffer a caller-supplied geometry, mark intersecting graph edges),
the same class of code as the pre-existing `overlay_edges_with_hazard`/
`find_nearest_node`, not a new modeling algorithm. `app/services/
scenario.py`'s comparison functions (`diff_by_class`, `diff_route_
analysis`) are pure aggregation over two already-computed `results`
dicts -- delta/pct-change arithmetic, never a new score or model.

### Frozen baseline snapshot, not a live reference

At scenario creation, every currently-`active` `TwinLayer` for the parent
`DigitalTwin` is copied *by reference* (FK to the same `Dataset`, not a
data copy) into `scenario_baseline_layers`. After that moment the
scenario's baseline never changes, even if the twin's active layers are
later superseded or retired -- proven directly in the end-to-end smoke
test (`test_full_pipeline_smoke_twin_to_scenario_lab`), which supersedes
the twin's hazard layer after scenario creation and asserts the scenario
still resolves the *original* dataset.

This was the single largest either-way judgment call in this phase. A
live reference (re-querying the twin's active layers on every read) would
have been simpler to implement, but would let a scenario's meaning
silently drift out from under whoever created it, purely because someone
else changed the twin later -- a non-reproducible result, which conflicts
with this codebase's existing discipline of storing complete, frozen
`results`/`provenance` records rather than re-derivable live views
(exactly what every Phase 4-8 analysis already does for its own inputs).

### Overrides: a normalized table, and reject-on-duplicate

`scenario_layer_overrides` mirrors `TwinLayer`'s own separate-table
pattern -- a real FK to a validated `Dataset`, never a bare id in
unstructured JSON. Unlike `TwinLayer` registration (which auto-supersedes
a prior active layer of the same `dataset_type`, because a twin
represents an evolving *current* state), a second override for the same
`dataset_type` on one scenario is rejected with 409: a scenario
represents one coherent hypothesis, so a conflicting override is far more
likely a mistake than an intentional revision, and must be explicitly
removed via `DELETE` first.

### Immutability is scoped per dataset_type, not scenario-wide -- a correction made during implementation

The originally reviewed plan froze *all* of a scenario's overrides the
moment *any* analysis run existed on it. Building the real end-to-end
smoke test surfaced that this blocks the phase's own central workflow:
run an alternate hazard scenario, register its output as this scenario's
hazard override, then run exposure/risk/route against that override. The
hazard run itself already counts as "a run," so the override registration
step -- which must happen *after* the hazard run produces its output
dataset -- was unconditionally rejected.

The fix, confirmed with the user before implementing: `can_mutate_
overrides(dataset_type, consumed_dataset_types)` freezes only the
`dataset_type` that some run on this scenario has actually resolved and
consumed as an input, derived from each run record's own denormalized
input fields (`HazardScenario.input_datasets` keys, `ExposureAnalysis`/
`RiskAnalysis`/`RouteAnalysis`'s `hazard_dataset_type`/
`exposure_dataset_type`, and `"roads"` whenever a `RouteAnalysis.
road_dataset_id` is set) -- no extra bookkeeping table needed. A hazard
run that only ever consumes `"dem"` never blocks a later override of
`"landslide_susceptibility"`, which the hazard run only *produces*, never
reads. This is safe precisely because every scenario-triggered analysis
record is self-contained: it stores its own resolved dataset ids in its
own `parameters`/`*_dataset_id` columns at creation time, exactly like
every Phase 4-8 analysis already does, so there is no live binding a past
run depends on that a later override could invalidate. `ScenarioStateRead`
surfaces this as `can_override: bool` on each `effective_layers` entry,
not as a single scenario-wide flag.

### Blocked road segments: additive inline geometry, not a new Dataset type

Confirmed via inspection of `app/services/routing.py`: no caller-supplied
road-blocking input existed before this phase, and graph edge ids are
`position-in-exploded-list` at build time -- not stable across runs or
datasets -- so they can never be a scenario's primary blocking contract.
`run_route_analysis` gained two purely additive keyword arguments,
`blocked_segment_geometries: list[dict] | None = None` and
`match_buffer_m: float = 5.0`, defaulting to `None`/no-op so every
existing Phase 8 caller and test is unaffected -- verified by the full
pre-existing routing test suite passing unchanged. Submitted GeoJSON
LineString/MultiLineString geometries (WGS84, same convention as origin/
destination) are reprojected into the working CRS and resolved to edge
ids via `resolve_blocked_edge_ids`, unioned with the hazard-derived
blocked set before both Dijkstra calls, and reported separately in
`results["scenario_blocking"]` so "blocked by hazard" and "blocked by
this what-if" are never conflated. A Dataset-referenced alternative (a
reusable, uploaded blocked-segments layer) was considered and explicitly
deferred -- see Consequences.

### `scenario_id` added to the four run-record tables, not to `Dataset`

Unlike Phase 9 (which added zero columns to `datasets`), Phase 10 needed
one new nullable `scenario_id` column on `hazard_scenarios`,
`exposure_analyses`, `risk_analyses`, and `route_analyses` -- ordinary
one-directional foreign keys to `scenarios`, since `scenarios` has no FK
pointing back at any of these four tables (no `use_alter=True` circular-FK
dance needed, unlike every `datasets <-> {hazard_scenarios, ...}` pair).
`datasets` itself gained **no** new column: Scenario Lab produces no new
kind of `Dataset` row of its own -- its outputs are ordinary hazard/
exposure/risk/route-analysis outputs, already reachable one hop away via
the existing `hazard_scenario_id`/`exposure_analysis_id`/
`risk_analysis_id`/`route_analysis_id` columns Phases 4-8 already added.
Adding a second, redundant `scenario_id` directly on `Dataset` would be
denormalization with no query that needs it un-joined.

### No branching

Every `Scenario` is rooted directly at one `DigitalTwin` (`twin_id`,
non-nullable). There is no `parent_scenario_id`, no scenario-of-a-
scenario, no duplicate/branch endpoint. ADR 0010 named Phase 10 as the
deferral target for "multi-scenario branching"; this phase reads that
narrowly -- branching itself, not the whole phase -- and leaves it fully
out of scope rather than half-building it.

### Comparison is diffing, not modeling

`POST /scenario-comparisons` always compares two *explicit* analysis
records of the same type (`exposure`/`risk`/`route`) -- there is no
implicit "the twin's canonical result," because a twin never runs
exposure/risk/route analyses itself. The diff is pure aggregation over
already-computed `results` JSON: per-class numeric deltas and
pct-changes for exposure/risk, and per-named-route metric deltas plus a
graph-level blocked-edge summary for routes. A `hazard_dataset_type`
mismatch between the two sides is surfaced as a `comparability_warnings`
entry, never silently reconciled -- the same disclosure convention as
`crs_mismatch_layer_ids` in Phase 9.

### Digital Twin non-mutation guarantee

No code path in this phase writes to `digital_twins` or `twin_layers`.
`scenario_baseline_layers` only reads and copies FK references at
creation time. Promoting a scenario's output into the real twin remains a
deliberate, manual action through the existing Phase 9
`POST /digital-twins/{id}/layers` endpoint -- Phase 10 adds no "promote"
shortcut. `test_scenario_lifecycle_never_mutates_digital_twin` asserts
`GET /digital-twins/{id}/state` and `GET /digital-twins/{id}/layers?
status=all` are byte-identical before and after a full scenario
lifecycle (creation, a hazard run, a layer override, archiving).

## Consequences

- No new dependencies. Blocked-segment geometry matching reuses
  `shapely`/`geopandas` primitives already imported in `routing.py`. No
  RQ/Redis -- every operation is a small synchronous DB read/write plus a
  call into an already-synchronous existing service function, the same
  cost class as every prior phase's own synchronous-computation
  justification.
- 20 new pure-function tests (`test_scenario_lab.py`) + 16 new API
  integration tests (`test_scenario_lab_api.py`, including one full Phase
  2-9 -> Scenario Lab end-to-end smoke test). 351 total in the suite, all
  passing (up from 315).
- Frontend unchanged -- API-only, same as every phase since Phase 2.
- Deferred: Dataset-referenced (reusable, uploaded) blocked-segments
  layers, as an alternative to inline geometry; an auto-resolved
  "compare to baseline" convenience endpoint (MVP ships only the
  explicit-two-analysis-id comparison); scenario branching / any
  `parent_scenario_id` bookkeeping; a "promote scenario output into the
  real twin" shortcut; any forecasting/prediction (Scenario Lab stays
  "controlled what-if over already-modeled data," never a prediction
  engine); live sensor/traffic/weather feed ingestion (no such data
  source exists anywhere in this system); 2D/3D visualization, MapLibre/
  Three.js rendering, and a polished comparison UI (Phase 11 Command
  Dashboard).
