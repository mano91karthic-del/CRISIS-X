import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { buildTerrainGeometry, computeCameraFraming, decodeGrayscaleFromRGBA } from './buildTerrainMesh'
import { datasetHeightmapPngUrl, datasetPreviewPngUrl, getDatasetHeightmapBounds } from '../../lib/api/previews'

declare global {
  interface Window {
    __scene?: THREE.Scene
    __camera?: THREE.PerspectiveCamera
  }
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
export function SceneView({ visible, demDatasetId, drapeDatasetId, exaggeration }: SceneViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const terrainMeshRef = useRef<THREE.Mesh | null>(null)
  const frameRef = useRef<number | null>(null)
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  // Always up to date, read (not depended on) by the geometry-rebuild
  // effect so a freshly-built mesh gets the CURRENT drape applied
  // immediately, without that effect re-running just because the drape
  // changed.
  const drapeDatasetIdRef = useRef(drapeDatasetId)
  drapeDatasetIdRef.current = drapeDatasetId

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

    const ambient = new THREE.AmbientLight(0xffffff, 0.7)
    const directional = new THREE.DirectionalLight(0xffffff, 0.8)
    directional.position.set(1, 1, 1)
    scene.add(ambient, directional)

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
      controls.dispose()
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

      const material = new THREE.MeshStandardMaterial({ color: '#64748b', side: THREE.DoubleSide })

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

      // Frame the camera to the terrain's OWN computed real-world size --
      // never a fixed constant, so this works for a DEM a few hundred
      // meters across or one spanning 100+ km (see the Phase 11 "3D
      // terrain invisible" bug: a hardcoded camera only happened to work
      // for a terrain coincidentally close to ~1000 world-units).
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

      // Apply whatever drape is CURRENTLY selected to this freshly-built
      // mesh -- reads the ref (not the effect's own dependency array), so
      // this never causes the geometry/camera effect itself to re-run
      // just because the drape changes.
      await applyDrapeToMesh(mesh, drapeDatasetIdRef.current)
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

  return (
    <div className={visible ? 'h-full w-full' : 'hidden'}>
      <div ref={containerRef} className="h-full w-full" data-testid="scene-3d-container" />
    </div>
  )
}
