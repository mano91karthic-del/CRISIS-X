# TERRAIN-X Package Contract — v1.0

This is the exchange format between TERRAIN-X and CRISIS-X. It's a static
file contract, not an API or shared filesystem assumption: TERRAIN-X (or a
human) produces a file matching this spec, and CRISIS-X reads it. CRISIS-X
never runs, calls, or imports TERRAIN-X code, and never assumes anything
about TERRAIN-X's internal implementation beyond what's declared here.

## Package format

A TERRAIN-X package is a **ZIP archive** containing:

- `manifest.json` at the archive root (required)
- Zero or more asset files, referenced by the manifest via paths relative
  to the archive root

## Governing principle

**Every manifest claim is independently re-verified, never trusted as
fact.** CRISIS-X opens each referenced file itself and extracts CRS,
format, and other metadata directly from the file — the same validation
Data Hub uploads go through. If the manifest's claims (CRS, checksum)
disagree with what CRISIS-X finds in the actual file, both values are
recorded and the mismatch is flagged; the actual file always wins for what
CRISIS-X treats as true.

## manifest.json shape

```json
{
  "contract_version": "1.0",
  "package_id": "TERRAIN-X's own opaque run/job identifier",
  "generated_at": "2026-09-10T12:00:00Z",
  "source": {
    "system": "TERRAIN-X",
    "version": "0.3.1",
    "model": "optional model/algorithm name, e.g. 'depth-anything-v2'",
    "notes": "optional free text"
  },
  "area_of_interest": { "name": "optional human-readable label" },
  "assets": [
    {
      "asset_id": "unique within this package",
      "role": "dem | dsm | slope | aspect | flood_screening | landslide_screening | other",
      "file": "relative path inside the zip, e.g. 'dsm.tif'",
      "format": "geotiff | geojson | gpkg | csv",
      "vertical_reference": "relative | metric_calibrated | unknown",
      "crs": "TERRAIN-X's claimed CRS — independently re-verified, never trusted",
      "checksum_sha256": "optional, compared against CRISIS-X's own, never trusted blindly",
      "description": "optional free text",
      "uncertainty": {}
    }
  ]
}
```

### Top-level fields

| Field | Required | Notes |
|---|---|---|
| `contract_version` | yes | Must be a version this CRISIS-X build recognizes (currently `"1.0"`). An unrecognized version rejects the **whole package** — no best-effort parsing of an unfamiliar shape. |
| `package_id` | no | TERRAIN-X's own identifier for this export. Distinct from the CRISIS-X-assigned database id of the resulting package record. |
| `generated_at` | no | TERRAIN-X's claim of when it produced this package. Not independently verified (there's nothing to verify it against). |
| `source` | yes | `system` is required; `version`/`model`/`notes` are optional. |
| `area_of_interest` | no | Currently just an optional human label. |
| `assets` | yes | Must contain at least one entry. |

### Asset fields

| Field | Required | Notes |
|---|---|---|
| `asset_id` | yes | Unique within the package. |
| `role` | yes | One of the listed values. **An unrecognized role does not reject the asset** — it's cataloged as CRISIS-X's generic `other` type, with the raw role string preserved, so a future TERRAIN-X role addition doesn't break existing imports. |
| `file` | yes | Path relative to the archive root. Must not contain `..` segments or be absolute — rejected at the archive-safety layer regardless of what the manifest declares (see Security, below). |
| `format` | yes | One of `geotiff`, `geojson`, `gpkg`, `csv`. **`shapefile` is explicitly not supported in v1** — see below. |
| `vertical_reference` | **required for `role: dem` or `role: dsm`**, ignored otherwise | `relative` (uncalibrated monocular depth), `metric_calibrated` (real elevation), or `unknown`. Missing entirely on a dem/dsm asset marks that asset invalid — the contract requires an explicit declaration, not silence. |
| `crs` | no | TERRAIN-X's claimed CRS. Compared against the actual file's CRS; never substituted for it. |
| `checksum_sha256` | no | Compared against CRISIS-X's own computed checksum of the actual file. |
| `description`, `uncertainty` | no | Free-form. No fabricated numbers are invented if TERRAIN-X doesn't provide any — `uncertainty` is simply empty/absent rather than guessed. |

## The vertical_reference gate

This is the most important rule in the contract, and it exists because a
DEM/DSM's elevation *values* — not just its horizontal pixel size — must be
in real metric units for CRISIS-X's terrain derivations (slope, aspect, and
any future hazard modeling) to mean anything. TERRAIN-X can legitimately
produce **relative depth** (monocular depth estimation, no metric
calibration) as well as **metric_calibrated** elevation when reference data
exists.

- `metric_calibrated` — usable as a source for CRISIS-X's `/derive`
  endpoint (slope/aspect) and any future hazard modeling.
- `relative` or `unknown` — imported and cataloged (inspectable,
  downloadable) but **rejected** if used as a `/derive` source. CRISIS-X
  will not compute "slope in degrees" from data that was never claimed to
  be in real elevation units and silently present that as a real number.
- missing entirely (on a dem/dsm asset) — the asset itself is marked
  invalid at import time. The contract requires an explicit declaration.

## Shapefile support: explicitly deferred in v1

A Shapefile is not one file — it's a set of sidecar files (`.shp`, `.shx`,
`.dbf`, optionally `.prj` and others) that must travel together to be
readable. This contract version does not define a multi-file asset
representation, so declaring `format: "shapefile"` (or a `file` ending in
`.shp`) is rejected for that asset with a clear message, rather than
silently attempting to import an unreadable lone `.shp`. Use **GeoJSON** or
**GeoPackage** instead — both are single-file and already supported.

## Security: archive safety

Because a package arrives as an untrusted ZIP upload, CRISIS-X validates
every archive entry *before* extracting anything:

- No absolute paths, no `..` traversal segments, no Windows drive-letter
  paths — any unsafe path rejects the **whole package**.
- Every extraction target is confirmed to resolve inside the staging
  directory (defense in depth beyond the string check).
- Archive size, entry count, and per-file/total uncompressed size are all
  capped, checked against actual decompressed bytes as they're read (not
  just the archive's own declared, attacker-controlled metadata) — zip-bomb
  protection.

See `app/services/archive_safety.py` for the implementation and limits.

## Atomicity

- **Malformed ZIP, missing `manifest.json`, or an unsupported
  `contract_version`** — the whole package is rejected. Nothing is
  persisted: no package record, no dataset rows.
- **A structurally valid manifest** always creates a package record and one
  dataset row per declared asset. Individual assets may still be marked
  `invalid` (bad declaration, missing file, unreadable content) without
  blocking their valid siblings — the same per-dataset philosophy Data Hub
  uploads already follow.

## Versioning

Only `contract_version: "1.0"` is currently supported. A future v1.1/v2.0
would be additive where possible; CRISIS-X explicitly rejects versions it
doesn't recognize rather than guessing at an unfamiliar shape.
