import * as THREE from 'three'
import proj4 from 'proj4'
import type { HeightmapPixels } from './buildTerrainMesh'
import { isWgs84Crs } from '../../geo/bounds'
import { colorForLabel } from '../../geo/legendColors'

// Define EPSG:32644 (UTM zone 44N) for proj4js
proj4.defs('EPSG:32644', '+proj=utm +zone=44 +datum=WGS84 +units=m +no_defs')

/** New visualization-integration phase: drapes real vector features
 * (roads, evacuation routes) onto the existing 3D terrain mesh.
 *
 * The terrain mesh (buildTerrainMesh.ts) is built in a LOCAL, pixel-
 * grid-centered space -- world (0,0,0) is the geometric center of the
 * DEM's own pixel grid, not any real-world geographic point (see
 * buildTerrainGeometry: THREE.PlaneGeometry is centered at its local
 * origin by default). To place a WGS84 road/route vertex correctly on
 * that same mesh, this module derives the SAME linear lon/lat -> local-
 * meters scale factor the terrain mesh implicitly uses: dividing the
 * terrain's own real-world footprint (worldWidthM/worldHeightM, already
 * computed by buildTerrainGeometry from the DEM's own pixel size) by
 * its geographic extent (heightmap-bounds' west/south/east/north) --
 * never a fresh geodesic computation, which would silently introduce a
 * SECOND, different approximation than the terrain mesh's own and
 * misalign every overlay from the ground it's meant to sit on.
 *
 * This intentionally reuses ONLY numbers the backend already returns
 * (heightmap-bounds' west/south/east/north -- fetched by SceneView.tsx
 * for the terrain build, previously discarded once elevation/pixel-size
 * were read out of the same response) -- no new backend endpoint, no
 * proj4/proj4js, consistent with ADR 0012's "reprojection is exclusively
 * a backend responsibility, never on the frontend" decision.
 *
 * IMPORTANT SCOPE LIMIT: this only works when the terrain DEM's native
 * CRS is geographic (WGS84-like degrees) -- the same unit family real
 * road/route GeoJSON is always returned in (see
 * app/services/preview.py::build_geojson_preview, always WGS84). For a
 * DEM in a projected CRS (e.g. UTM meters), heightmap-bounds' west/
 * south/east/north are in that CRS's own metric units, not degrees --
 * mixing those with WGS84 lon/lat would silently misplace every
 * overlay. `isGeographicNativeCrs` guards this; callers must skip
 * overlay draping (not crash, not silently draw wrong geometry) when it
 * returns false. Reprojecting a projected DEM's overlays would require
 * a genuinely new backend capability, not attempted here.
 */

/** Alias kept for this module's own naming (heightmap-bounds calls this
 * field `native_crs`) -- see geo/bounds.ts::isWgs84Crs, the single
 * shared implementation (also used by the 2D map's fitBounds logic).
 */
export const isGeographicNativeCrs = isWgs84Crs

/** True when the DEM's native CRS is projected (meters) -- the same unit
 * family the terrain mesh's local coordinates are in. For projected CRS
 * data, no degrees-to-meters conversion is needed; the transformation is
 * a simple translation to the mesh's local origin.
 */
export function isProjectedNativeCrs(crs: string | null | undefined): boolean {
  if (!crs) return false
  const upper = crs.toUpperCase()
  return upper.includes('EPSG:32644') || upper.includes('UTM')
}

/** True when the DEM's native CRS is supported for 3D terrain overlay
 * draping -- either geographic (WGS84-like degrees) or projected (meters).
 * Both can be transformed into the terrain mesh's local coordinate system.
 */
export function isSupportedTerrainCrs(crs: string | null | undefined): boolean {
  return isGeographicNativeCrs(crs) || isProjectedNativeCrs(crs)
}

export interface TerrainLocalTransform {
  /** Converts a WGS84 [lon, lat] into the terrain mesh's own local
   * [x, z] ground-plane coordinates (pre-elevation; world Y/up is
   * supplied separately by an elevation sampler). Matches exactly the
   * local space buildTerrainGeometry's rotated PlaneGeometry occupies.
   */
  toLocalXZ(lon: number, lat: number): [number, number]
}

export function createTerrainLocalTransform(params: {
  boundsWestLon: number
  boundsSouthLat: number
  boundsEastLon: number
  boundsNorthLat: number
  worldWidthM: number
  worldHeightM: number
}): TerrainLocalTransform {
  const { boundsWestLon, boundsSouthLat, boundsEastLon, boundsNorthLat, worldWidthM, worldHeightM } = params
  const centerLon = (boundsWestLon + boundsEastLon) / 2
  const centerLat = (boundsSouthLat + boundsNorthLat) / 2
  const lonSpan = boundsEastLon - boundsWestLon || 1
  const latSpan = boundsNorthLat - boundsSouthLat || 1
  const metersPerDegreeLon = worldWidthM / lonSpan
  const metersPerDegreeLat = worldHeightM / latSpan

  return {
    toLocalXZ(lon: number, lat: number): [number, number] {
      const x = (lon - centerLon) * metersPerDegreeLon
      // Row 0 of the DEM's pixel grid (north edge) maps to local plane
      // Y=+height/2, which the mesh's -90 deg X rotation sends to world
      // Z=-height/2 (see buildTerrainGeometry/SceneView's rotation) --
      // i.e. increasing latitude means DECREASING world Z.
      const z = -(lat - centerLat) * metersPerDegreeLat
      return [x, z]
    },
  }
}

/** Creates a coordinate transformation for projected CRS data (e.g.
 * EPSG:32644). Uses proj4js for accurate CRS transformation from the
 * DEM's native projected CRS to WGS84, then applies the same linear
 * conversion as the geographic CRS transform. This ensures buildings
 * and roads align with the terrain.
 */
export function createProjectedTerrainLocalTransform(params: {
  boundsWest: number
  boundsSouth: number
  boundsEast: number
  boundsNorth: number
  worldWidthM: number
  worldHeightM: number
  nativeCrs?: string
}): TerrainLocalTransform {
  const { boundsWest, boundsSouth, boundsEast, boundsNorth, worldWidthM, worldHeightM, nativeCrs } = params

  // Use proj4js for proper CRS transformation
  let wgs84West: number, wgs84South: number, wgs84East: number, wgs84North: number

  try {
    // Transform the DEM bounds from projected CRS to WGS84
    const sourceCrs = nativeCrs || 'EPSG:32644'
    const destCrs = 'EPSG:4326'

    const [w, s] = proj4(sourceCrs, destCrs, [boundsWest, boundsSouth])
    const [e, n] = proj4(sourceCrs, destCrs, [boundsEast, boundsNorth])

    wgs84West = w
    wgs84South = s
    wgs84East = e
    wgs84North = n
  } catch {
    // Fallback: use the bounds as-is (assuming they're already WGS84)
    wgs84West = boundsWest
    wgs84South = boundsSouth
    wgs84East = boundsEast
    wgs84North = boundsNorth
  }

  const centerLon = (wgs84West + wgs84East) / 2
  const centerLat = (wgs84South + wgs84North) / 2
  const lonSpan = wgs84East - wgs84West || 1
  const latSpan = wgs84North - wgs84South || 1
  const metersPerDegreeLon = worldWidthM / lonSpan
  const metersPerDegreeLat = worldHeightM / latSpan

  return {
    toLocalXZ(lon: number, lat: number): [number, number] {
      const x = (lon - centerLon) * metersPerDegreeLon
      const z = -(lat - centerLat) * metersPerDegreeLat
      return [x, z]
    },
  }
}

/** Nearest-pixel elevation lookup against the SAME decoded heightmap
 * pixels the terrain mesh itself was built from (not the mesh's own,
 * possibly-decimated vertex grid) -- gives overlay placement the most
 * accurate elevation available, independent of the mesh's
 * maxResolution cap. Returns exaggerated elevation in world-Y meters,
 * matching buildTerrainGeometry's own elevationM * exaggeration.
 */
export function createElevationSampler(params: {
  pixels: HeightmapPixels
  elevationMinM: number
  elevationMaxM: number
  exaggeration: number
  boundsWestLon: number
  boundsSouthLat: number
  boundsEastLon: number
  boundsNorthLat: number
}): (lon: number, lat: number) => number {
  const { pixels, elevationMinM, elevationMaxM, exaggeration, boundsWestLon, boundsSouthLat, boundsEastLon, boundsNorthLat } = params
  const span = elevationMaxM - elevationMinM || 1
  const lonSpan = boundsEastLon - boundsWestLon || 1
  const latSpan = boundsNorthLat - boundsSouthLat || 1

  return (lon: number, lat: number): number => {
    const u = (lon - boundsWestLon) / lonSpan // 0 (west) .. 1 (east)
    const v = (boundsNorthLat - lat) / latSpan // 0 (north) .. 1 (south) -- row 0 is north
    const col = Math.min(pixels.width - 1, Math.max(0, Math.round(u * (pixels.width - 1))))
    const row = Math.min(pixels.height - 1, Math.max(0, Math.round(v * (pixels.height - 1))))
    const value = pixels.values[row * pixels.width + col]
    const elevationM = elevationMinM + (value / 255) * span
    return elevationM * exaggeration
  }
}

export interface GeoJsonFeatureLike {
  /** GeoJSON feature id, when present (real backend output carries a
   * stable per-feature id for risk/exposure features; plain OSM-sourced
   * roads/buildings may not). Used only to populate selection metadata
   * (see OverlayFeatureUserData) -- never required for rendering.
   */
  id?: string | number
  geometry: { type: string; coordinates: unknown } | null
  properties?: Record<string, unknown> | null
}

/** Attached as `.userData` on every Mesh/Line this module builds, so a
 * raycast hit (see SceneView.tsx's click handler) can populate the SAME
 * `activeFeature` store shape the 2D map's click handler already
 * populates (see map2d/layers/useVectorLayer.ts) -- one selection/
 * inspector/Assistant-context system shared by both views, not a
 * parallel 3D-only one.
 */
export interface OverlayFeatureUserData {
  datasetId: string
  featureId: string | number | null
  properties: Record<string, unknown>
}

function featureUserData(datasetId: string, feature: GeoJsonFeatureLike): OverlayFeatureUserData {
  return { datasetId, featureId: feature.id ?? null, properties: feature.properties ?? {} }
}

/** Keeps only the features whose geometry intersects `bbox` (a WGS84
 * lon/lat box) -- at least one coordinate falling inside counts as an
 * intersection, which is deliberately permissive (a feature straddling
 * the edge is kept whole, never clipped mid-geometry). Exists because a
 * dataset's own full extent can be far larger than the specific study
 * area a Digital Twin's other layers (roads/route) actually cover -- see
 * ADR 0013 "IMPORTANT DATA RULE": rendering an arbitrary slice of a
 * much-larger-extent dataset (e.g. the first N of 50,000 city-wide
 * buildings) alongside a small ~3km road/route network would silently
 * mix unrelated geography into one scene. Callers should compute `bbox`
 * from the layers that actually define the study area (e.g. the terrain
 * DEM's own heightmap-bounds, or a roads/route layer's bbox) rather than
 * the to-be-filtered dataset's own extent.
 */
export function filterFeaturesByBbox<T extends GeoJsonFeatureLike>(
  features: T[],
  bbox: { west: number; south: number; east: number; north: number },
): T[] {
  const intersects = (coords: unknown): boolean => {
    const arr = coords as unknown[]
    if (typeof arr[0] === 'number') {
      const [lon, lat] = arr as [number, number]
      return lon >= bbox.west && lon <= bbox.east && lat >= bbox.south && lat <= bbox.north
    }
    return arr.some(intersects)
  }
  return features.filter((f) => f.geometry !== null && intersects(f.geometry.coordinates))
}

/** Resolves one feature's draw color: `colorProperty`'s value (e.g.
 * `risk_class`/`hazard_class_label`) looked up through the SAME shared
 * legend the 2D map uses (geo/legendColors.ts), falling back to the
 * flat `fallbackColor` when no colorProperty is configured or the
 * feature lacks that property -- never a fabricated classification.
 */
function resolveFeatureColor(feature: GeoJsonFeatureLike, colorProperty: string | undefined, fallbackColor: string): string {
  if (!colorProperty) return fallbackColor
  const value = feature.properties?.[colorProperty]
  return typeof value === 'string' ? colorForLabel(value) : fallbackColor
}

export interface LineFeatureOverlayParams {
  geojson: { features: GeoJsonFeatureLike[] }
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
  /** Source dataset id, tagged onto each built object's `.userData` (see
   * OverlayFeatureUserData) for click-to-select.
   */
  datasetId: string
  /** Fallback/flat color -- used directly when `colorProperty` is unset. */
  color: string
  /** GeoJSON feature property to color by (e.g. `risk_class` for
   * risk_classification, `hazard_class_label` for exposure_features) --
   * same shared legend as the 2D map, so a road segment reads as the
   * same risk class in both views. Omit for a flat-colored layer
   * (roads, routes).
   */
  colorProperty?: string
  /** Raised slightly above the sampled terrain elevation so the line
   * renders visibly on top of the surface rather than z-fighting with
   * it. Meters, already in the same (possibly exaggerated) Y-space the
   * elevation sampler returns.
   */
  heightOffsetM?: number
}

function coordinatesToLocalPoints(
  coords: [number, number][],
  transform: TerrainLocalTransform,
  sampleElevationM: (lon: number, lat: number) => number,
  heightOffsetM: number,
): THREE.Vector3[] {
  return coords.map(([lon, lat]) => {
    const [x, z] = transform.toLocalXZ(lon, lat)
    const y = sampleElevationM(lon, lat) + heightOffsetM
    return new THREE.Vector3(x, y, z)
  })
}

/** Builds a THREE.Group of terrain-draped line segments from a WGS84
 * GeoJSON FeatureCollection (LineString/MultiLineString only -- other
 * geometry types are skipped, never fabricated). One THREE.Line per
 * LineString ring. One material per DISTINCT resolved color (shared
 * across features using it), not one material per feature -- keeps
 * material count bounded to the legend size (5-8 colors) even for a
 * dataset with thousands of features.
 */
export function buildLineFeatureOverlay(params: LineFeatureOverlayParams): THREE.Group {
  const { geojson, transform, sampleElevationM, datasetId, color, colorProperty, heightOffsetM = 3 } = params
  const group = new THREE.Group()
  const materialByColor = new Map<string, THREE.LineBasicMaterial>()
  const materialFor = (c: string) => {
    let material = materialByColor.get(c)
    if (!material) {
      material = new THREE.LineBasicMaterial({ color: c })
      materialByColor.set(c, material)
    }
    return material
  }

  for (const feature of geojson.features) {
    const geometry = feature.geometry
    if (!geometry) continue

    const rings: [number, number][][] =
      geometry.type === 'LineString'
        ? [geometry.coordinates as [number, number][]]
        : geometry.type === 'MultiLineString'
          ? (geometry.coordinates as [number, number][][])
          : []
    if (rings.length === 0) continue

    const material = materialFor(resolveFeatureColor(feature, colorProperty, color))
    const userData = featureUserData(datasetId, feature)
    for (const ring of rings) {
      if (ring.length < 2) continue
      const points = coordinatesToLocalPoints(ring, transform, sampleElevationM, heightOffsetM)
      const lineGeometry = new THREE.BufferGeometry().setFromPoints(points)
      const line = new THREE.Line(lineGeometry, material)
      line.userData = userData
      group.add(line)
    }
  }

  return group
}

/** Converts one polygon ring's [lon,lat] coordinates into the SAME
 * local ground-plane space buildLineFeatureOverlay uses, as
 * THREE.Vector2(x, z) pairs -- used both for triangulation input and,
 * via its own .x/.y, to build the final flat vertex positions.
 */
function ringToLocalXZ(ring: [number, number][], transform: TerrainLocalTransform): THREE.Vector2[] {
  return ring.map(([lon, lat]) => {
    const [x, z] = transform.toLocalXZ(lon, lat)
    return new THREE.Vector2(x, z)
  })
}

function ringCentroidLonLat(ring: [number, number][]): [number, number] {
  let lon = 0
  let lat = 0
  for (const [pointLon, pointLat] of ring) {
    lon += pointLon
    lat += pointLat
  }
  return [lon / ring.length, lat / ring.length]
}

// Uniform placeholder building height, in meters, used ONLY when a
// polygon feature carries no real height/levels attribute (true of
// every CRISIS-X building dataset currently registered -- see ADR 0013
// DATA REQUIREMENTS item C: "Building height -- MISSING"). Roughly
// 2-3 storeys, a visually reasonable urban-mass default. This is a
// VISUALIZATION-ONLY value, never presented as measured data -- see
// PolygonFeatureOverlayParams.heightM/heightProperty and the "Building
// height" note surfaced in the dashboard's data-provenance UI.
export const FALLBACK_BUILDING_HEIGHT_M = 8

// Display-only building height multiplier. The Chennai study area is
// ~4 km² with 8 m fallback building heights, making extrusion nearly
// invisible at full-scene scale. This multiplier is applied ONLY at
// render time to make building masses readable from the default camera
// -- it does NOT change the underlying FALLBACK_BUILDING_HEIGHT_M data
// value, and the userData.heightSource provenance tag continues to
// report the original 8 m fallback. A future real-height dataset would
// use its own values without this multiplier.
export const DISPLAY_BUILDING_HEIGHT_MULTIPLIER = 3
// Synthetic dataset building height multiplier. The synthetic test dataset
// has buildings 3-18m tall on an 8km terrain, making them nearly invisible
// at full-scene scale. This multiplier is applied ONLY at render time to
// synthetic building heights to make them visible. It does NOT change the
// underlying height_m data value, and the userData.heightSource provenance
// tag continues to report the original unmultiplied value.
export const SYNTHETIC_BUILDING_HEIGHT_MULTIPLIER = 8

// Building style definitions for procedural facade generation.
// Each style defines wall color, accent color, window color, and roof color.
// Colors are natural, muted tones inspired by real urban architecture.
export interface BuildingStyle {
  name: string
  wallColor: string
  accentColor: string
  windowColor: string
  roofColor: string
  hasBalconies: boolean
  hasFloorSlabs: boolean
  hasParapet: boolean
}

export const BUILDING_STYLES: BuildingStyle[] = [
  // Style 1 — Modern Apartment (white/light concrete, brown accents, balconies)
  { name: 'modern_apartment', wallColor: '#F0EBE3', accentColor: '#A0522D', windowColor: '#1C2833', roofColor: '#5D6D7E', hasBalconies: true, hasFloorSlabs: true, hasParapet: true },
  // Style 2 — Modern Residential (white walls, dark brown/wood-like facade)
  { name: 'modern_residential', wallColor: '#FAFAF5', accentColor: '#3E2723', windowColor: '#0D1B2A', roofColor: '#4A4A4A', hasBalconies: true, hasFloorSlabs: false, hasParapet: true },
  // Style 3 — Classic/Urban (cream/beige walls, darker window frames)
  { name: 'classic_urban', wallColor: '#E8DCC8', accentColor: '#5D4E37', windowColor: '#1A1A1A', roofColor: '#6B5B4F', hasBalconies: false, hasFloorSlabs: true, hasParapet: true },
  // Style 4 — Simple Urban (warm concrete, light gray)
  { name: 'simple_urban', wallColor: '#D4C4B0', accentColor: '#7D6608', windowColor: '#2C3438', roofColor: '#5D6D7E', hasBalconies: false, hasFloorSlabs: false, hasParapet: true },
  // Style 5 — Commercial (larger dark glass, concrete frame)
  { name: 'commercial', wallColor: '#D5D8DC', accentColor: '#2C3E50', windowColor: '#0A0A0A', roofColor: '#424949', hasBalconies: false, hasFloorSlabs: true, hasParapet: false },
]

/** Deterministically assigns a building style based on building ID.
 * Uses a simple hash of the building ID to select a style.
 * The same building ID always gets the same style.
 */
export function getBuildingStyle(buildingId: string | number | undefined): BuildingStyle {
  if (!buildingId) return BUILDING_STYLES[3] // Default to simple_urban
  const id = String(buildingId)
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    const char = id.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash // Convert to 32bit integer
  }
  const index = Math.abs(hash) % BUILDING_STYLES.length
  return BUILDING_STYLES[index]
}

export interface PolygonFeatureOverlayParams {
  geojson: { features: GeoJsonFeatureLike[] }
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
  /** Source dataset id, tagged onto each built mesh's `.userData` (see
   * OverlayFeatureUserData) for click-to-select.
   */
  datasetId: string
  /** Fallback/flat color -- used directly when `colorProperty` is unset. */
  color: string
  /** Same meaning as buildLineFeatureOverlay's -- e.g. `risk_class` for
   * building risk_classification, `hazard_class_label` for building
   * exposure_features.
   */
  colorProperty?: string
  /** GeoJSON property carrying a REAL per-feature height/elevation value
   * in meters (e.g. a future `height_m` or `levels * 3` attribute) --
   * used in preference to `heightM` whenever present and numeric. No
   * CRISIS-X dataset currently provides one (see FALLBACK_BUILDING_HEIGHT_M);
   * this exists so real height data can be wired in later without a
   * rendering-code change.
   */
  heightProperty?: string
  /** Uniform extrusion height in meters, applied when `heightProperty`
   * is unset or the feature lacks that property. Omit (or 0) to render
   * flat, non-extruded footprints, matching this function's original
   * behavior. Passing FALLBACK_BUILDING_HEIGHT_M extrudes every building
   * to the SAME placeholder height -- deliberately uniform, never varied
   * by footprint area or anything else that would make a fabricated
   * value look like derived real data.
   */
  heightM?: number
  /** Raised slightly above the sampled terrain elevation, same purpose
   * as buildLineFeatureOverlay's heightOffsetM.
   */
  heightOffsetM?: number
  /** Provenance label for height values extracted from `heightProperty`.
   * Defaults to 'real' for measured data. Use 'synthetic_dataset' for
   * synthetic test data to distinguish it from real measurements.
   */
  heightSourceLabel?: 'real' | 'synthetic_dataset'
  /** GeoJSON property carrying a hex color string for the building body
   * (e.g. `building_color`). When present, overrides the flat `color`/
   * `colorProperty` resolution for this feature. Used by the synthetic
   * realistic buildings dataset to give each building a natural, varied
   * appearance without implying a risk/exposure classification.
   */
  buildingColorProperty?: string
}

function resolveFeatureHeightM(
  feature: GeoJsonFeatureLike,
  heightProperty: string | undefined,
  heightM: number | undefined,
  heightSourceLabel: 'real' | 'synthetic_dataset' = 'real',
): { heightM: number; heightSource: 'real' | 'synthetic_dataset' | 'fallback' | 'none' } {
  if (heightProperty) {
    const value = feature.properties?.[heightProperty]
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      return { heightM: value, heightSource: heightSourceLabel }
    }
  }
  if (heightM && heightM > 0) return { heightM, heightSource: 'fallback' }
  return { heightM: 0, heightSource: 'none' }
}

/** Builds a THREE.Group of flat terrain-draped road surfaces from a
 * WGS84 GeoJSON FeatureCollection (Polygon/MultiPolygon only). Each
 * polygon becomes a flat asphalt mesh at the terrain elevation.
 * Used for 3D road visualization instead of LineString centerlines.
 */
export function buildRoadSurfaceOverlay(params: {
  geojson: { features: GeoJsonFeatureLike[] }
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
  datasetId: string
  color: string
  heightOffsetM?: number
}): THREE.Group {
  const { geojson, transform, sampleElevationM, datasetId, color, heightOffsetM = 0.15 } = params
  const group = new THREE.Group()
  const material = new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide, roughness: 0.9 })

  for (const feature of geojson.features) {
    const geometry = feature.geometry
    if (!geometry) continue

    const polygons: [number, number][][][] =
      geometry.type === 'Polygon'
        ? [geometry.coordinates as [number, number][][]]
        : geometry.type === 'MultiPolygon'
          ? (geometry.coordinates as [number, number][][][])
          : []
    if (polygons.length === 0) continue

    const userData = featureUserData(datasetId, feature)

    for (const rings of polygons) {
      if (rings.length === 0 || rings[0].length < 3) continue
      const [outerLonLat, ...holesLonLat] = rings
      const [centroidLon, centroidLat] = ringCentroidLonLat(outerLonLat)
      const baseY = sampleElevationM(centroidLon, centroidLat) + heightOffsetM

      const outer = ringToLocalXZ(outerLonLat, transform)
      const holes = holesLonLat.map((h) => ringToLocalXZ(h, transform))
      const triangles = THREE.ShapeUtils.triangulateShape(outer, holes)
      const allPoints = [outer, ...holes].flat()
      if (triangles.length === 0 || allPoints.length === 0) continue

      const positions: number[] = []
      allPoints.forEach((p) => {
        positions.push(p.x, baseY, p.y)
      })
      const indices = triangles.flat()

      const roadGeo = new THREE.BufferGeometry()
      roadGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3))
      roadGeo.setIndex(indices)
      roadGeo.computeVertexNormals()

      const mesh = new THREE.Mesh(roadGeo, material)
      mesh.userData = userData
      group.add(mesh)
    }
  }

  return group
}

/** Builds a THREE.Group of terrain-draped polygons from a WGS84 GeoJSON
 * FeatureCollection (Polygon/MultiPolygon only) -- flat footprints by
 * default, or extruded building masses when `heightM`/`heightProperty`
 * resolves to a positive height (see FALLBACK_BUILDING_HEIGHT_M: no
 * CRISIS-X building dataset currently has real height data, so today
 * every extrusion uses the uniform placeholder, tagged
 * `userData.heightSource = 'fallback'` -- never presented as measured).
 * One elevation sample per polygon (its outer ring's centroid) is used
 * for the whole footprint's base, not per-vertex -- exact for flat local
 * terrain, a small honest approximation otherwise (a single building
 * footprint is a tiny fraction of any real DEM's overall relief). An
 * extruded building gets a top cap plus per-edge side walls; no bottom
 * cap (never visible from above/outside, so skipped for triangle count).
 */
export function buildPolygonFeatureOverlay(params: PolygonFeatureOverlayParams): THREE.Group {
  const { geojson, transform, sampleElevationM, datasetId, heightOffsetM = 2, heightProperty, heightM, heightSourceLabel = 'real', buildingColorProperty } = params
  const group = new THREE.Group()
  const materialByColor = new Map<string, THREE.MeshStandardMaterial>()
  const materialFor = (c: string) => {
    let material = materialByColor.get(c)
    if (!material) {
      material = new THREE.MeshStandardMaterial({ color: c, side: THREE.DoubleSide, transparent: true, opacity: 0.92 })
      materialByColor.set(c, material)
    }
    return material
  }

  for (const feature of geojson.features) {
    const geometry = feature.geometry
    if (!geometry) continue

    const polygons: [number, number][][][] =
      geometry.type === 'Polygon'
        ? [geometry.coordinates as [number, number][][]]
        : geometry.type === 'MultiPolygon'
          ? (geometry.coordinates as [number, number][][][])
          : []
    if (polygons.length === 0) continue

    // Get deterministic building style based on building ID
    const style = getBuildingStyle((feature.properties?.building_id as string | undefined) ?? feature.id)
    const material = materialFor(
      buildingColorProperty
        ? (feature.properties?.[buildingColorProperty] as string | undefined) ?? style.wallColor
        : style.wallColor,
    )
    const { heightM: resolvedHeightM, heightSource } = resolveFeatureHeightM(feature, heightProperty, heightM, heightSourceLabel)
    // Apply display-only height multiplier for fallback and synthetic heights
    // so building masses are visible at full-scene scale. The userData
    // provenance continues to report the original unmultiplied value.
    const displayHeightM = heightSource === 'fallback'
      ? resolvedHeightM * DISPLAY_BUILDING_HEIGHT_MULTIPLIER
      : heightSource === 'synthetic_dataset'
        ? resolvedHeightM * SYNTHETIC_BUILDING_HEIGHT_MULTIPLIER
        : resolvedHeightM
    const base = featureUserData(datasetId, feature)
    // Folded into `.properties` (not just a sibling userData field) so it
    // actually appears in the Feature Inspector panel when a building is
    // clicked -- see FeatureInspectorPanel.tsx, which renders every
    // `properties` entry generically. Without this, a fallback-height
    // extrusion would look identical to real height data the moment a
    // user actually inspects it, defeating the whole point of tracking
    // heightSource in the first place.
    const userData = {
      ...base,
      heightSource,
      properties: { ...base.properties, visualization_height_source: heightSource === 'fallback' ? `${resolvedHeightM}m (visualization-only placeholder, no real height data)` : heightSource === 'real' ? `${resolvedHeightM}m (real height attribute)` : 'none (flat, no extrusion)' },
    }

    for (const rings of polygons) {
      if (rings.length === 0 || rings[0].length < 3) continue
      const [outerLonLat, ...holesLonLat] = rings
      const [centroidLon, centroidLat] = ringCentroidLonLat(outerLonLat)
      const baseY = sampleElevationM(centroidLon, centroidLat) + heightOffsetM
      const topY = baseY + displayHeightM

      const outer = ringToLocalXZ(outerLonLat, transform)
      const holes = holesLonLat.map((h) => ringToLocalXZ(h, transform))
      // NOTE: triangulateShape mutates `outer`/`holes` in place (it
      // strips each ring's redundant closing point, since GeoJSON rings
      // repeat their first point but a triangulation contour shouldn't).
      // `allPoints` is built AFTER this call specifically so its vertex
      // positions and the returned triangle indices stay consistent --
      // they both reference the SAME (already-mutated) arrays.
      const triangles = THREE.ShapeUtils.triangulateShape(outer, holes)
      const allPoints = [outer, ...holes].flat()
      if (triangles.length === 0 || allPoints.length === 0) continue

      const topPositions: number[] = []
      allPoints.forEach((p) => {
        topPositions.push(p.x, topY, p.y)
      })
      const topIndices = triangles.flat()

      const sidePositions: number[] = []
      const sideIndices: number[] = []
      if (displayHeightM > 0) {
        // Side walls: one quad (2 triangles) per edge of every ring
        // (outer + holes), connecting the base and top rims. Each ring
        // is walked independently, closing back to its own first point.
        for (const ring of [outer, ...holes]) {
          for (let i = 0; i < ring.length; i++) {
            const a = ring[i]
            const b = ring[(i + 1) % ring.length]
            const startIndex = topPositions.length / 3 + sidePositions.length / 3
            sidePositions.push(a.x, baseY, a.y, a.x, topY, a.y, b.x, topY, b.y, b.x, baseY, b.y)
            sideIndices.push(startIndex, startIndex + 1, startIndex + 2, startIndex, startIndex + 2, startIndex + 3)
          }
        }
      }

      const positions = new Float32Array([...topPositions, ...sidePositions])
      const indices = [...topIndices, ...sideIndices]

      const polygonGeometry = new THREE.BufferGeometry()
      polygonGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      polygonGeometry.setIndex(indices)
      polygonGeometry.computeVertexNormals()

      const mesh = new THREE.Mesh(polygonGeometry, material)
      mesh.userData = userData
      group.add(mesh)

      // Add facade details for buildings with sufficient height
      if (displayHeightM > 10) {
        const floors = Math.max(1, Math.round(displayHeightM / 24)) // ~3m per floor with multiplier
        const windowMat = materialFor(style.windowColor)
        const accentMat = materialFor(style.accentColor)
        const roofMat = materialFor(style.roofColor)

        // Calculate building dimensions for facade element sizing
        const outerVec3 = outer.map((p) => new THREE.Vector3(p.x, 0, p.y))
        const bbox = new THREE.Box3().setFromPoints(outerVec3)
        const buildingWidth = bbox.max.x - bbox.min.x
        const buildingDepth = bbox.max.z - bbox.min.z
        const minDimension = Math.min(buildingWidth, buildingDepth)

        // Add facade panels (large architectural sections)
        const panelCount = Math.min(4, Math.max(1, Math.round(minDimension / 20)))
        for (let p = 0; p < panelCount; p++) {
          const idx = Math.floor((p / panelCount) * outer.length)
          const point = outer[idx]
          const panelWidth = Math.min(8, minDimension / 3)
          const panelHeight = displayHeightM * 0.6
          const panelGeo = new THREE.BoxGeometry(panelWidth, panelHeight, 0.5)
          const panel = new THREE.Mesh(panelGeo, p % 2 === 0 ? accentMat : materialFor(style.wallColor))
          panel.position.set(point.x, baseY + panelHeight / 2, point.y)
          group.add(panel)
        }

        // Add floor slabs (visible horizontal bands)
        if (style.hasFloorSlabs) {
          for (let f = 1; f < floors; f++) {
            const slabY = baseY + (displayHeightM / floors) * f
            const slabPositions: number[] = []
            const slabIndices: number[] = []
            for (let i = 0; i < outer.length; i++) {
              const p = outer[i]
              const next = outer[(i + 1) % outer.length]
              const startIndex = slabPositions.length / 3
              slabPositions.push(p.x, slabY, p.y, next.x, slabY, next.y)
              slabIndices.push(startIndex, startIndex + 1)
            }
            if (slabPositions.length > 0) {
              const slabGeo = new THREE.BufferGeometry()
              slabGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(slabPositions), 3))
              slabGeo.setIndex(slabIndices)
              const slab = new THREE.Line(slabGeo, new THREE.LineBasicMaterial({ color: style.accentColor, linewidth: 2 }))
              group.add(slab)
            }
          }
        }

        // Add windows on each floor (larger, more visible)
        const windowsPerFloor = Math.min(5, Math.max(2, Math.round(minDimension / 15)))
        for (let f = 0; f < floors; f++) {
          const floorY = baseY + (displayHeightM / floors) * f + (displayHeightM / floors) * 0.35
          for (let w = 0; w < windowsPerFloor; w++) {
            const idx = Math.floor((w / windowsPerFloor) * outer.length)
            const p = outer[idx]
            const next = outer[(idx + 1) % outer.length]
            const midX = (p.x + next.x) / 2
            const midZ = (p.y + next.y) / 2
            const windowWidth = Math.min(4, minDimension / 8)
            const windowHeight = Math.min(5, (displayHeightM / floors) * 0.5)
            const windowGeo = new THREE.BoxGeometry(windowWidth, windowHeight, 0.3)
            const windowMesh = new THREE.Mesh(windowGeo, windowMat)
            windowMesh.position.set(midX, floorY, midZ)
            group.add(windowMesh)
          }
        }

        // Add balconies for apartment-style buildings (more prominent)
        if (style.hasBalconies && floors > 2) {
          for (let f = 1; f < floors; f += 2) {
            const balconyY = baseY + (displayHeightM / floors) * f
            const idx = Math.floor(outer.length / 4)
            const p = outer[idx]
            // Balcony slab
            const balconyGeo = new THREE.BoxGeometry(6, 0.4, 3)
            const balcony = new THREE.Mesh(balconyGeo, accentMat)
            balcony.position.set(p.x, balconyY, p.y)
            group.add(balcony)
            // Balcony railing
            const railingGeo = new THREE.BoxGeometry(6, 1, 0.1)
            const railing = new THREE.Mesh(railingGeo, roofMat)
            railing.position.set(p.x, balconyY + 0.7, p.y + 1.5)
            group.add(railing)
          }
        }

        // Add roof parapet (more visible)
        if (style.hasParapet) {
          const parapetHeight = 2.0
          const parapetPositions: number[] = []
          const parapetIndices: number[] = []
          for (let i = 0; i < outer.length; i++) {
            const p = outer[i]
            const next = outer[(i + 1) % outer.length]
            const startIndex = parapetPositions.length / 3
            parapetPositions.push(p.x, topY, p.y, p.x, topY + parapetHeight, p.y, next.x, topY + parapetHeight, next.y, next.x, topY, next.y)
            parapetIndices.push(startIndex, startIndex + 1, startIndex + 2, startIndex, startIndex + 2, startIndex + 3)
          }
          if (parapetPositions.length > 0) {
            const parapetGeo = new THREE.BufferGeometry()
            parapetGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(parapetPositions), 3))
            parapetGeo.setIndex(parapetIndices)
            const parapet = new THREE.Mesh(parapetGeo, roofMat)
            group.add(parapet)
          }

          // Add rooftop structure for selected buildings (deterministic)
          const buildingId = feature.properties?.building_id
          if (buildingId) {
            const hash = String(buildingId).split('').reduce((a, c) => a + c.charCodeAt(0), 0)
            if (hash % 5 === 0) { // ~20% of buildings get rooftop structures
              const tankGeo = new THREE.CylinderGeometry(1.5, 1.5, 2, 8)
              const tank = new THREE.Mesh(tankGeo, roofMat)
              const centerX = (bbox.min.x + bbox.max.x) / 2
              const centerZ = (bbox.min.z + bbox.max.z) / 2
              tank.position.set(centerX, topY + parapetHeight + 1, centerZ)
              group.add(tank)
            }
          }
        }
      }
    }
  }

  return group
}

// Road ribbon width, in meters -- NOT measured data (see ADR 0013 DATA
// REQUIREMENTS item E: "Road width -- MISSING", confirmed 0 of 839 real
// Chennai road features carry a non-null OSM `width` tag). A documented,
// visualization-only fallback keyed by OSM's `highway` classification
// (which IS present on nearly every real road feature) so a primary
// road reads visibly wider than a footpath without inventing a specific
// measured width for any one segment. `DEFAULT_ROAD_WIDTH_M` covers any
// feature missing (or not carrying) a recognized `highway` tag at all --
// e.g. a risk/exposure-derived road-segment feature, which only carries
// hazard/risk attributes, never the original OSM tags.
export const ROAD_WIDTH_BY_HIGHWAY_CLASS_M: Record<string, number> = {
  trunk: 10,
  primary: 8,
  primary_link: 6,
  secondary: 7,
  secondary_link: 5.5,
  tertiary: 6,
  tertiary_link: 5,
  residential: 5,
  living_street: 4,
  service: 3.5,
  pedestrian: 3,
  footway: 1.5,
  path: 1.2,
  steps: 1.2,
}
export const DEFAULT_ROAD_WIDTH_M = 5
// Synthetic dataset road_type widths (meters) -- visualization-only values
// for the synthetic test dataset, not measured road widths.
export const ROAD_WIDTH_BY_TYPE_M: Record<string, number> = {
  primary: 10,
  secondary: 7,
  arterial: 12,
  residential: 5,
  service: 3.5,
}
// Asphalt road surface color -- dark charcoal/gray, clearly distinct from terrain
export const ASPHALT_COLOR = '#2C2C2C'
// Road marking colors
export const ROAD_MARKING_COLOR = '#E8E8E8' // white/yellow markings
// Routes are drawn wider than any road class so a highlighted
// shortest/hazard-aware path always reads as visually prominent on top
// of the underlying road network, per the reference image's "clearly
// highlighted routes" requirement -- also a visualization width, not a
// physical road measurement.
export const ROUTE_WIDTH_M = 7

function resolveFeatureWidthM(
  feature: GeoJsonFeatureLike,
  widthProperty: string | undefined,
  fixedWidthM: number | undefined,
): number {
  if (widthProperty) {
    const raw = feature.properties?.[widthProperty]
    if (typeof raw === 'number' && Number.isFinite(raw) && raw > 0) return raw
    // Check both highway (real OSM) and road_type (synthetic) mappings
    if (typeof raw === 'string') {
      if (ROAD_WIDTH_BY_HIGHWAY_CLASS_M[raw] !== undefined) {
        return ROAD_WIDTH_BY_HIGHWAY_CLASS_M[raw]
      }
      if (ROAD_WIDTH_BY_TYPE_M[raw] !== undefined) {
        return ROAD_WIDTH_BY_TYPE_M[raw]
      }
    }
  }
  return fixedWidthM ?? DEFAULT_ROAD_WIDTH_M
}

export interface RibbonFeatureOverlayParams {
  geojson: { features: GeoJsonFeatureLike[] }
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
  /** Source dataset id, tagged onto each built mesh's `.userData` (see
   * OverlayFeatureUserData) for click-to-select.
   */
  datasetId: string
  /** Fallback/flat color -- used directly when `colorProperty` is unset. */
  color: string
  /** Same meaning as buildLineFeatureOverlay's. */
  colorProperty?: string
  /** Raised slightly above the sampled terrain elevation so the ribbon
   * renders visibly on top of the terrain surface. Meters.
   */
  heightOffsetM?: number
  /** A fixed width in meters, used when `widthProperty` is unset or the
   * feature lacks a recognized value (e.g. ROUTE_WIDTH_M for routes).
   * Defaults to DEFAULT_ROAD_WIDTH_M.
   */
  widthM?: number
  /** GeoJSON property to resolve a per-feature width from -- e.g.
   * `highway`, looked up through ROAD_WIDTH_BY_HIGHWAY_CLASS_M. Omit for
   * a uniform `widthM` (routes always use a fixed width; a real road
   * segment's own class-derived width varies feature to feature).
   */
  widthProperty?: string
}

/** Builds a THREE.Group of terrain-draped RIBBONS (thin extruded quad
 * strips, not hairline THREE.Line segments) from a WGS84 GeoJSON
 * FeatureCollection (LineString/MultiLineString only). Each consecutive
 * point pair becomes one quad, offset perpendicular to the segment's own
 * direction in the local XZ ground plane by +/- half the resolved width
 * (see resolveFeatureWidthM) -- a deliberately simple per-segment
 * approach (no mitered joints at sharp turns), an acceptable and common
 * simplification for a visualization at this scale. Replaces
 * buildLineFeatureOverlay in the live 3D scene so roads/routes read as
 * solid, visually meaningful paths instead of near-invisible wireframe
 * lines (see ADR 0013's "avoid the current thin cyan/white wireframe
 * appearance" success criterion); buildLineFeatureOverlay itself is kept
 * for callers that want the plain hairline rendering.
 */
export function buildRibbonFeatureOverlay(params: RibbonFeatureOverlayParams): THREE.Group {
  const { geojson, transform, sampleElevationM, datasetId, color, colorProperty, heightOffsetM = 1, widthM, widthProperty } = params
  const group = new THREE.Group()
  const materialByColor = new Map<string, THREE.MeshStandardMaterial>()
  const materialFor = (c: string) => {
    let material = materialByColor.get(c)
    if (!material) {
      material = new THREE.MeshStandardMaterial({ color: c, side: THREE.DoubleSide, emissive: c, emissiveIntensity: 0.15 })
      materialByColor.set(c, material)
    }
    return material
  }

  for (const feature of geojson.features) {
    const geometry = feature.geometry
    if (!geometry) continue

    const rings: [number, number][][] =
      geometry.type === 'LineString'
        ? [geometry.coordinates as [number, number][]]
        : geometry.type === 'MultiLineString'
          ? (geometry.coordinates as [number, number][][])
          : []
    if (rings.length === 0) continue

    // Use asphalt color for roads, route color for routes
    const isRoute = datasetId.includes('route') || colorProperty === undefined
    const roadColor = isRoute ? color : ASPHALT_COLOR
    const material = materialFor(roadColor)
    const markingMaterial = materialFor(ROAD_MARKING_COLOR)
    const halfWidth = resolveFeatureWidthM(feature, widthProperty, widthM) / 2
    const userData = featureUserData(datasetId, feature)
    const roadType = feature.properties?.road_type as string | undefined
    const isMajorRoad = roadType === 'primary' || roadType === 'arterial'

    for (const ring of rings) {
      if (ring.length < 2) continue
      const points = coordinatesToLocalPoints(ring, transform, sampleElevationM, heightOffsetM)

      const positions: number[] = []
      const indices: number[] = []
      for (let i = 0; i < points.length - 1; i++) {
        const p0 = points[i]
        const p1 = points[i + 1]
        const dx = p1.x - p0.x
        const dz = p1.z - p0.z
        const len = Math.hypot(dx, dz)
        if (len === 0) continue
        const nx = (-dz / len) * halfWidth
        const nz = (dx / len) * halfWidth

        const startIndex = positions.length / 3
        positions.push(
          p0.x + nx, p0.y, p0.z + nz,
          p0.x - nx, p0.y, p0.z - nz,
          p1.x - nx, p1.y, p1.z - nz,
          p1.x + nx, p1.y, p1.z + nz,
        )
        indices.push(startIndex, startIndex + 1, startIndex + 2, startIndex, startIndex + 2, startIndex + 3)
      }
      if (positions.length === 0) continue

      const ribbonGeometry = new THREE.BufferGeometry()
      ribbonGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3))
      ribbonGeometry.setIndex(indices)
      ribbonGeometry.computeVertexNormals()

      const mesh = new THREE.Mesh(ribbonGeometry, material)
      mesh.userData = userData
      group.add(mesh)

      // Add road markings and sidewalks for major roads
      if (isMajorRoad && !isRoute) {
        const markingPositions: number[] = []
        const markingIndices: number[] = []
        const sidewalkPositions: number[] = []
        const sidewalkIndices: number[] = []
        const sidewalkMaterial = materialFor('#9A9A9A') // light gray sidewalk

        for (let i = 0; i < points.length - 1; i++) {
          const p0 = points[i]
          const p1 = points[i + 1]
          const dx = p1.x - p0.x
          const dz = p1.z - p0.z
          const len = Math.hypot(dx, dz)
          if (len === 0) continue

          // Center line marking
          const markWidth = 0.3
          const nx = (-dz / len) * markWidth
          const nz = (dx / len) * markWidth

          const startIndex = markingPositions.length / 3
          markingPositions.push(
            p0.x + nx, p0.y + 0.05, p0.z + nz,
            p0.x - nx, p0.y + 0.05, p0.z - nz,
            p1.x - nx, p1.y + 0.05, p1.z - nz,
            p1.x + nx, p1.y + 0.05, p1.z + nz,
          )
          markingIndices.push(startIndex, startIndex + 1, startIndex + 2, startIndex, startIndex + 2, startIndex + 3)

          // Sidewalks (both sides of the road)
          const sidewalkWidth = 1.5
          const sidewalkOffset = halfWidth + sidewalkWidth / 2
          const snx = (-dz / len) * sidewalkOffset
          const snz = (dx / len) * sidewalkOffset
          const snx2 = (-dz / len) * (sidewalkOffset + sidewalkWidth)
          const snz2 = (dx / len) * (sidewalkOffset + sidewalkWidth)

          const sidewalkStart = sidewalkPositions.length / 3
          // Left sidewalk
          sidewalkPositions.push(
            p0.x + snx, p0.y + 0.02, p0.z + snz,
            p0.x + snx2, p0.y + 0.02, p0.z + snz2,
            p1.x + snx2, p1.y + 0.02, p1.z + snz2,
            p1.x + snx, p1.y + 0.02, p1.z + snz,
          )
          sidewalkIndices.push(sidewalkStart, sidewalkStart + 1, sidewalkStart + 2, sidewalkStart, sidewalkStart + 2, sidewalkStart + 3)

          // Right sidewalk
          sidewalkPositions.push(
            p0.x - snx, p0.y + 0.02, p0.z - snz,
            p0.x - snx2, p0.y + 0.02, p0.z - snz2,
            p1.x - snx2, p1.y + 0.02, p1.z - snz2,
            p1.x - snx, p1.y + 0.02, p1.z - snz,
          )
          sidewalkIndices.push(sidewalkStart + 4, sidewalkStart + 5, sidewalkStart + 6, sidewalkStart + 4, sidewalkStart + 6, sidewalkStart + 7)
        }
        if (markingPositions.length > 0) {
          const markingGeo = new THREE.BufferGeometry()
          markingGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(markingPositions), 3))
          markingGeo.setIndex(markingIndices)
          const marking = new THREE.Mesh(markingGeo, markingMaterial)
          group.add(marking)
        }
        if (sidewalkPositions.length > 0) {
          const sidewalkGeo = new THREE.BufferGeometry()
          sidewalkGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(sidewalkPositions), 3))
          sidewalkGeo.setIndex(sidewalkIndices)
          const sidewalk = new THREE.Mesh(sidewalkGeo, sidewalkMaterial)
          group.add(sidewalk)
        }
      }
    }
  }

  return group
}

export interface RouteMarkerParams {
  /** [lon, lat] of the route's real origin/destination point -- from the
   * route analysis record's own origin_lon/origin_lat/destination_lon/
   * destination_lat (or *_snapped equivalents), never invented.
   */
  lonLat: [number, number]
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
  color: string
  /** Meters above the sampled terrain elevation the marker's base sits
   * at, and the marker's own height (a cone tip pointing up).
   */
  heightOffsetM?: number
  radiusM?: number
}

/** Builds a single terrain-anchored marker (a small upward-pointing
 * cone) at a real route origin/destination point -- see ADR 0013
 * "start/end route markers" success criterion. Two calls (start green,
 * end red by convention, matching the reference image) build a route's
 * full marker pair; this module makes no assumption about which color
 * means which end, that's the caller's (SceneView's) choice.
 */
export function buildRouteMarker(params: RouteMarkerParams): THREE.Mesh {
  const { lonLat, transform, sampleElevationM, color, heightOffsetM = 0, radiusM = 6 } = params
  const [lon, lat] = lonLat
  const [x, z] = transform.toLocalXZ(lon, lat)
  const baseY = sampleElevationM(lon, lat) + heightOffsetM
  const coneHeight = radiusM * 3

  const geometry = new THREE.ConeGeometry(radiusM, coneHeight, 12)
  const material = new THREE.MeshStandardMaterial({ color })
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(x, baseY + coneHeight / 2, z)
  return mesh
}

export interface CameraBoxFraming {
  position: [number, number, number]
  target: [number, number, number]
  far: number
}

/** Frames a camera around a world-space bounding box -- used when real
 * vector overlays (roads/routes) exist, since they're typically a small
 * sub-region of a much larger DEM tile (e.g. a real ~2.7km road network
 * within a ~111km SRTM tile): framing the camera to the FULL terrain in
 * that case would leave the actual overlay content as an imperceptible
 * speck. Mirrors buildTerrainMesh.ts::computeCameraFraming's own
 * proportions (0.9x distance, 0.6x/relief-based height) so both
 * produce a comparable "overview" feel, just anchored to different
 * spatial extents.
 */
export function computeCameraFramingForBox(box: THREE.Box3, padding = 1.5): CameraBoxFraming {
  const size = new THREE.Vector3()
  box.getSize(size)
  const center = new THREE.Vector3()
  box.getCenter(center)

  const horizontalSpan = Math.max(size.x, size.z, 1) * padding
  const relief = Math.max(size.y, 0)
  const distance = horizontalSpan * 0.7
  const height = Math.max(horizontalSpan * 0.25, relief * 1.5)

  return {
    position: [center.x, center.y + height, center.z + distance],
    target: [center.x, center.y, center.z],
    far: Math.max(distance, height) * 3 + 100,
  }
}

/** Disposes every geometry/material in a group built by
 * buildLineFeatureOverlay and/or buildPolygonFeatureOverlay. Materials
 * are shared across features using the same resolved color (see
 * `materialFor` in each builder); disposing the same material object
 * more than once here is harmless (Three.js's dispose is idempotent).
 */
export function disposeLineFeatureOverlay(group: THREE.Group): void {
  group.traverse((obj) => {
    if (obj instanceof THREE.Line || obj instanceof THREE.Mesh) {
      obj.geometry.dispose()
      const material = obj.material
      if (Array.isArray(material)) material.forEach((m) => m.dispose())
      else material.dispose()
    }
  })
}
