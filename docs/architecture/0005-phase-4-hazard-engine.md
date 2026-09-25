# ADR 0005: Phase 4 Flood + Landslide Hazard Engine

Date: 2026-09-16
Status: Accepted

Roadmap numbering from here on: **Phase 4 is the Hazard Engine** (flood +
landslide combined), **Phase 5 is Earth Observation AI / Change Detection**.
This supersedes the ad hoc "Phase 5/6" references in ADR 0003.

## Context

The first real hazard-modeling layer. Everything here must produce
scenario/screening outputs, never a claimed deterministic prediction, and
every result must preserve inputs, method, parameters/assumptions, CRS,
timestamp, provenance, and uncertainty/limitations — directly per the
project's foundational "no fabricated scientific results" rule.

## Decision

### Flow direction/accumulation live in Phase 2's terrain.py, not a new module

They're general, deterministic, scenario-independent terrain-characterization
products — the same category as slope/aspect — so they extend
`app/services/terrain.py` and the `/derive` endpoint (`DerivableProduct` now
includes `flow_direction`/`flow_accumulation`) rather than living in
hazard-specific code. Phase 4 is the consumer that justifies building them
now (ADR 0003's deferral was conditional on exactly this), but the
capability itself belongs where slope/aspect are.

**Depression filling**: a simplified priority-flood algorithm (Barnes et
al.), pure NumPy/stdlib `heapq` — necessary because flow accumulation
without pit handling breaks on any DEM with local noise. Limitation:
no flat-area flow-resolution refinement (e.g. Garbrecht & Martz); cells
not 8-connected to the raster border through valid cells are left
undefined (physically reasonable, not a bug).

**D8 flow direction**: ESRI-style codes (1/2/4/8/16/32/64/128 for
E/SE/S/SW/W/NW/N/NE), steepest-descent-per-real-distance, with `0` as an
explicit non-nodata "undetermined" value (edge/flat) distinct from
`nodata=-9999`.

**Flow accumulation**: Kahn's topological sort over the D8-implied directed
forest (each cell has at most one outgoing edge, so no cycles are possible —
flow direction always requires a strict elevation drop), propagating a
cell-count (not area/discharge) total downstream. `build_flow_graph`'s
topological order is reused, in reverse, by HAND (one sort, two consumers).

**Shared reprojection**: `derive_terrain_product`'s CRS-reprojection block
was factored out into `load_projected_elevation` (used by slope/aspect,
flow_direction/accumulation, and the flood hazard module). Because
reprojection is deterministic, independently-derived products of the same
DEM always land on an identical grid — the flood module can combine the
DEM, flow_direction, and flow_accumulation rasters cell-by-cell with no
extra alignment step, avoiding the classic multi-raster-misalignment
pitfall by construction rather than by checking.

### Flood: HAND (Height Above Nearest Drainage) screening

A respected, peer-reviewed simplified method (Nobre et al. 2011; used
operationally by NOAA for rapid flood mapping) — the honest ceiling of
what's implementable without a calibrated hydraulic model or discharge
data. For each cell, `HAND = elevation − elevation of the nearest
downstream channel cell` (a channel cell is one whose flow accumulation
meets a `channel_threshold_cells` parameter), walked along the D8 path
using the **original** (unfilled) elevation for the subtraction — filling
is routing topology only and would distort real depth-above-channel values.

**`depth_above_drainage_m` is always a direct, user-supplied scenario
parameter — never derived from rainfall.** Converting rainfall depth to
flood extent requires a rainfall-runoff model (infiltration, routing,
channel conveyance) CRISIS-X has no legitimate calibration source for;
inventing a conversion coefficient would itself be a fabricated scientific
result. A scenario may optionally reference a rainfall dataset for
human-readable context, recorded in provenance, with **zero effect** on the
computed result — proven by a dedicated test
(`test_flood_scenario_valid_rainfall_reference_recorded_but_inert`).

A cell is "inundated" if `HAND < depth_above_drainage_m` (and HAND is
defined); output depth = `depth_above_drainage_m − HAND` there.

### Landslide: slope-only susceptibility screening

Deliberately the most conservative defensible design. Per factor
considered:

- **Slope** — the only scoring factor. Classified into 5 classes via 4
  ascending breakpoints (generic default `[5,15,25,35]°`, always overridable
  and always recorded — never silently assumed). Boundary convention:
  a value exactly at a breakpoint falls into the higher class.
- **Aspect, elevation** — excluded from scoring. Any aspect- or
  elevation-susceptibility relationship in the literature is regionally
  specific (solar exposure, geology, land-cover zonation), not universal;
  baking one in without regional calibration data would be an uncalibrated,
  effectively fabricated regional claim.
- **Curvature** — excluded because no curvature derivative exists anywhere
  in the system yet (Phase 2 deferred it; TERRAIN-X doesn't document it
  either) — genuinely unavailable, not a design choice.
- **Rainfall** — accepted only as an optional `rainfall_context` label
  and/or a linked reference dataset, recorded in provenance, with **zero
  effect** on the computed class — proven by a dedicated test
  (`test_landslide_rainfall_context_recorded_but_does_not_change_class`).
  No legitimate rainfall-to-susceptibility coefficient exists to apply.

### Scenario/lineage model

New `HazardScenario` table (`hazard_type`, `input_datasets` JSON keyed by
role, `parameters` JSON — the exact values used including applied
defaults, `rainfall_dataset_id`, `status`, `error_message`). New
`Dataset.hazard_scenario_id` (nullable FK, `ondelete="SET NULL"`, same
lineage-survives-deletion policy as `source_dataset_id`/
`terrain_x_package_id`) and new `DatasetOrigin.HAZARD_MODEL` — distinguishing
a scenario-driven hazard output from a plain deterministic terrain
derivative, directly enabling later Exposure/Risk Engine queries like "all
hazard-model outputs for this project."

`HazardScenario.rainfall_dataset_id → Dataset.id` and
`Dataset.hazard_scenario_id → HazardScenario.id` form a genuine circular FK
between the two tables. `Dataset.hazard_scenario_id`'s constraint uses
`use_alter=True` (a standard SQLAlchemy pattern) so `Base.metadata.create_all()`
can order table creation correctly on any backend — not relying on SQLite's
unusually lenient DDL, since Postgres is the target production database.

Hazard outputs are ordinary `Dataset` rows (`dataset_type="flood_inundation"`
/ `"landslide_susceptibility"`), same pattern as Phase 2 derivatives and
Phase 3 package assets — zero changes needed to existing
download/delete/list-filter endpoints.

### Reused, not duplicated: the TERRAIN-X eligibility gate

The `vertical_reference == metric_calibrated` check (Phase 3) was inline in
`/derive` only. It's now `app/services/dataset_gates.py::ensure_metric_calibrated_if_terrain_x_import`,
reused identically by `/derive` and both hazard endpoints — one enforcement
point for "never treat relative/unknown depth data as real elevation,"
verified to still produce the exact same rejection behavior via the
existing Phase 3 test suite (no regression).

### API endpoints

`POST /projects/{id}/hazard-scenarios/flood` and `.../landslide` (two typed
endpoints, not one discriminated-union body — cleaner OpenAPI docs, and the
pattern future hazard types extend without growing a union indefinitely);
`GET /projects/{id}/hazard-scenarios` (filterable by `hazard_type`);
`GET /hazard-scenarios/{id}`; `GET /hazard-scenarios/{id}/datasets`. Both
POST handlers share one internal orchestration helper
(`_run_hazard_scenario` in `app/api/hazards.py`) covering scenario/dataset
creation, computation, and the success/failure finalization path — avoiding
near-duplicate ~80-line handlers.

### Validation and failure behavior

Pre-flight (404/400/422, nothing persisted): project exists; DEM
exists/right type/validated/gated; required derivative(s) exist for that
exact DEM (`flow_direction`+`flow_accumulation` for flood, `slope` for
landslide — caller must have already run `/derive`, no auto-chaining, see
Deferred); parameters validated (`depth_above_drainage_m > 0`,
`slope_breakpoints_deg` strictly ascending); optional rainfall reference
must exist in-project if supplied. Post-validation runtime failures (e.g. a
corrupted input file) are caught by `_run_hazard_scenario` and recorded as
`HazardScenario.status="failed"` + an `invalid` output dataset — never a
raw 500 — mirroring Phase 2/3's exception-handling precedent exactly.

### Dependencies and RQ/Redis

**No new dependencies** — NumPy + stdlib `heapq`, everything already
installed. A hydrology library (`richdem`/`pysheds`) was considered and
rejected again for the same reason as Phase 2: unverified Windows/Python
3.13 wheel availability, where self-implementing (as already done for
Horn's method) keeps the pattern consistent and fully testable.

**RQ/Redis: still not introduced.** Flow accumulation/HAND are the first
algorithms doing genuine cell-by-cell graph traversal in Python rather than
vectorized NumPy array math — the strongest "next async candidate" signal
so far, flagged explicitly rather than silently accepted. Stays synchronous
because it's still well within request budgets at hackathon/regional-DEM
scale; revisit if real DEM sizes prove slow.

## Consequences

- The full terrain → hazard pipeline (upload DEM → derive flow/slope →
  run scenario) is real, tested end-to-end, and scientifically honest about
  its own limitations at every output.
- 44 new tests (13 flow-algorithm, 13 hazard-math, 18 API-level), all
  passing; 119 total in the suite.
- Frontend unchanged (explicit instruction) — hazard scenarios are
  inspectable via `/docs` for now, same as Phase 2/3.
- Deferred: curvature-based landslide refinement (no source exists); any
  rainfall→depth or rainfall→susceptibility conversion formula (fabrication
  risk); full hydraulic/hydrodynamic flood simulation; flat-area
  flow-resolution refinements beyond basic priority-flood; async/RQ
  processing; vector polygon extraction of inundation extent (raster only);
  multi-scenario comparison/diffing (Scenario Lab's job); auto-chaining
  derivation (caller runs `/derive` explicitly first); soil/geology/
  land-cover-informed landslide factors; wildfire/cyclone/earthquake/
  tsunami hazard types (architecture accommodates them via the same
  scenario+endpoint-per-type pattern); any frontend UI.
