# ADR 0003: Phase 2 Geospatial Processing (Slope/Aspect)

Date: 2026-09-16
Status: Accepted

## Context

The original 17-module architecture had a standalone "Terrain Adapter" phase
before Geospatial Processing. Phase 1's generic upload validation (CRS
checks, "never guess a CRS," provenance, bbox extraction) already covers
that ground for DEM/DSM datasets, so Phase 2 is purely the **Geospatial
Processing** module: deriving real terrain products from an already-
validated DEM/DSM.

The full module spec lists slope, aspect, curvature, flow direction, flow
accumulation, and watershed-related products. Building all of that now would
be speculative: flow direction/accumulation/watershed delineation have no
consumer yet, and their correct design (depression filling method, D8 vs
D-infinity, etc.) should be driven by the actual Hazard Engine (Phase 5
Flood) when it exists, not guessed at in advance.

> **Addendum (2026-09-16, Phase 4):** the flow direction/accumulation
> deferral below is superseded by ADR 0005 — Phase 4's flood hazard engine
> is the concrete consumer, and they were added to this same module
> (`app/services/terrain.py`) rather than a separate one, since they remain
> general, deterministic terrain-characterization products. Curvature and
> watershed delineation remain deferred. The roadmap referenced below as
> "Phase 5 Flood"/"Phase 6 Landslide" is renumbered as of Phase 4's start:
> both are combined into "Phase 4 Hazard Engine."

## Decision

**Scope: slope and aspect only.** Curvature, flow direction/accumulation,
and watershed delineation are deferred until a concrete consumer (Phase 5
Flood or Phase 6 Landslide) specifies what it actually needs from them.

**CRS-aware computation.** Slope is meaningless if pixel size isn't in real
metric units. Before computing, a geographic-CRS (degree-based) source DEM
is automatically reprojected to an appropriate UTM zone, chosen from the
raster's centroid via `pyproj`'s UTM lookup (`query_utm_crs_info`). A
DEM already in a projected/metric CRS is used as-is. **Limitation:** a
single UTM zone is assumed adequate for the raster's extent — correct for
local/regional DEMs, not appropriate for continental-scale rasters spanning
multiple zones. Not handled; flagged here rather than silently wrong.

**Algorithm: Horn's method**, the same 3×3 finite-difference kernel GDAL's
`gdaldem` and QGIS use, implemented directly in NumPy
(`app/services/terrain.py::compute_slope_aspect`) as a pure function
(elevation array in, slope/aspect arrays out — no file I/O), so it's
testable against known analytic cases independent of rasterio. Verified in
`tests/test_terrain.py` against: a flat plane (slope=0 everywhere), a
constant-gradient ramp (slope = `arctan(gradient)` exactly), and tilted
ramps whose aspect is independently derivable by reasoning about which
direction is downhill (not copied from an aspect formula we didn't verify).

**Nodata/edge handling:** border cells (no full 3×3 neighborhood) and any
cell whose neighborhood touches nodata are `NaN` in the computed arrays,
written out as a dedicated nodata value (`-9999.0`) — never a fabricated
number. Flat cells (slope ≈ 0) get a distinct sentinel (`-1.0`) in the
aspect raster, since direction is undefined on flat ground.

**Derived products are ordinary catalog datasets.** No new table. A new
nullable, self-referential `Dataset.source_dataset_id` column
(`ondelete="SET NULL"`) links a derived dataset back to its DEM/DSM source.
`DatasetType` (the upload-facing enum) is deliberately **not** extended with
`slope`/`aspect` — that would let an uploaded file be mislabeled as a
computed product. Instead, the `/derive` endpoint takes a separate
`Literal["slope", "aspect"]` request schema, and `Dataset.dataset_type` /
`DatasetRead.dataset_type` are plain strings that can hold either family of
values. `?dataset_type=slope` filtering still works because the column
value is what's compared, not a shared enum.

**Provenance:** every derived dataset's `provenance` field records the
source dataset's id/name/filename, the method (`"horn"`), whether
reprojection happened, the source and output CRS, and pixel size — so
derivation is always traceable, per the project's "maintain provenance and
model parameters" rule.

**Correctness fix applied alongside this phase:** SQLite silently ignores
`ON DELETE`/`ON UPDATE` clauses unless `PRAGMA foreign_keys=ON` is set per
connection. Nothing enabled this previously, so Phase 1's `project_id`
cascade delete was dormant (never exercised — there's no delete-project
endpoint yet). `app/db/sqlite_pragma.py::enable_sqlite_foreign_keys` now
sets this pragma via a SQLAlchemy `connect` event listener, applied to both
the real app engine (`db/session.py`) and the test engine
(`tests/conftest.py`). A no-op on Postgres, which enforces these natively.

## Consequences

- Slope/aspect are immediately usable by Phase 6 (Landslide susceptibility).
- No new heavy dependencies — NumPy, `rasterio.warp`, and `pyproj` were
  already installed in Phase 1.
- `packages/geo-core` extraction stays deferred (as in Phase 1); the
  algorithm lives in `apps/api/app/services/terrain.py`.
- Synchronous, in-request computation (no Redis/RQ) remains appropriate at
  hackathon-fixture scale; this is the first candidate for Phase 4's async
  worker once real large DEMs are used, which would time out an HTTP
  request.
- Frontend is unchanged in this phase (explicit choice) — derived datasets
  are already visible via the existing Phase 1 dataset table/API, since
  they're just more `Dataset` rows; triggering a derivation happens through
  `/docs` (Swagger UI) for now.
