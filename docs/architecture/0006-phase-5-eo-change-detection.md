# ADR 0006: Phase 5 Earth Observation / Change Detection

Date: 2026-09-16
Status: Accepted

## Context

The first EO/change-detection layer, operating on user-uploaded `imagery`
datasets (no live satellite APIs yet). Every result must preserve inputs,
acquisition dates, bands, preprocessing, method, thresholds, CRS,
resolution, nodata handling, parameters, limitations, and computation
timestamp — and must never present observed image change as confirmed
real-world disaster damage.

## Decision

### Extending `validation.py`'s raster metadata (additive, not a rewrite)

`_validate_raster` (Phase 1) now also extracts, for every raster upload
(uniform, not imagery-specific — no dataset-type branching in shared code):
per-band dtype list, band color interpretation (`src.colorinterp` — real
GDAL tags like `red`/`green`/`blue`/`undefined`), pixel size, and a
best-effort acquisition-date extraction from embedded file tags
(`TIFFTAG_DATETIME` and similar). Color interpretation is surfaced as a
**catalog hint only** — it is never used to infer band roles for a
computation. NIR/SWIR have no standard tag, so any method that needs
specific bands (`normalized_difference`) requires the caller to declare
them explicitly; CVA's default requires no band semantics at all.

### Acquisition date: a new generic, nullable `Dataset` column

Not imagery-specific. Resolution policy — deliberately *not* copying Phase
3's "file always wins" CRS policy, because a date tag isn't definitionally
authoritative the way an embedded CRS is (it might reflect file-creation
time, not acquisition time): the **user-supplied value takes precedence**
when given; the extracted tag is the fallback; if both are given and
disagree, both are recorded with an explicit `acquisition_date_mismatch`
flag rather than silently resolved either way. If neither exists,
`acquisition_date` stays `NULL` — this never blocks cataloging or running
an analysis; the output provenance discloses `null` honestly rather than
guessing.

### Alignment: `before` is always the fixed reference grid

If `after`'s `(crs, transform, width, height)` doesn't already match
`before`'s, `after` is resampled onto `before`'s exact grid (bilinear,
same choice as Phase 2's DEM reprojection) via `rasterio.warp.reproject`.
An explicit pre-flight bounds-overlap check (`check_bounds_overlap`)
rejects non-overlapping pairs before attempting alignment — otherwise a
non-overlapping pair would silently resample into an all-nodata result
instead of a clear rejection.

### Methodology: two established methods, no invented coefficients

- **Change Vector Analysis** (CVA, Malila 1980) — Euclidean magnitude of
  the per-band difference vector. Default band set: all bands common to
  both images (`1..min(before.count, after.count)`), always explicitly
  recorded even when defaulted. No band semantics required.
- **Normalized difference** — `(A-B)/(A+B)` computed per image, then
  differenced. Requires explicit `band_a_index`/`band_b_index` from the
  caller; an optional `index_name` (e.g. `"NDVI"`) is a pure label that
  never changes the math — CRISIS-X never pre-commits to "band 4 is NIR,"
  which varies by sensor.
- Both are scale-relative/robust-to-uncalibrated-input by construction —
  the reason they're preferred here over presenting a raw pixel/DN
  difference as physically meaningful, since no radiometric or atmospheric
  calibration is performed (no legitimate calibration coefficients exist
  for arbitrary uploaded sensors; inventing one would itself be a
  fabricated scientific result).
- Thresholding (`mask = |value| >= threshold`) is uniform across both
  methods.

### AI/ML: Otsu automatic thresholding, explicitly not deep learning

Supervised ML was rejected: no training labels exist anywhere in this
system, and bolting on an unvetted pretrained model (unknown training data,
unknown applicability) would be exactly the "meaningless AI label"
anti-pattern this project forbids. The one genuinely unsupervised technique
offered is **Otsu's method (1979)** — self-implemented in
`compute_otsu_threshold` (~40 lines of NumPy: histogram + between-class
variance maximization), used only to automatically pick the change/no-change
boundary from the change-magnitude histogram's own shape. No training data,
no labels, no PyTorch. Every Otsu-thresholded output's `limitations`
explicitly states it is classical unsupervised statistics, **not** a
trained/learned model and **not** deep learning/AI — verified by a
dedicated test (`test_otsu_threshold_is_recorded_and_used`) asserting that
exact disclosure is present. PyTorch was considered and rejected: there is
no genuine inference workload to justify it (no trained model exists to
run), and introducing it for Otsu alone would be dependency weight with no
real computation behind it. The `method` dispatch pattern (a string + one
function per method) is designed so a real trained model — with disclosed
architecture, weights provenance, and training data — could be added later
as `method="pretrained_model"` without restructuring anything built now.

### Lineage: `EOChangeAnalysis` + `Dataset.eo_change_analysis_id`

Same pattern as `TerrainXPackage`/`HazardScenario`: a run record
(`before_dataset_id`, `after_dataset_id`, `method`, `threshold_method`,
`parameters` JSON — every value actually used, defaults included) plus a
nullable `Dataset.eo_change_analysis_id` FK (`ondelete="SET NULL"`, same
lineage-survives-deletion policy as the rest). New
`DatasetOrigin.EO_ANALYSIS`. Two output datasets per analysis (magnitude +
mask, both always produced since a threshold — manual or Otsu-computed —
always exists once computation completes), plain-string `dataset_type`
values `"eo_change_magnitude"`/`"eo_change_mask"` (not added to the
upload-facing `DatasetType` enum, consistent with `"slope"`/`"aspect"`/
`"flood_inundation"`).

`EOChangeAnalysis.source_format` records `"geotiff"` for every run — a
deliberate field, not a hardcoded assumption baked into the schema. Adding
another EO source format or a live imagery feed later needs only a wider
set of accepted `source_format` values and an additional loader in
`app/services/eo_change.py`, not a redesign of `EOChangeAnalysis` or its
lineage.

`EOChangeAnalysis.before_dataset_id`/`after_dataset_id → Dataset.id` and
`Dataset.eo_change_analysis_id → EOChangeAnalysis.id` form a circular FK,
same situation as Phase 4's `HazardScenario`. Resolved identically:
`use_alter=True` + an explicit constraint name on the `Dataset` side, so
`Base.metadata.create_all()` orders table creation correctly on any
backend.

### API endpoints

`POST /projects/{id}/eo-change-analyses` (one endpoint, `method` as a
request field rather than two typed endpoints like Phase 4's flood/
landslide split — both EO methods share the same before/after/threshold
shape, differing only in which optional band fields are required, which a
single `model_validator` enforces cleanly); `GET /projects/{id}/eo-change-analyses`
(filterable by `method`); `GET /eo-change-analyses/{id}`;
`GET /eo-change-analyses/{id}/datasets`. Orchestration in
`app/api/eo_change.py` follows the same *pattern* as Phase 4's
`_run_hazard_scenario` (not literally shared code, since EO produces two
output datasets per run against Phase 4's one).

### Validation and failure behavior

Pre-flight (404/400/422, nothing persisted): project exists; both datasets
exist/belong to project/`dataset_type=="imagery"`/validated; files exist on
disk (410); spatial overlap exists; band indices in range for both images;
`normalized_difference` requires both band indices, `change_vector_analysis`
rejects them being supplied; `manual` requires `change_threshold`, `otsu`
rejects it being supplied. Post-validation runtime failure (e.g. an
out-of-range band index that only becomes apparent during array indexing,
or a corrupted file) is caught and recorded as
`EOChangeAnalysis.status="failed"` + `invalid` output datasets — never a
raw 500, identical precedent to Phase 2/3/4.

## Consequences

- Fully vectorized NumPy array math throughout (reprojection is
  GDAL-accelerated via `rasterio.warp`; CVA/normalized-difference have no
  per-cell Python loops) — computationally lighter than Phase 4's graph
  algorithms, so **RQ/Redis remains unjustified, even more clearly than
  Phase 4.**
- **No new dependencies** — NumPy + `rasterio`, both already installed.
  Otsu is self-implemented (no `scikit-image`). **PyTorch is not
  introduced** (see above).
- 38 new tests (16 pure-math: CVA/normalized-difference/Otsu against known
  analytic cases and general invariants; 22 API-level: happy paths,
  alignment, nodata, threshold monotonicity, Otsu disclosure, overlap
  rejection, validation, provenance/lineage, acquisition-date resolution).
  157 total in the suite, all passing.
- Frontend unchanged (explicit instruction) — inspectable via `/docs` for
  now, same as Phase 2/3/4.
- Deferred: live satellite/imagery APIs; cloud/shadow detection (no
  genuine capability without a cloud-mask band or a vetted classifier);
  radiometric/atmospheric calibration between acquisitions; supervised or
  pretrained-model-based change classification (no labels, no vetted
  model); persisting intermediate per-image index rasters as separate
  catalog datasets; vector polygon extraction of change regions (raster
  only); multi-temporal (>2 image) time-series analysis (pairwise only);
  any change-type/cause attribution (flood damage, fire, deforestation,
  etc. — explicitly out of scope, belongs to future modules with
  appropriate evidence); async/RQ processing; any frontend UI; auto-selection
  of before/after images (always explicit).
