import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { buildTerrainGeometry, computeCameraFraming, decodeGrayscaleFromRGBA } from './buildTerrainMesh'
import {
  buildPolygonFeatureOverlay,
  buildRoadSurfaceOverlay,
  buildRibbonFeatureOverlay,
  buildRouteMarker,
  computeCameraFramingForBox,
  createElevationSampler,
  createTerrainLocalTransform,
  createProjectedTerrainLocalTransform,
  disposeLineFeatureOverlay,
  filterFeaturesByBbox,
  isGeographicNativeCrs,
  isSupportedTerrainCrs,
  FALLBACK_BUILDING_HEIGHT_M,
  ROUTE_WIDTH_M,
  type OverlayFeatureUserData,
  type TerrainLocalTransform,
} from './terrainOverlay'
import { datasetHeightmapPngUrl, datasetPreviewPngUrl, getDatasetGeoJson, getDatasetHeightmapBounds } from '../../lib/api/previews'

declare global {
  interface Window {
    __scene?: THREE.Scene
    __camera?: THREE.PerspectiveCamera
  }
}

export interface TerrainVectorOverlay {
  datasetId: string
  /** The layer's dataset_type (e.g. `roads`, `route_hazard_aware`,
   * `risk_classification`) -- decides ribbon width (route vs. road
   * class-based) independent of the fetched GeoJSON's own geometry type,
   * which only decides ribbon-vs-extrusion (see rebuildVectorOverlays).
   */
  datasetType: string
  /** Fixed/fallback display color -- matches the SAME color the 2D map
   * already uses for this layer (see
   * map2d/layers/datasetTypeClassification.ts) so a road/route reads as
   * the same thing in both views.
   */
  color: string
  /** GeoJSON feature property to color by instead of the flat `color`
   * (e.g. `risk_class` for risk_classification, `hazard_class_label`
   * for exposure_features) -- same shared legend the 2D map uses. Omit
   * for a flat-colored layer (roads, routes).
   */
  colorProperty?: string
}

/** A route analysis's real origin/destination points -- from the route
 * analysis record's own origin_lon/origin_lat/destination_lon/
 * destination_lat (see types/routing.ts::RouteAnalysis), never invented.
 * Rendered as a start (green) / end (red) marker pair when the route's
 * own layer(s) are visible.
 */
export interface RouteEndpoints {
  origin: [number, number]
  destination: [number, number]
}

export interface SceneViewProps {
  visible: boolean
  /** DEM/DSM dataset id to build the terrain surface from -- null when
   * no terrain layer is registered/visible (an explicit empty state is
   * rendered by the parent dashboard, not by SceneView itself).
   */
  demDatasetId: string | null
  /** Optional hazard/slope/etc. raster dataset id, draped onto the
   * terrain via the SAME colorized preview.png the 2D map uses --
   * guarantees 2D/3D color agreement (see ADR 0012 §11).
   */
  drapeDatasetId: string | null
  exaggeration: number
  /** Vector datasets draped onto the terrain surface -- roads/routes as
   * width-aware ribbons, buildings (risk/exposure-classified) as
   * extruded masses (see terrainOverlay.ts). Only takes effect when the
   * terrain DEM's native CRS is geographic (see isGeographicNativeCrs)
   * -- silently skipped, never wrongly placed, for a projected-CRS DEM.
   */
  vectorOverlays?: TerrainVectorOverlay[]
  /** A polygon overlay's features are kept only if they intersect this
   * WGS84 bbox before rendering -- see terrainOverlay.ts::filterFeaturesByBbox.
   * Exists because a building dataset's own full extent can be far
   * larger than the specific area the rest of the scene (roads/route)
   * actually covers (see ADR 0013 IMPORTANT DATA RULE); pass the roads/
   * route layers' own bbox, not the building dataset's. Omit (or null)
   * to render every fetched building feature unfiltered.
   */
  buildingClipBbox?: { west: number; south: number; east: number; north: number } | null
  /** The canonical study-area bounding box in WGS84 -- used to frame the
   * 3D camera to the study area instead of placing the camera 100km away
   * at full DEM tile scale.
   */
  aoiBbox?: { west: number; south: number; east: number; north: number } | null
  /** Real route-analysis origin/destination points to mark -- see
   * RouteEndpoints. Rendered independent of which specific route layer
   * checkboxes are on, since the origin/destination belong to the
   * analysis, not to route_shortest or route_hazard_aware individually.
   */
  routeEndpoints?: RouteEndpoints[]
  /** Called when the user clicks a rendered overlay feature (building,
   * road, route). Wire to the SAME `activeFeature` store the 2D map's
   * click handler populates (see map2d/layers/useVectorLayer.ts) so the
   * feature inspector panel and Assistant context work identically
   * regardless of which view the click came from.
   */
  onFeatureSelect?: (feature: OverlayFeatureUserData) => void
}

function loadImageElement(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`Failed to load image: ${url}`))
    img.src = url
  })
}

function imageToImageData(img: HTMLImageElement): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('2D canvas context unavailable')
  ctx.drawImage(img, 0, 0)
  return ctx.getImageData(0, 0, canvas.width, canvas.height)
}

/** Loads (or clears) the drape texture on whichever mesh is passed in --
 * shared by both the geometry-rebuild effect (which must apply the
 * CURRENT drape to a freshly-built mesh so a first load never silently
 * drops it) and the drape-only effect (which reuses this same logic when
 * just the overlay changes, without touching geometry or camera).
 */
async function applyDrapeToMesh(mesh: THREE.Mesh, drapeDatasetId: string | null): Promise<void> {
  const material = mesh.material as THREE.MeshStandardMaterial
  if (!drapeDatasetId) {
    if (material.map) {
      material.map = null
      material.color.set('#64748b')
      material.needsUpdate = true
    }
    return
  }
  const drapeImg = await loadImageElement(datasetPreviewPngUrl(drapeDatasetId))
  const texture = new THREE.Texture(drapeImg)
  texture.needsUpdate = true
  material.map = texture
  material.color.set('#ffffff')
  material.needsUpdate = true
}

interface TerrainSampler {
  transform: TerrainLocalTransform
  sampleElevationM: (lon: number, lat: number) => number
}

/** Rebuilds the vector-overlay group (roads/routes draped on terrain)
 * from scratch -- shared by the geometry-rebuild effect (applying
 * whatever overlays are CURRENTLY selected to a freshly-built terrain)
 * and the overlay-only effect (reacting to a layer-visibility toggle
 * without touching terrain geometry -- see reframeCameraToOverlaysIfPresent
 * for the one exception, camera reframing, which callers apply
 * separately). Old group contents are always disposed before new ones
 * are added, mirroring applyDrapeToMesh's replace-not-accumulate
 * discipline.
 */
async function rebuildVectorOverlays(
  scene: THREE.Scene,
  overlayGroupRef: React.MutableRefObject<THREE.Group | null>,
  overlays: TerrainVectorOverlay[],
  sampler: TerrainSampler | null,
  tokenRef: React.MutableRefObject<number>,
  token: number,
  buildingClipBbox: { west: number; south: number; east: number; north: number } | null,
  routeEndpoints: RouteEndpoints[],
): Promise<void> {
  if (!sampler || (overlays.length === 0 && routeEndpoints.length === 0)) {
    // Stale relative to a newer rebuild that's since started -- that
    // call owns overlayGroupRef now; don't touch it.
    if (tokenRef.current !== token) return
    if (overlayGroupRef.current) {
      scene.remove(overlayGroupRef.current)
      disposeLineFeatureOverlay(overlayGroupRef.current)
      overlayGroupRef.current = null
    }
    return
  }

  const group = new THREE.Group()
  for (const overlay of overlays) {
    const geojson = await getDatasetGeoJson(overlay.datasetId, { limit: 50000 })
    // Dispatch by the fetched GeoJSON's own geometry type -- the same
    // dataset_type (risk_classification/exposure_features) can be
    // either lines (roads as the exposure asset) or polygons (buildings
    // as the exposure asset), so this can't be decided from
    // dataset_type alone. Mirrors useVectorLayer.ts's own
    // `data.features[0]?.geometry?.type` dispatch in the 2D map.
    const firstGeometryType = geojson.features.find((f) => f.geometry)?.geometry?.type
    const isPolygon = firstGeometryType === 'Polygon' || firstGeometryType === 'MultiPolygon'

    if (isPolygon) {
      // Check if this is a road surface polygon (not a building)
      const isRoadSurface = overlay.datasetType === 'roads' && overlay.datasetId === 'd6cf7473-084c-4fcc-9599-4c588749729b'

      if (isRoadSurface) {
        // Road surfaces: flat asphalt meshes (not extruded buildings)
        const featureGroup = buildRoadSurfaceOverlay({
          geojson,
          transform: sampler.transform,
          sampleElevationM: sampler.sampleElevationM,
          datasetId: overlay.datasetId,
          color: '#2C2C2C', // asphalt
          heightOffsetM: 0.15,
        })
        group.add(featureGroup)
      } else {
        // Buildings: clip to the study area before extruding
        const features = buildingClipBbox ? filterFeaturesByBbox(geojson.features, buildingClipBbox) : geojson.features
        const featureGroup = buildPolygonFeatureOverlay({
          geojson: { features },
          transform: sampler.transform,
          sampleElevationM: sampler.sampleElevationM,
          datasetId: overlay.datasetId,
          color: overlay.color,
          colorProperty: overlay.colorProperty,
          heightM: FALLBACK_BUILDING_HEIGHT_M,
          heightProperty: 'height_m',
          heightSourceLabel: 'synthetic_dataset',
          buildingColorProperty: 'building_color',
        })
        group.add(featureGroup)
      }
    } else {
      // Roads/routes: a route always uses the fixed, wider ROUTE_WIDTH_M
      // so a highlighted path reads as visually prominent over the road
      // network beneath it; a plain road (or a road-sourced risk/
      // exposure layer, which no longer carries the original `highway`
      // tag) resolves its own width per-feature from `highway` via
      // ROAD_WIDTH_BY_HIGHWAY_CLASS_M, falling back to DEFAULT_ROAD_WIDTH_M.
      const isRoute = overlay.datasetType.startsWith('route_')
      const featureGroup = buildRibbonFeatureOverlay({
        geojson,
        transform: sampler.transform,
        sampleElevationM: sampler.sampleElevationM,
        datasetId: overlay.datasetId,
        color: overlay.color,
        colorProperty: overlay.colorProperty,
        widthM: isRoute ? ROUTE_WIDTH_M : undefined,
        widthProperty: isRoute ? undefined : (overlay.datasetType === 'roads' ? 'road_type' : 'highway'),
        heightOffsetM: isRoute ? 2.5 : 1, // routes sit visibly above the road ribbon beneath them
      })
      group.add(featureGroup)
    }
  }

  for (const endpoints of routeEndpoints) {
    const startMarker = buildRouteMarker({
      lonLat: endpoints.origin,
      transform: sampler.transform,
      sampleElevationM: sampler.sampleElevationM,
      color: '#22c55e', // green -- start, matches the "safe/shelter" color family used elsewhere
      heightOffsetM: 3,
    })
    startMarker.userData = { datasetId: 'route-endpoint', featureId: null, properties: { role: 'origin', lon: endpoints.origin[0], lat: endpoints.origin[1] } }
    const endMarker = buildRouteMarker({
      lonLat: endpoints.destination,
      transform: sampler.transform,
      sampleElevationM: sampler.sampleElevationM,
      color: '#ef4444', // red -- destination, matches the "blocked/critical" color used for route_blocked_segments
      heightOffsetM: 3,
    })
    endMarker.userData = { datasetId: 'route-endpoint', featureId: null, properties: { role: 'destination', lon: endpoints.destination[0], lat: endpoints.destination[1] } }
    group.add(startMarker, endMarker)
  }

  // Two overlapping rebuilds can be in flight at once (e.g. a user
  // toggling several checkboxes before the first fetch resolves) --
  // each awaits its own independent set of GeoJSON fetches, so they can
  // resolve out of order. Without this guard, an older call resolving
  // last would clobber a newer, more-current result (or double-add a
  // group without removing the newer one), leaving the scene showing
  // stale/partial overlays. Only the call whose token is still the
  // latest is allowed to touch the scene/ref; a stale result is
  // disposed instead, matching the old-group-disposal discipline below.
  if (tokenRef.current !== token) {
    disposeLineFeatureOverlay(group)
    return
  }
  if (overlayGroupRef.current) {
    scene.remove(overlayGroupRef.current)
    disposeLineFeatureOverlay(overlayGroupRef.current)
  }
  scene.add(group)
  overlayGroupRef.current = group
}

function computeAoiLocalBox(
  aoiBbox: { west: number; south: number; east: number; north: number },
  sampler: TerrainSampler,
): THREE.Box3 {
  const [xMin, zMax] = sampler.transform.toLocalXZ(aoiBbox.west, aoiBbox.south)
  const [xMax, zMin] = sampler.transform.toLocalXZ(aoiBbox.east, aoiBbox.north)
  const y1 = sampler.sampleElevationM(aoiBbox.west, aoiBbox.south)
  const y2 = sampler.sampleElevationM(aoiBbox.east, aoiBbox.north)
  const yCenter = sampler.sampleElevationM((aoiBbox.west + aoiBbox.east) / 2, (aoiBbox.south + aoiBbox.north) / 2)
  const yMin = Math.min(y1, y2, yCenter)
  const yMax = Math.max(y1, y2, yCenter) + 20
  return new THREE.Box3(
    new THREE.Vector3(Math.min(xMin, xMax), yMin, Math.min(zMin, zMax)),
    new THREE.Vector3(Math.max(xMin, xMax), yMax, Math.max(zMin, zMax)),
  )
}

/** Reframes the camera/controls to fit whatever vector-overlay content
 * currently exists -- called after EVERY overlay rebuild (both the
 * initial terrain build and a later layer-visibility toggle), unlike
 * the drape texture's "never touch the camera" rule: a road/route is
 * typically a small sub-region of a much larger DEM tile, so a user
 * turning one on has no way to actually see it at the terrain's own
 * full-tile framing otherwise. If no overlay is visible, falls back to
 * framing the canonical AOI box rather than full 110km terrain.
 */
function reframeCameraToOverlaysIfPresent(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  overlayGroup: THREE.Group | null,
  fallbackAoiBbox?: { west: number; south: number; east: number; north: number } | null,
  sampler?: TerrainSampler | null,
): void {
  if (overlayGroup && overlayGroup.children.length > 0) {
    const box = new THREE.Box3().setFromObject(overlayGroup)
    if (Number.isFinite(box.min.x) && Number.isFinite(box.max.x)) {
      const framing = computeCameraFramingForBox(box)
      camera.position.set(...framing.position)
      camera.far = framing.far
      camera.updateProjectionMatrix()
      controls.target.set(...framing.target)
      controls.update()
      return
    }
  }
  if (fallbackAoiBbox && sampler) {
    const aoiBox = computeAoiLocalBox(fallbackAoiBbox, sampler)
    const framing = computeCameraFramingForBox(aoiBox, 1.6)
    camera.position.set(...framing.position)
    camera.far = framing.far
    camera.updateProjectionMatrix()
    controls.target.set(...framing.target)
    controls.update()
  }
}

/** Owns the Three.js renderer/scene/camera/controls lifecycle,
 * independent of MapView's MapLibre instance (see ADR 0012 §12 -- two
 * synced views, not a merged canvas). Terrain is built from the DEM's
 * native-CRS heightmap (never reprojected -- see buildTerrainMesh.ts),
 * so the scene has no geographic camera; overlays are aligned in the
 * same local-meter space.
 *
 * Camera framing (see buildTerrainMesh.ts::computeCameraFraming) is
 * recomputed only when the terrain's actual geometry changes (the DEM
 * dataset or the exaggeration factor) -- never when only the drape
 * overlay changes, and never on an unrelated re-render, so a user's
 * manual orbit/zoom is never silently reset by toggling a hazard layer.
 */
export function SceneView({
  visible,
  demDatasetId,
  drapeDatasetId,
  exaggeration,
  vectorOverlays = [],
  buildingClipBbox = null,
  aoiBbox = null,
  routeEndpoints = [],
  onFeatureSelect,
}: SceneViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const terrainMeshRef = useRef<THREE.Mesh | null>(null)
  const overlayGroupRef = useRef<THREE.Group | null>(null)
  // Bumped by every call site that invokes rebuildVectorOverlays, so
  // overlapping async rebuilds (geometry-rebuild effect + overlay-only
  // effect, or two rapid overlay-only rebuilds) can tell whether they're
  // still the most recent request before mutating the scene -- see
  // rebuildVectorOverlays's own comment.
  const overlayRebuildTokenRef = useRef(0)
  const terrainSamplerRef = useRef<TerrainSampler | null>(null)
  const frameRef = useRef<number | null>(null)
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  // Always up to date, read (not depended on) by the geometry-rebuild
  // effect so a freshly-built mesh gets the CURRENT drape applied
  // immediately, without that effect re-running just because the drape
  // changed.
  const drapeDatasetIdRef = useRef(drapeDatasetId)
  drapeDatasetIdRef.current = drapeDatasetId
  // Same pattern for vector overlays -- see the overlay-only effect
  // below for why its dependency is a stable string key, not this array.
  const vectorOverlaysRef = useRef(vectorOverlays)
  vectorOverlaysRef.current = vectorOverlays
  const buildingClipBboxRef = useRef(buildingClipBbox)
  buildingClipBboxRef.current = buildingClipBbox
  const aoiBboxRef = useRef(aoiBbox)
  aoiBboxRef.current = aoiBbox
  const routeEndpointsRef = useRef(routeEndpoints)
  routeEndpointsRef.current = routeEndpoints
  const onFeatureSelectRef = useRef(onFeatureSelect)
  onFeatureSelectRef.current = onFeatureSelect
  const vectorOverlaysKey = vectorOverlays.map((o) => `${o.datasetId}:${o.datasetType}:${o.color}:${o.colorProperty ?? ''}`).join(',')
  const buildingClipBboxKey = buildingClipBbox ? `${buildingClipBbox.west}:${buildingClipBbox.south}:${buildingClipBbox.east}:${buildingClipBbox.north}` : ''
  const aoiBboxKey = aoiBbox ? `${aoiBbox.west}:${aoiBbox.south}:${aoiBbox.east}:${aoiBbox.north}` : ''
  const routeEndpointsKey = routeEndpoints.map((e) => `${e.origin.join(',')}|${e.destination.join(',')}`).join(';')

  // --- renderer/scene/camera lifecycle: created once, disposed on unmount ---
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#0f172a')
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100000)
    camera.position.set(0, 500, 800)

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    container.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true

    const ambient = new THREE.AmbientLight(0xffffff, 0.5)
    const directional = new THREE.DirectionalLight(0xffffff, 1.0)
    directional.position.set(1, 1, 1)
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.3)
    fillLight.position.set(-1, 0.5, -1)
    scene.add(ambient, directional, fillLight)

    sceneRef.current = scene
    cameraRef.current = camera
    rendererRef.current = renderer
    controlsRef.current = controls

    // Only for the Playwright smoke spec's scene/camera assertions --
    // opt-in via env, never present unless explicitly built for E2E (same
    // flag MapView.tsx uses for its window.__map hook).
    if (import.meta.env.VITE_EXPOSE_MAP_FOR_TESTS === 'true') {
      window.__scene = scene
      window.__camera = camera
    }

    const resize = () => {
      const width = container.clientWidth || 1
      const height = container.clientHeight || 1
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
    }
    resize()
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(container)

    // Click-to-select: raycast against the CURRENT overlay group only
    // (never the terrain mesh itself, which carries no feature data) --
    // populates the SAME activeFeature shape the 2D map's click handler
    // uses (see map2d/layers/useVectorLayer.ts), via onFeatureSelect, so
    // the feature inspector panel and Assistant context work
    // identically from either view. A drag-to-orbit gesture is not a
    // click: only a pointerup that lands within a few pixels of its own
    // pointerdown counts, matching how OrbitControls itself distinguishes
    // "click" from "drag" (it doesn't preventDefault on click, so this
    // listener always sees both).
    const raycaster = new THREE.Raycaster()
    const pointerNdc = new THREE.Vector2()
    let pointerDownPos: { x: number; y: number } | null = null
    const handlePointerDown = (event: PointerEvent) => {
      pointerDownPos = { x: event.clientX, y: event.clientY }
    }
    const handlePointerUp = (event: PointerEvent) => {
      if (!onFeatureSelectRef.current || !overlayGroupRef.current) return
      if (!pointerDownPos) return
      const dx = event.clientX - pointerDownPos.x
      const dy = event.clientY - pointerDownPos.y
      pointerDownPos = null
      if (Math.hypot(dx, dy) > 5) return // treat as a drag/orbit, not a click

      const rect = renderer.domElement.getBoundingClientRect()
      pointerNdc.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
      pointerNdc.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointerNdc, camera)
      const hits = raycaster.intersectObject(overlayGroupRef.current, true)
      const hit = hits.find((h) => h.object.userData && 'datasetId' in h.object.userData)
      if (hit) onFeatureSelectRef.current(hit.object.userData as OverlayFeatureUserData)
    }
    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)

    const animate = () => {
      frameRef.current = requestAnimationFrame(animate)
      if (!visibleRef.current) return
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      resizeObserver.disconnect()
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
      renderer.domElement.removeEventListener('pointerup', handlePointerUp)
      controls.dispose()
      if (overlayGroupRef.current) {
        disposeLineFeatureOverlay(overlayGroupRef.current)
        overlayGroupRef.current = null
      }
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose()
          const material = obj.material
          if (Array.isArray(material)) material.forEach((m) => m.dispose())
          else material.dispose()
        }
      })
      renderer.dispose()
      if (renderer.domElement.parentElement === container) container.removeChild(renderer.domElement)
    }
  }, [])

  // --- terrain geometry + camera refit: only when the DEM or exaggeration changes ---
  useEffect(() => {
    let cancelled = false
    const scene = sceneRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!scene || !camera || !controls || !demDatasetId) return
    const activeScene: THREE.Scene = scene
    const activeCamera: THREE.PerspectiveCamera = camera
    const activeControls: OrbitControls = controls

    async function build() {
      const bounds = await getDatasetHeightmapBounds(demDatasetId!)
      if (bounds.elevation_min_m === null || bounds.elevation_max_m === null || bounds.pixel_size_x_m === null) {
        throw new Error('Heightmap bounds are missing required elevation/pixel-size fields')
      }
      const heightmapImg = await loadImageElement(datasetHeightmapPngUrl(demDatasetId!))
      const imageData = imageToImageData(heightmapImg)
      const pixels = decodeGrayscaleFromRGBA(imageData.data, imageData.width, imageData.height)

      const { geometry, worldWidthM, worldHeightM } = buildTerrainGeometry({
        pixels,
        elevationMinM: bounds.elevation_min_m!,
        elevationMaxM: bounds.elevation_max_m!,
        pixelSizeXM: bounds.pixel_size_x_m!,
        pixelSizeYM: bounds.pixel_size_y_m ?? bounds.pixel_size_x_m!,
        exaggeration,
      })

      if (cancelled) return

      const material = new THREE.MeshStandardMaterial({ color: '#475569', side: THREE.DoubleSide })

      if (terrainMeshRef.current) {
        activeScene.remove(terrainMeshRef.current)
        terrainMeshRef.current.geometry.dispose()
        const oldMaterial = terrainMeshRef.current.material
        if (Array.isArray(oldMaterial)) oldMaterial.forEach((m) => m.dispose())
        else oldMaterial.dispose()
      }

      const mesh = new THREE.Mesh(geometry, material)
      mesh.rotation.x = -Math.PI / 2 // plane's local Z (elevation) becomes world Y (up)
      activeScene.add(mesh)
      terrainMeshRef.current = mesh

      // Establish (or clear) this terrain build's local coordinate
      // sampler for vector overlays -- see terrainOverlay.ts. Supports
      // both geographic (WGS84) and projected (e.g. UTM) CRS data.
      terrainSamplerRef.current = isSupportedTerrainCrs(bounds.native_crs)
        ? {
            transform: isGeographicNativeCrs(bounds.native_crs)
              ? createTerrainLocalTransform({
                  boundsWestLon: bounds.west,
                  boundsSouthLat: bounds.south,
                  boundsEastLon: bounds.east,
                  boundsNorthLat: bounds.north,
                  worldWidthM,
                  worldHeightM,
                })
              : createProjectedTerrainLocalTransform({
                  boundsWest: bounds.west,
                  boundsSouth: bounds.south,
                  boundsEast: bounds.east,
                  boundsNorth: bounds.north,
                  worldWidthM,
                  worldHeightM,
                }),
            sampleElevationM: createElevationSampler({
              pixels,
              elevationMinM: bounds.elevation_min_m!,
              elevationMaxM: bounds.elevation_max_m!,
              exaggeration,
              boundsWestLon: bounds.west,
              boundsSouthLat: bounds.south,
              boundsEastLon: bounds.east,
              boundsNorthLat: bounds.north,
            }),
          }
        : null

      // Frame camera: frame to canonical AOI if known, otherwise full terrain
      if (aoiBboxRef.current && terrainSamplerRef.current) {
        const aoiBox = computeAoiLocalBox(aoiBboxRef.current, terrainSamplerRef.current)
        const framing = computeCameraFramingForBox(aoiBox, 1.6)
        activeCamera.position.set(...framing.position)
        activeCamera.far = framing.far
        activeCamera.updateProjectionMatrix()
        activeControls.target.set(...framing.target)
        activeControls.update()
      } else {
        const framing = computeCameraFraming({
          worldWidthM,
          worldHeightM,
          elevationMinM: bounds.elevation_min_m!,
          elevationMaxM: bounds.elevation_max_m!,
          exaggeration,
        })
        activeCamera.position.set(...framing.position)
        activeCamera.far = framing.far
        activeCamera.updateProjectionMatrix()
        activeControls.target.set(...framing.target)
        activeControls.update()
      }

      // Apply whatever drape is CURRENTLY selected to this freshly-built
      // mesh -- reads the ref (not the effect's own dependency array), so
      // this never causes the geometry/camera effect itself to re-run
      // just because the drape changes.
      await applyDrapeToMesh(mesh, drapeDatasetIdRef.current)

      if (cancelled) return
      // Apply whatever overlays are CURRENTLY selected -- reads the ref
      // (not this effect's dependency array), matching the drape's own
      // "freshly-built mesh gets current selection" discipline.
      overlayRebuildTokenRef.current += 1
      const overlayToken = overlayRebuildTokenRef.current
      await rebuildVectorOverlays(
        activeScene,
        overlayGroupRef,
        vectorOverlaysRef.current,
        terrainSamplerRef.current,
        overlayRebuildTokenRef,
        overlayToken,
        buildingClipBboxRef.current,
        routeEndpointsRef.current,
      )
      if (cancelled || overlayRebuildTokenRef.current !== overlayToken) return

      // If any overlay was already selected at this initial build, frame
      // to it; otherwise fallback to the canonical AOI:
      reframeCameraToOverlaysIfPresent(activeCamera, activeControls, overlayGroupRef.current, aoiBboxRef.current, terrainSamplerRef.current)
    }

    build().catch((err) => {
      // Fail visibly-in-console, not silently -- a missing/invalid
      // terrain dataset is a real data problem, never papered over.
      // eslint-disable-next-line no-console
      console.error('Failed to build 3D terrain:', err)
    })

    return () => {
      cancelled = true
    }
  }, [demDatasetId, exaggeration])

  // --- drape texture only: never touches geometry or camera ---
  useEffect(() => {
    if (!terrainMeshRef.current) return // no mesh yet -- the geometry effect above applies the initial drape itself
    let cancelled = false
    const mesh = terrainMeshRef.current

    applyDrapeToMesh(mesh, drapeDatasetId).catch((err) => {
      if (cancelled) return
      // eslint-disable-next-line no-console
      console.error('Failed to apply 3D drape texture:', err)
    })

    return () => {
      cancelled = true
    }
  }, [drapeDatasetId])

  // --- vector overlays only: never touches terrain geometry ---
  // Dependency is a stable string key, not the `vectorOverlays` array
  // itself -- DashboardPage recomputes that array on every render, and
  // reacting to referential inequality would rebuild the overlay group
  // (an async GeoJSON refetch) on every unrelated re-render. Unlike the
  // drape effect above, this DOES reframe the camera when overlay
  // content newly appears (see reframeCameraToOverlaysIfPresent) --
  // without it, enabling a road/route layer on a huge DEM tile would
  // have no visible effect at all.
  useEffect(() => {
    const scene = sceneRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    // No terrain built yet (or a projected-CRS DEM) -- the geometry
    // effect above will apply the initial selection itself once a
    // sampler exists; nothing to do here yet.
    if (!scene || !camera || !controls || !terrainSamplerRef.current) return
    let cancelled = false
    overlayRebuildTokenRef.current += 1
    const overlayToken = overlayRebuildTokenRef.current

    rebuildVectorOverlays(
      scene,
      overlayGroupRef,
      vectorOverlaysRef.current,
      terrainSamplerRef.current,
      overlayRebuildTokenRef,
      overlayToken,
      buildingClipBboxRef.current,
      routeEndpointsRef.current,
    )
      .then(() => {
        if (cancelled || overlayRebuildTokenRef.current !== overlayToken) return
        reframeCameraToOverlaysIfPresent(camera, controls, overlayGroupRef.current, aoiBboxRef.current, terrainSamplerRef.current)
      })
      .catch((err) => {
        if (cancelled) return
        // eslint-disable-next-line no-console
        console.error('Failed to apply 3D vector overlays:', err)
      })

    return () => {
      cancelled = true
    }
  }, [vectorOverlaysKey, buildingClipBboxKey, aoiBboxKey, routeEndpointsKey])

  return (
    <div className={visible ? 'h-full w-full' : 'hidden'}>
      <div ref={containerRef} className="h-full w-full" data-testid="scene-3d-container" />
    </div>
  )
}
