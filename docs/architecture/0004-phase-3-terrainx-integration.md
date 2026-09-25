# ADR 0004: Phase 3 TERRAIN-X Integration / Terrain Adapter

Date: 2026-09-16
Status: Accepted

## Context

TERRAIN-X is a separate companion project (different team, different
repository) producing terrain reconstruction, monocular depth estimation,
and screening layers from imagery. CRISIS-X must remain fully functional
without it — TERRAIN-X import is an *additional* ingestion path alongside
Phase 1's plain file upload, not a dependency. CRISIS-X must never run,
call, or import TERRAIN-X code, and must not assume anything about its
internal implementation or filesystem layout.

Roadmap numbering used from here on: **Phase 4 is the Hazard Engine**
(flood + landslide), **Phase 5 is Earth Observation AI / Change
Detection**. This ADR's cross-references use that numbering.

## Decision

### Exchange format, not a dependency

A TERRAIN-X package is a static ZIP + versioned JSON manifest (see
`docs/data-contracts/terrainx-package-v1.md`). CRISIS-X only ever reads this
file — no API calls, no shared database, no assumed folder structure.
Nothing in the import path can fail because TERRAIN-X isn't installed,
running, or reachable, because nothing ever tries to reach it.

### Never trust the manifest — always re-verify

The same discipline Phase 1's upload validation established ("never guess
or assume a CRS") is extended to third-party claims generally: every
manifest-declared CRS and checksum is independently re-derived from the
actual asset file (reusing the existing `validate_dataset_file` — no
duplicated validation logic), and any mismatch is recorded rather than
silently resolved either way. The actual file is always ground truth.

### The vertical_reference gate

TERRAIN-X can produce either calibrated metric elevation or uncalibrated
relative depth. Silently treating the latter as the former — and then
computing "slope in degrees" from it — would be exactly the kind of
fabricated scientific result the project's foundational rules forbid. The
contract makes `vertical_reference` mandatory for `dem`/`dsm` assets
(`relative` / `metric_calibrated` / `unknown`, no silent default), and
`app/api/datasets.py`'s `/derive` endpoint now refuses any TERRAIN-X-import
source whose `vertical_reference` isn't `metric_calibrated`. Relative/
unknown-reference elevation data is still cataloged and downloadable — it's
only derivation (and, later, hazard modeling) that's blocked.

### Explicit lineage: `DatasetOrigin`

Before this phase, whether a dataset was uploaded or computed was only
inferable from `source_dataset_id` being set — insufficient once a third
origin (TERRAIN-X import) exists. `Dataset.origin` (`uploaded` /
`crisisx_derived` / `terrain_x_import`) is now an explicit, required,
queryable column, retrofitted onto Phase 1's upload endpoint and Phase 2's
derive endpoint via a backfill migration (`source_dataset_id IS NOT NULL`
→ `crisisx_derived`, else → `uploaded`).

### Assets are ordinary Dataset rows

A TERRAIN-X package's assets become regular `Dataset` rows (one per asset),
not one opaque blob per package — the same choice Phase 2 made for derived
products. `role` maps onto the same `dataset_type` values used elsewhere
(`dem`, `dsm`, and Phase 2's own `slope`/`aspect` strings — a TERRAIN-X-
precomputed slope and a CRISIS-X-computed one share a type, distinguished
by `origin`, not by a separate namespace). New `DatasetType` values
`flood_screening` and `landslide_susceptibility_screening` were added since
TERRAIN-X explicitly produces these. An unrecognized `role` falls back to
`other` — forward-compatible with a future TERRAIN-X role addition rather
than rejecting the package. Package-level metadata (manifest, source
system/version, the original zip) lives in a new `TerrainXPackage` table,
linked from each asset via `Dataset.terrain_x_package_id`
(`ondelete="SET NULL"`, same lineage-survives-deletion policy as Phase 2's
`source_dataset_id`).

This design means zero changes were needed to the existing
download/derive/delete/list-filter endpoints — imported assets just flow
through them.

### Archive safety

Because the ZIP arrives as an untrusted upload, `app/services/archive_safety.py`
validates every entry before extracting anything: absolute paths, `..`
traversal, and Windows drive-letter paths are all rejected; every
extraction target is confirmed to resolve inside the staging directory;
and archive/entry/uncompressed-size limits are enforced against actual
decompressed bytes as they're read, not just the archive's own
(attacker-controlled) declared metadata. This is a standalone, reusable
utility — not specific to TERRAIN-X packages.

### Shapefile support deferred

A Shapefile is inherently multi-file (`.shp`/`.shx`/`.dbf`/...), and this
contract version has no multi-file asset representation. Rather than
accept a `.shp` reference and silently fail (or worse, partially succeed
with a broken/incomplete read), `format: "shapefile"` is explicitly
rejected per-asset with a clear message pointing to GeoJSON/GeoPackage.
Defining a proper multi-file asset shape is left for a future contract
version if it turns out to be needed.

### Atomicity

A structurally invalid package (bad ZIP, missing manifest, unsupported
`contract_version`) creates nothing at all — verified by
`tests/test_terrain_packages_api.py`'s rejection tests, which confirm the
project's package list stays empty. A structurally valid package always
creates the package record and one dataset row per asset; individual
assets may be marked invalid without blocking their valid siblings — the
same per-dataset philosophy as Phase 1 uploads.

## Consequences

- CRISIS-X's independence from TERRAIN-X is now demonstrated, not just
  claimed: nothing in the import path imports, calls, or requires
  TERRAIN-X; the entire test suite builds its own synthetic packages
  on-the-fly.
- Runs synchronously in-request (no Redis/RQ), consistent with Phase 1/2 —
  flagged again here as a Phase-4-adjacent async candidate, since a
  multi-asset ZIP is the largest single upload the system handles so far.
- `DatasetType.TERRAIN_X_PACKAGE` (the Phase 1 placeholder for "a package
  becomes one dataset") is superseded by this per-asset design. Left in
  place, unused, to avoid enum churn.
- Deferred: live TERRAIN-X connectivity of any kind; automatic folder-
  watching ingestion; TERRAIN-X's 3D visualization outputs (unrelated to
  this data-layer contract); any flood/landslide *modeling* using imported
  screening layers (that's Phase 4's job — Phase 3 only catalogs them);
  auto-triggering CRISIS-X derivation on import; package versioning/re-run
  linkage; package signature/authenticity verification (no auth exists
  anywhere in the API yet — not a Phase-3-specific gap); multi-file
  (Shapefile) assets.
- No frontend changes in this phase (explicit instruction) — imported
  packages/assets are inspectable via `/docs` (Swagger UI) for now.
