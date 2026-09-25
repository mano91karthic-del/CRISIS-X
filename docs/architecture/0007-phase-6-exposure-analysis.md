# ADR 0007: Phase 6 Exposure Analysis

Date: 2026-09-17
Status: Accepted

## Context

Exposure Analysis answers one question only: which assets/population
spatially intersect a modeled hazard/change layer, broken down by hazard
class. It is explicitly **not** vulnerability, damage estimation, or
monetary loss, and must never collapse into a single severity/danger
score — hazard ≠ exposure ≠ risk, and only the first two exist yet. v1
scope is exactly one hazard/change dataset + exactly one exposure dataset
per analysis.

## Decision

### Eligible hazard inputs

`SUPPORTED_HAZARD_TYPES = {"flood_inundation", "landslide_susceptibility", "eo_change_mask"}`
(`app/services/exposure.py`). These are the three existing Phase 4/5 raster
outputs; `eo_change_magnitude` is deliberately excluded (continuous
magnitude, not a hazard classification — verified by a dedicated test). The
hazard dataset must additionally have `origin` in
`{hazard_model, eo_analysis}` and `status == validated` — checked in
`app/api/exposure.py::_get_hazard_dataset` before any computation runs.

### One pipeline, reused for every exposure asset type

1. Classify the hazard raster into small integer class codes
   (`classify_hazard_array`): flood is single-class (`inundated`),
   landslide uses its stored `class_legend` (or a default 5-class legend),
   EO change mask is binary. Fails closed (`ValueError`) for any
   `dataset_type` outside `SUPPORTED_HAZARD_TYPES` — never guesses at
   unfamiliar raster value semantics.
2. Polygonize the classified raster into one dissolved (multi)polygon per
   class (`polygonize_hazard_raster`, via `rasterio.features.shapes`).
   Nodata/invalid cells produce no polygon at all, so "no hazard signal
   here" is never folded into any class.
3. Reproject the exposure asset into the hazard raster's CRS (not the
   reverse — the hazard raster is the fixed reference grid, same convention
   as Phase 5's `before` image).
4. Overlay against the hazard class polygons:
   - **Points** (vector population, or population raster cells converted to
     centroids): `geopandas.sjoin(predicate="within")` — count and,
     if a value field is given, sum.
   - **Lines** (roads): true geometric intersection via `geopandas.overlay`,
     not centroid/within-only — reports intersected length, not whole-line
     length for any line that merely crosses a class boundary.
   - **Polygons** (buildings, hospitals, shelters, critical_infrastructure,
     safe_zones, other, vector population-as-polygons): true geometric
     intersection; if a population field is given, apportions it by
     intersected-area fraction (uniform-density assumption within the
     source polygon — disclosed as a limitation, not silently assumed).

### Population handling: raster vs. vector, never guessed

- **Raster population**: only `dataset_type == "population"` GeoTIFFs are
  accepted as a raster exposure input (checked in
  `_get_exposure_dataset` — no other raster type has meaningful
  point/count semantics). Cells are converted to centroid points carrying
  the cell value as `population` (`raster_to_population_points`); nodata
  and non-positive cells are skipped, never counted. `population_field` is
  rejected as inapplicable for raster population (the value column is
  fixed).
- **Vector population**: `population_field` is *required* — the attribute
  column holding the population count is never inferred from column names
  or heuristics. Validated against the exposure file's actual columns
  before computation starts (`_resolve_population_field`, checked pre-flight
  so a bad column name 400s immediately rather than surfacing mid-overlay).
- **Every other vector exposure type** (buildings, roads, hospitals,
  shelters, critical_infrastructure, safe_zones, other): no `population_field`
  is accepted at all — rejected upstream if supplied, since these assets
  are counted/measured, not summed by an attribute.

### CRS and reprojection

If the hazard raster's CRS is geographic, it is auto-reprojected to a
single auto-selected UTM zone (`pick_utm_crs`, reused from Phase 2/5) so
area/length outputs are metric, not degrees². Reprojection uses
**nearest-neighbor**, deliberately not bilinear: hazard raster values are
categorical (landslide class, change mask) or have a sharp nodata boundary
(flood depth) — interpolating them would blend adjacent classes or blend
real depth with the nodata sentinel into meaningless intermediate numbers.
This mirrors Phase 5's reasoning for reusing bilinear on continuous
imagery, applied in the opposite direction for categorical hazard data.
The exposure vector dataset is always reprojected to match (`gdf.to_crs`),
never the other way — the hazard raster's CRS/grid is the fixed reference,
consistent with Phase 5's `before`-is-reference convention.

### Results shape and provenance

`ExposureAnalysis.results` (JSON) holds `method`, `hazard_dataset_type`,
`reprojected_hazard_crs`, `crs`, `hazard_class_labels`, a label-keyed
`by_class` breakdown (count/sum for points, length_m/feature_count for
lines, area_m2/feature_count/population_sum for polygons), optional flood
`depth_statistics`, and `limitations` — always including the two fixed
exposure-vs-risk disclosures (`EXPOSURE_LIMITATIONS`) plus any
situationally-applicable ones (UTM reprojection, raster-centroid
assignment, polygon uniform-density apportionment, flood depth-statistics
scope). The optional output feature layer (an `exposure_features`
GeoJSON `Dataset`, produced only when the overlay is non-empty) carries
the full `results` dict plus hazard/exposure dataset id+name in its own
`provenance` — resilient even if a source dataset is later deleted
(`source_dataset_id`/`hazard_dataset_id`/`exposure_dataset_id` are all
`SET NULL` on delete, lineage-survives policy consistent with every prior
phase).

### Lineage: `ExposureAnalysis` + `Dataset.exposure_analysis_id`

Same pattern as `HazardScenario`/`EOChangeAnalysis`: a run record
(`hazard_dataset_id`, `exposure_dataset_id`, denormalized
`hazard_dataset_type`/`exposure_dataset_type`, `population_field`,
`parameters`, `results`, `status`, `error_message`) plus a nullable
`Dataset.exposure_analysis_id` FK (`ondelete="SET NULL"`). New
`DatasetOrigin.EXPOSURE_ANALYSIS`. Unlike Phase 4/5, the output dataset is
**optional** — an analysis with zero overlay features (e.g. no exposure
features anywhere near the hazard extent) still completes successfully
with an empty `by_class` and produces no feature-layer `Dataset`, since
writing an empty GeoJSON would be a meaningless artifact. `results` is
stored directly as JSON on `ExposureAnalysis` rather than a normalized
per-class table — exposure summaries are fundamentally tabular and small,
not worth a separate schema for what this phase needs.

`ExposureAnalysis.hazard_dataset_id`/`exposure_dataset_id → Dataset.id`
and `Dataset.exposure_analysis_id → ExposureAnalysis.id` form the same
circular-FK situation as Phase 4/5, resolved identically: `use_alter=True`
+ an explicit constraint name on the `Dataset` side.

### API endpoints

`POST /projects/{id}/exposure-analyses`, `GET /projects/{id}/exposure-analyses`
(filterable by `exposure_dataset_type`), `GET /exposure-analyses/{id}`,
`GET /exposure-analyses/{id}/datasets` — same shape as Phase 4/5's
endpoint families.

### Validation and failure behavior

Pre-flight (404/400, nothing persisted): project exists; hazard dataset
exists/belongs to project/has an eligible `origin`+`dataset_type`/is
validated; exposure dataset exists/belongs to project/is validated/has
usable geometry (raster population or a supported vector format); both
files exist on disk (410); `population_field` requiredness resolved per
the rules above, including validating the column actually exists in the
exposure file. Once pre-flight passes, an `ExposureAnalysis` row is
created and flushed *before* computation runs, so a post-validation
runtime failure (e.g. mixed geometry types discovered only once the file
is actually read, or any other exception from `run_exposure_analysis`) is
caught and recorded as `ExposureAnalysis.status="failed"` +
`error_message` — never a raw 500, same precedent as Phase 2/3/4/5.

## Consequences

- No new dependencies — GeoPandas/Shapely/rasterio, all already installed
  since Phase 1/2.
- Fully vectorized overlay operations (GeoPandas `sjoin`/`overlay`, GDAL
  polygonization) — no per-feature Python loops, no new async justification;
  **RQ/Redis remains unjustified**, consistent with Phase 4/5's conclusion.
- 45 new tests (24 pure-geometry: classification, polygonization,
  point/line/polygon overlay against known synthetic cases including
  boundary-straddling and nodata; 21 API-level: raster/vector population,
  buildings/roads polygon+line overlay, CRS reprojection, flood depth
  statistics, nodata exclusion, multi-class landslide, `population_field`
  validation, EO change mask eligibility vs. magnitude rejection, mixed
  geometry rejection producing a failed record, cross-project ownership
  rejection, provenance/lineage completeness, listing/detail endpoints).
  204 total in the suite, all passing.
- Frontend unchanged (explicit instruction) — inspectable via `/docs`,
  same as Phase 2–5.
- Deferred: vulnerability scoring, damage estimation, monetary loss, any
  multi-hazard or multi-exposure-dataset analysis (v1 is strictly 1×1),
  population-raster sub-cell interpolation (centroid-only), true
  building-count-weighted (vs. area-weighted) population apportionment,
  any risk/likelihood combination — belongs to a future Risk module with
  its own explicit scope discussion.
