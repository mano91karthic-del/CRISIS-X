# ADR 0008: Phase 7 Risk Engine

Date: 2026-09-17
Status: Accepted

## Context

Phase 4 produces hazard (flood inundation / landslide susceptibility /
EO change classification). Phase 6 produces exposure (which assets/
population spatially intersect a hazard class, and how much). Neither
phase produces risk, and Phase 6's own service module (`app/services/
exposure.py`) is explicit that it is "not a risk score" — that boundary is
enforced structurally, not just documented.

Phase 7 introduces risk: a transparent, configurable combination of an
already-classified hazard with two explicit, caller-declared assumptions —
vulnerability and consequence — into a risk score per hazard class. It must
not blur the Hazard ≠ Exposure ≠ Risk boundary the project has held since
Phase 4, must not claim deterministic disaster prediction, must not
introduce a black-box/ML risk score, and must not invent monetary-loss
figures the system has no data to justify.

## Decision

### Formula: Risk = Hazard intensity × Vulnerability × Consequence

```
hazard_intensity_weight(hazard_dataset_type, hazard_class_code) =
    hazard_class_code / HAZARD_CLASS_MAX_CODE[hazard_dataset_type]

risk_score = hazard_intensity_weight × vulnerability_weight × consequence_weight
```

(`app/services/risk.py::compute_hazard_intensity_weight` /
`compute_risk_score`.) This is the simplest defensible classical
disaster-risk identity, not a weighted/learned/black-box score — every
factor is either deterministically derived from an already-fixed
classification or an explicit number the caller stated.

`HAZARD_CLASS_MAX_CODE = {"flood_inundation": 1, "eo_change_mask": 1,
"landslide_susceptibility": 5}` is a **fixed constant per hazard type**,
matching Phase 4/5's own fixed classification schemes (landslide is always
5 ordinal classes; flood and the EO change mask are always single-class/
binary) — **not** `max(codes present in a given analysis)`. Anchoring to
the scheme rather than to what happens to appear in one AOI prevents the
weight from drifting: a small AOI that only exhibits landslide classes 1-2
must not have class 2 suddenly read as "maximum severity" merely because
it's the highest code present there.

`vulnerability_weight` and `consequence_weight` are both **required**
request fields in `[0, 1]`, with **no default value** — unlike e.g.
`slope_breakpoints_deg` (Phase 4), which quietly falls back to a documented
default. Vulnerability/consequence are exactly the assumptions this phase
exists to make explicit; silently defaulting either to e.g. `1.0` would
assert "maximum vulnerability/consequence" without the caller ever having
stated that. Both are recorded verbatim as first-class `RiskAnalysis`
columns (not buried only in JSON), echoed into `parameters`, and echoed
into every output dataset's `provenance`.

### risk_score is a per-class hazard-intensity screening figure, not a magnitude

This is the load-bearing distinction of the whole phase, and it shows up in
three places, deliberately:

1. **The formula itself** has no fourth factor for exposure quantity.
   `build_risk_by_class` (`app/services/risk.py`) adds
   `hazard_intensity_weight`/`risk_score`/`risk_class` to each `by_class`
   entry while carrying every pre-existing exposure-quantity key
   (`count`/`sum`/`length_m`/`feature_count`/`area_m2`/`population_sum`)
   through **completely unchanged** — risk_score is never used to scale,
   weight, or replace those figures. `test_risk.py::
   test_build_risk_by_class_does_not_use_exposure_quantity_in_formula`
   proves this directly: two classes with identical hazard class/weights
   but wildly different `count` (1 vs. 100,000) get an identical
   `risk_score`.
2. **`RiskAnalysis.results["by_class"]`** always reports exposure quantity
   and risk_score side by side in the same entry — a consumer never has to
   choose between "how much is exposed" and "how severe is it," both are
   always present together.
3. **`RISK_LIMITATIONS`** states this explicitly and unconditionally (not
   conditionally appended like Phase 6's UTM/population caveats): "risk_score
   is NOT weighted, scaled, or normalized by exposure quantity ... It must
   never be read as a total, aggregate, or project-level risk magnitude."
   No code path in this module sums or otherwise aggregates `risk_score`
   across classes or across features into a single number — that would be
   exactly the kind of arbitrary, unjustified exposure-normalization this
   phase was scoped to avoid.

We deliberately did **not** invent an exposure-quantity normalization
(e.g. dividing by a project-wide max count, or log-scaling population) to
fold exposure into the score — there is no principled basis in the current
data for choosing one normalization over another, and doing so would
manufacture a false sense of aggregate precision the underlying hazard/
exposure layers don't support.

### Risk classification and thresholds

`risk_score` (always in `[0, 1]`, since it's a product of three `[0,1]`-
bounded factors) is classified into 5 ordinal classes via
`classify_risk_score` — `np.digitize` against 4 ascending breakpoints,
left-inclusive (a value exactly at a breakpoint falls into the higher
class), identical convention to `hazard_landslide.classify_slope`.
`risk_breakpoints` defaults to `[0.2, 0.4, 0.6, 0.8]` but is caller-
configurable (validated: exactly 4 strictly-ascending values, each
strictly between 0 and 1) and always recorded verbatim.
`RISK_CLASS_LEGEND` reuses the exact `very_low/low/moderate/high/very_high`
naming Phase 4's landslide legend already established, for vocabulary
consistency.

### Scope: reuse Phase 6's geometry, never re-derive it

Phase 7 consumes exactly **one completed `ExposureAnalysis`** (v1 stays
1×1×1 — one hazard × one exposure × one risk run — same discipline Phase 6
applied). It does not re-open the hazard/exposure source datasets, re-
polygonize the hazard raster, or re-run the spatial overlay: it reads
`ExposureAnalysis.results["by_class"]` (already computed) for the aggregate
risk breakdown, and — only if Phase 6 produced a non-empty
`exposure_features` GeoJSON — reads that file and joins `risk_score`/
`risk_class` onto it by matching the `hazard_class_code` column every
overlay branch already writes (`app/api/risk.py`, mirroring `app/api/
exposure.py`'s `_get_hazard_dataset`/`_get_exposure_dataset` pattern but at
one remove — validating the *exposure analysis*, not raw datasets).

An `ExposureAnalysis` with an empty `by_class` (zero overlap) is not
treated as an error — the risk analysis completes with an empty
`results["by_class"]` and no output dataset, mirroring Phase 6's own
"empty is a valid completion, not a failure" precedent. Likewise, an
exposure analysis that never produced a feature layer (raster-population
class-polygon case aside — that *does* carry `hazard_class_code` and is
joinable) still yields a complete aggregate risk breakdown; only the
spatial output is skipped.

### Database models and lineage

`RiskAnalysis` (`app/models/risk_analysis.py`) follows the exact
`HazardScenario`/`ExposureAnalysis` shape: `id`/`project_id`/`name`/
`description`/input refs/`parameters`/`results`(JSON)/`status`/
`error_message`/`created_at`, no `relationship()` to `Project` (FK column
only, consistent with all three prior run-record models). It denormalizes
`hazard_dataset_id`/`exposure_dataset_id`/`hazard_dataset_type`/
`exposure_dataset_type` from its `exposure_analysis_id` input at creation
time — same resilience rationale `ExposureAnalysis` itself already applies
to its own hazard/exposure inputs: the risk record survives even if the
exposure analysis, or the datasets it names, are later deleted.

`Dataset` gains `origin = "risk_analysis"` and a `risk_analysis_id` FK
(`ondelete="SET NULL"`, `use_alter=True`, `name="fk_datasets_risk_analysis_id"`)
— a genuine circular FK with `RiskAnalysis.hazard_dataset_id`/
`exposure_dataset_id` pointing at `datasets.id`, resolved identically to
`hazard_scenario_id`/`eo_change_analysis_id`/`exposure_analysis_id`.
Migration `0aa54f44eedb` follows the exact two-step recipe (`op.create_table
('risk_analyses', ...)` with forward FKs, then a `batch_alter_table
('datasets', ...)` adding the deferred circular FK) established by
`84bfd6d203a3`/`2dcc1df5f1a5`/`3fbdc30da2cb`.

### Output dataset

One optional `dataset_type="risk_classification"` GeoJSON `Dataset`
(free-string derived-product name, not a `DatasetType` enum member — same
convention as `flood_inundation`/`exposure_features`), produced only when a
spatial join happened and yielded ≥1 feature. Its `provenance` merges the
base lineage dict (`risk_analysis_id`, `exposure_analysis_id`,
`hazard_dataset_id`/`name`, `exposure_dataset_id`/`name`) with the full
`RiskAnalysis.results` dict — same two-part pattern every prior phase uses.

### Validation and fail-closed behavior

Pre-flight: project exists (404); `exposure_analysis_id` exists and belongs
to the project (404); its `status == "completed"` (400 — nothing usable to
build risk from otherwise); its `hazard_dataset_type` is one of the three
`HAZARD_CLASS_MAX_CODE` keys (400, defensive — should already be guaranteed
transitively by Phase 6's own `SUPPORTED_HAZARD_TYPES` gate, but Phase 7
fails closed rather than trusting that transitively); `vulnerability_weight`/
`consequence_weight` in `[0,1]` (422, Pydantic); `risk_breakpoints` exactly
4 strictly-ascending values in `(0,1)` (422); if a spatial join will be
attempted, the `exposure_features` file must exist on disk (410).
Any other runtime failure (e.g. a corrupt `exposure_features` GeoJSON) is
caught by a broad `except Exception`, recorded as `status="failed"` +
`error_message`, HTTP 201 still returned — never a raw 500, same precedent
as every prior phase.

## Consequences

- No new dependency — `numpy` (`np.digitize`, same as `classify_slope`) and
  `pandas`/`geopandas` (a column map on an already-materialized
  GeoDataFrame) are already installed since Phase 1/2/6.
- No new async/RQ justification: this is dict arithmetic plus one
  GeoDataFrame column-map operation on data Phase 6 already fully
  materialized — cheaper than the exposure overlay itself, which was
  already judged not to need RQ (ADR 0007).
- 20 new pure-function tests (`test_risk.py`) + 14 new API integration
  tests (`test_risk_api.py`, including one full DEM→hazard→exposure→risk
  pipeline smoke test walking lineage purely from response ids). 238 total
  in the suite, all passing.
- Frontend unchanged — API-only, inspectable via `/docs`, same as Phases
  2-6.
- Deferred: monetary/currency loss estimation; casualty/injury estimation;
  structural damage/fragility-curve modeling (no building material/
  construction-type attributes exist in the Data Hub); per-hazard-class or
  per-asset-type vulnerability *curves* (v1 is one scalar
  `vulnerability_weight` per analysis, not a lookup table); multi-hazard
  risk combination (v1 stays 1×1×1, same discipline as Phase 6); any
  probabilistic/likelihood dimension (return periods, annual exceedance
  probability) — current hazard layers are scenario/screening outputs, not
  frequency-calibrated, so a likelihood axis would be fabricated; any
  aggregation of `risk_score` across classes/features/analyses into a
  single project-level "danger score" — belongs to a future Reporting/
  Dashboard phase, and only if it can be done without collapsing the
  disclosed per-class, exposure-quantity-preserving breakdown into an
  opaque number.
