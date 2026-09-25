# ADR 0012: Phase 11 Command Dashboard (2D + 3D)

Date: 2026-09-19
Status: Accepted

## Context

Phases 1-10 produced a complete analysis pipeline (Data Hub through
Scenario Lab) with zero visualization -- the only frontend that existed
was `apps/web`'s Phase 1 Data Hub UI (upload/list/delete datasets, one
page, no router, no state library, no tests). Phase 11 turns CRISIS-X
into the actual visual command interface: a 2D map (MapLibre GL JS) and
a 3D Digital Twin view (Three.js) that expose the existing Digital
Twin/Scenario Lab state and Phase 2-8 outputs. This phase is
visualization/interaction only -- it computes nothing scientific and
must not break the existing Data Hub UI.

## Decision

### Visualization only, orchestration not computation

`app/api/datasets.py` gained exactly five new endpoints (`/geojson`,
`/preview.png`, `/preview-bounds`, `/heightmap.png`,
`/heightmap-bounds`), backed by a new `app/services/preview.py`. Every
one of them reprojects/downsamples/colorizes an *already-computed*
Dataset file -- none of them run a hazard model, exposure overlay, risk
score, or route computation. Colorization always keys off a dataset's
own already-stored `class_legend`/`hazard_class_labels` (never a newly
invented classification), through one shared label -> color table
(`LEGEND_COLORS`) mirrored byte-for-byte on the frontend
(`geo/legendColors.ts`) so a 2D layer, a 3D drape texture, and the
backend's own PNG can never silently disagree about what a color means.
Everything else in this phase is frontend-only, consuming Phases 1-10's
existing API surface completely unchanged.

### Coexistence with Data Hub via routing, not replacement

`react-router-dom` was added; `App.tsx` became a thin route shell
(`/` -> `/dashboard`, `/dashboard/*` -> the new Command Dashboard,
`/data-hub` -> the existing `DataHubPage`, imported unchanged). Nothing
in `DataHubPage.tsx`, `DatasetTable.tsx`, `ProjectPanel.tsx`,
`UploadForm.tsx`, or `lib/api.ts`'s existing exports was modified -- new
API calls live in new `lib/api/*.ts` files. This is how "must not break
the existing Data Hub UI" is mechanically enforced, not just promised;
the full Vitest/typecheck/build runs in this report never touched those
files.

### MapLibre and Three.js as two independent, state-synced views -- not a merged canvas

`MapView` and `SceneView` each own an independent WebGL context/canvas,
both mounted simultaneously (one hidden via CSS on view-mode toggle) so
camera state and WebGL context survive switching. They synchronize only
through a new Zustand store (`state/dashboardStore.ts`): selection,
layer visibility/opacity, active feature, terrain exaggeration. A merged
canvas (MapLibre's `CustomLayer` hosting Three.js content) was
considered and rejected: it would couple MapLibre's render loop and
camera-matrix conventions to Three.js's scene graph (fragile across
MapLibre version bumps), permanently trap the 3D view inside MapLibre's
camera model (losing free orbit/pitch), and contradict "no unnecessary
additional mapping frameworks" in spirit by making MapLibre host the 3D
engine too. This was the single largest either-way judgment call in this
phase, approved before implementation.

### Raster/vector delivery: three small conversion endpoints, not a tile server

No tile-serving library existed (no titiler/rio-tiler), and no backend
output was guaranteed WGS84 before this phase -- `Dataset.crs` holds
whatever CRS the file actually has, often a projected UTM chosen by
`pick_utm_crs`. Rather than adopting a COG/XYZ tile pyramid (unjustified
complexity at this project's current, untested-at-scale data volume),
Phase 11 added:
- `GET /datasets/{id}/geojson` -- `geopandas.read_file` + `.to_crs("EPSG:4326")`
  + coordinate rounding + a 20,000-feature cap, wrapped in an envelope
  (`{type, features, truncated, total_feature_count, source_crs}`, never
  a bare `FeatureCollection`) so truncation is always visible to the
  frontend.
- `GET /datasets/{id}/preview.png` + `/preview-bounds` -- `rasterio.warp`
  reprojects to EPSG:4326 and downsamples to `max_dim` (default 1024px)
  in one `reproject()` call; nearest-neighbor resampling for classified
  rasters (never averages a class code into an invented intermediate
  value), bilinear for continuous ones; colorized via `LEGEND_COLORS`;
  bounds returned as JSON for MapLibre's `image` source `coordinates`.
- `GET /datasets/{id}/heightmap.png` + `/heightmap-bounds` -- DEM/DSM
  only, for the Three.js terrain mesh (see below).

**Pillow vs. GDAL's own PNG driver:** Pillow was added as the one new
backend dependency, for in-memory `Image.fromarray(...).save(buffer,
format="PNG")` PNG encoding. GDAL's PNG driver (already reachable
transitively via `rasterio`, technically zero-new-dependency) was
considered and rejected: in-memory writes need `rasterio.io.MemoryFile`
plumbing with rougher edges for 16-bit-or-RGBA output, for a conversion
this simple.

### Native-CRS heightmap for the 3D terrain, deliberately unreprojected

`build_heightmap` (in `app/services/preview.py`) keeps the DEM/DSM's own
metric CRS, never reprojecting to WGS84 -- the 3D scene only needs a
locally-planar XY grid (Three.js has no geographic camera), and
reprojecting a continuous elevation surface a second time would only add
resampling error for no benefit. `preview-bounds`/`heightmap-bounds`
return native bounds + pixel size in meters so
`components/view3d/buildTerrainMesh.ts` builds a metrically-correct mesh
with zero lon/lat math anywhere in the 3D code path. This is the direct
counterpart to `/preview.png`'s WGS84-only guarantee -- the same ADR
draws both boundaries explicitly so neither is accidentally assumed
elsewhere.

### Heightmap precision: 8-bit, confirmed by a real browser spike, not assumed

The approved plan required an implementation-time spike before
permanently locking the 8-bit-vs-16-bit heightmap precision decision.
`e2e/heightmap-precision-spike.spec.ts` loads a synthetic 512-value
16-bit grayscale PNG through the exact `drawImage` + `getImageData`
pipeline `SceneView.tsx` uses, and reports the distinct pixel values a
real Chromium canvas actually preserves. **Spike result: reported in the
Phase 11 implementation report** (see below) -- `build_heightmap` ships
8-bit grayscale PNGs (256 elevation levels across a DEM's actual
min/max), which is adequate for a screening-level command dashboard, not
a survey instrument, and avoids adding a client-side 16-bit PNG parser
to the frontend.

### Layer overrides freeze scoping and comparison rendering reused verbatim from Phase 10

The dashboard's `ScenarioComparisonPanel` renders exactly the `diff`
object `POST /scenario-comparisons` returns -- it never recomputes a
delta or percentage client-side, and always surfaces
`comparability_warnings` as a persistent banner, not a dismissible
tooltip, consistent with Phase 10's own disclosure discipline.

### Digital Twin non-mutation and observation/model/assumption distinction, carried into the UI

`LayerControlPanel` groups layers by the same categories Phase 9 already
derives (`terrain`/`hazard`/`exposure`/`risk`/`route`/`observation`) and
never invents a new taxonomy. `RiskInfoPanel` explicitly labels
`vulnerability_weight`/`consequence_weight` as "user-declared assumptions
(not measured facts)" and shows `risk_score` beside (never combined
with) exposure quantity, mirroring `risk.py`'s own "never aggregate"
rule. Every info panel renders its source analysis's `limitations` array
as a persistent callout, not a tooltip -- regression-tested directly
(`panels/infoPanels.test.tsx`) so a future refactor can't silently drop
it.

## Consequences

- One new backend dependency: `Pillow==11.0.0`.
- New frontend dependencies: `maplibre-gl`, `three` (+ `@types/three`),
  `react-router-dom`, `zustand`; dev: `vitest`, `@testing-library/react`,
  `@testing-library/jest-dom`, `jsdom`, `@playwright/test`, `@types/node`.
  Explicitly not added: `react-map-gl`, `proj4`/`proj4js`,
  `@tanstack/react-query`, any UI component library, **Cesium** (no true
  3D-globe requirement exists; MapLibre 2D + Three.js 3D already cover
  the need, and Cesium alongside them would violate "no unnecessary
  additional mapping frameworks" outright).
- No new database tables/columns/migrations -- Phase 11 is purely a
  presentation-format conversion on top of the existing `datasets` table.
- Data Hub (`/data-hub`) is fully preserved, verified by the fact that
  none of its source files were touched.
- Backend suite grew by the new `app/services/preview.py` pure-function
  and API integration tests; frontend gained its first-ever test suite
  (Vitest/RTL unit + component tests, one scoped Playwright E2E smoke
  spec) plus the heightmap precision spike.
- Deferred: Dataset-referenced blocked-segments-style raster tiling/COG
  pyramid infrastructure; server-side viewport/bbox-filtered vector
  queries and true pagination; `@tanstack/react-query`; raster
  pixel-value inspection on click; mobile-first responsive redesign;
  side-by-side 2D map comparison view and a true split 2D/3D layout;
  advanced 3D effects (shadows/lighting realism, LOD streaming, animated
  hazard time-series playback); building/road/point 3D extrusion beyond
  the terrain+hazard-drape core (see the implementation report's
  "scope trims" section); any AI assistant or natural-language query
  (Phase 12); live weather/traffic/sensor/satellite feeds (forbidden by
  constraints, no stub integration point built anywhere).
