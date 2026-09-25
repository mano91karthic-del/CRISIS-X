# ADR 0010: Phase 9 Digital Twin

Date: 2026-09-18
Status: Accepted

## Context

Every phase from 4 onward added a new *computation* whose real geospatial
output always lands as an ordinary `Dataset` row with provenance (terrain
derivation, hazard modeling, EO change detection, exposure overlay, risk
scoring, routing). Phase 9 does not add a new computation at all — it adds
a *composition* layer: a per-project registry that organizes the datasets
and analysis outputs already produced by Phases 1–8 into one coherent,
queryable "current state," with clear labels for what's observed vs.
modeled, what assumptions each modeled layer carries, and what's missing
or inconsistent — without recomputing, re-deriving, or duplicating
anything Phases 2–8 already computed.

## Decision

### Composition, not computation — and this is structurally provable, not just claimed

Every phase since Phase 4 has needed to touch the `datasets` table: a new
`DatasetOrigin` value, and a new nullable FK column (`hazard_scenario_id`,
`eo_change_analysis_id`, `exposure_analysis_id`, `risk_analysis_id`,
`route_analysis_id`) resolved via the `use_alter=True` circular-FK dance,
because each phase's output was a *new* `Dataset` row that needed to record
which run produced it. **Phase 9 adds none of that.** Migration
`103163b987c2` creates two new tables and touches `datasets` not at all —
no new `DatasetOrigin`, no new column, no circular FK. This isn't an
oversight; it's a direct, load-bearing consequence of the design: Digital
Twin produces zero new `Dataset` rows, so there is nothing for `datasets`
to need to point back to. If a future change to this phase ever needed to
add a column to `datasets`, that would be a signal the phase had drifted
into doing computation again, and worth re-examining against this ADR.

### Layer "role" = `Dataset.dataset_type`, verbatim

No parallel taxonomy was invented. `TwinLayer` has no separate `role` or
`slot` field — `"flood_inundation"`, `"landslide_susceptibility"`,
`"roads"`, `"dem"`, `"risk_classification"`, `"route_hazard_aware"` are
already the system's vocabulary from Phases 1–8, and replacement (see
below) keys off this same field directly.

### Category = `Dataset.origin`, mapped, not reinvented

`derive_layer_category` (`app/services/digital_twin.py`) is a small,
total, fail-closed mapping from the existing `DatasetOrigin` enum to one of
six categories (`observation`, `terrain`, `hazard`, `exposure`, `risk`,
`route`) — `test_derive_layer_category_matches_every_dataset_origin_enum_value`
asserts every `DatasetOrigin` member has an entry, so the mapping can never
silently go stale as new origins are added in future phases; an
unrecognized origin raises rather than guessing a bucket.

One deliberate simplification: EO change-detection outputs (`origin
="eo_analysis"`) are categorized as `"hazard"`, not a separate
`"change_detection"` category — even though they're derived from real
observed imagery via a real algorithm (CVA/Otsu), not a parametrized
scenario model like flood/landslide. This matches how the rest of the
system already treats them (`SUPPORTED_HAZARD_TYPES` in
`app/services/exposure.py` includes `eo_change_mask` alongside the two
hazard-model outputs) rather than introducing a seventh category nothing
else in the system uses.

### Assumptions and limitations: nested, never copied

`TwinLayer.provenance` records **only** registration-time facts —
`registered_at_version`, `superseded_layer_id`, `dataset_type`, `category`
— never a copy of the underlying `Dataset.provenance`. Every phase from 4
onward already merges `method`/`parameters`/`limitations` onto its output
`Dataset.provenance` at creation time; Phase 9 satisfies "distinguish
observations, modeled outputs, and assumptions" by **nesting** the full
`DatasetRead` (including its own `provenance`/`metadata_json`) inside every
`TwinLayerResult`, never re-storing a second copy that could drift out of
sync with the source of truth. The end-to-end smoke test
(`test_full_pipeline_smoke_terrain_to_twin`) asserts this directly: every
layer's provenance, re-fetched through the twin, is identical to what it
was at the moment Phase 4/6/7/8 itself produced it.

### Replacement: supersede, never delete

Registering a `Dataset` whose `dataset_type` matches an existing **active**
`TwinLayer` flips the old layer to `status="superseded"` (never deleted —
the same lineage-survives policy every prior phase applies via `SET NULL`
FKs rather than cascading deletes) and creates a new `active` layer
recording `provenance.superseded_layer_id`. Enforced at the API layer
(`app/api/digital_twin.py::register_twin_layer`), not a DB constraint —
same precedent as Phase 6's "one hazard + one exposure" being enforced by
request shape rather than a partial unique index, avoiding a SQLite/
Postgres divergence risk. A layer can also be explicitly **retired**
(`POST /twin-layers/{id}/retire`) with no replacement, for "this is no
longer relevant" without superseding it with something new. There is no
hard-delete endpoint anywhere in this phase.

### Versioning: a change counter, not a snapshot system

`DigitalTwin.version` increments by one on every registration, supersession,
or retirement. This is deliberately lightweight — not a full snapshot/
branch history. `TwinLayer.registered_at_version` gives a cheap way to ask
"what became active between version 3 and 5" without a separate event-log
table, but multi-scenario branching or comparing two simultaneous "what if"
states is explicitly **not** built here. That's the reason Phase 10
("Scenario Lab") exists as its own phase rather than being folded into
this one.

### Extent: reference-CRS-by-category-priority, reprojected on demand, never forced to UTM

`compute_twin_extent` never stores a cached extent (avoiding staleness as
layers are replaced) and never forces a "nicer" projection the way
`pick_utm_crs` does elsewhere — it has no area/length calculation to
justify that. It picks a **reference CRS** from the active layer with the
highest category priority (`terrain > hazard > exposure > risk > route >
observation`, preferring the DEM/terrain layer as the natural spatial
anchor of "a place") that actually has one, then reprojects every other
active layer's bbox corners into it via a plain `pyproj.Transformer` — pure
coordinate math on numbers already stored on `Dataset`, no
`rasterio`/`geopandas` file I/O anywhere in this phase. Any layer whose
native CRS differs from the reference is flagged in
`crs_mismatch_layer_ids`, never silently folded into the union without
disclosure — the same `reprojected_hazard_crs`/`crs_mismatch` flag pattern
already used in Phases 3/6/7/8. Layers with no CRS or no bbox (e.g. a
tabular CSV upload) are excluded from the union and listed in
`layers_without_extent`, never silently dropped without a trace.

### Timestamps: reported, never guessed

`compute_acquisition_date_range` reports the earliest/latest
`acquisition_date` among active layers that have one and lists which don't
— it adds no new date logic, it only aggregates what `Dataset
.acquisition_date` already enforces ("never fabricated" per that column's
own long-standing comment). Widely differing acquisition dates across
layers are surfaced, not reconciled.

### `missing_recommended_layers`: advisory only

A fixed checklist (`terrain`, `hazard`, `exposure`, `risk`) is compared
against the categories actually present, purely as a soft hint — never a
validation failure, never blocking twin creation or layer registration. A
valid twin for an AOI with no road network legitimately has no `route`
layer, which is exactly why `route` (and `observation`, which is an input
category, not an end state) is deliberately not on the checklist.

### No `status`/`error_message` on the run-record model — a deliberate departure

Every prior run-record model (`HazardScenario`, `EOChangeAnalysis`,
`ExposureAnalysis`, `RiskAnalysis`, `RouteAnalysis`) has a `status`
(`completed`/`failed`) and `error_message`, because each represents a
fallible computation that opens a file, reprojects a raster, or runs an
algorithm that can throw partway through. `DigitalTwin`/`TwinLayer` have
neither: registering a layer is deterministic CRUD over an
already-`validated` `Dataset` row's existing metadata columns — there is no
computation to fail partway through. This is flagged here explicitly as an
intentional shape difference, not an inconsistency with the established
pattern.

## Consequences

- No new dependencies — `pyproj` (already installed since Phase 1/2) for
  bbox corner reprojection is the only library this phase touches beyond
  SQLAlchemy/FastAPI/Pydantic; no `rasterio`/`geopandas` anywhere, since no
  file is ever opened.
- No RQ/Redis — every operation is a small, synchronous DB read/write plus
  in-memory coordinate math, strictly cheaper than any prior phase's own
  synchronous-computation justification.
- 19 new pure-function tests (`test_digital_twin.py`) + 16 new API
  integration tests (`test_digital_twin_api.py`, including one full
  Phase 2→8 pipeline → Digital Twin end-to-end smoke test that registers
  every resulting output dataset and proves no provenance was recomputed).
  315 total in the suite, all passing.
- `Project` (`app/models/project.py`) is untouched — no new
  `relationship()`, no new column — consistent with the established
  convention that run-record-shaped tables are queried by `project_id` FK,
  never via ORM traversal from `Project`.
- Frontend unchanged — API-only, inspectable via `/docs`, same as every
  phase since Phase 2. `apps/web/src` remains exactly Phase 1's Data Hub
  UI; Phase 9 introduces nothing for it to conflict with.
- Deferred: multi-scenario branching/what-if comparison (Phase 10 Scenario
  Lab's reason to exist); any forecasting/prediction from the assembled
  state (Digital Twin ≠ prediction engine, per the task's own boundary);
  live sensor/traffic/weather feed ingestion (no such data source exists
  anywhere in this system; never approximated); 2D/3D visualization,
  MapLibre/Three.js rendering (Phase 11 Command Dashboard); automatic
  CRS/extent/timestamp reconciliation (this phase only flags inconsistency,
  never resolves it); layer-level access control / multi-user editing
  conflicts (no multi-user concept exists anywhere in the system yet); hard
  deletion of twins or layers (only creation, supersession, and retirement
  exist, consistent with every prior phase's lineage-survives policy).
