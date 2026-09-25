import * as THREE from 'three'

export interface HeightmapPixels {
  width: number
  height: number
  /** Grayscale value per pixel, row-major, one byte per pixel (0-255).
   * Phase 11's heightmap.png is 8-bit grayscale (see ADR 0012's
   * "8-bit heightmap precision" decision) -- 256 elevation levels across
   * the raster's actual min/max, decoded from a canvas ImageData's red
   * channel (R=G=B for a true grayscale PNG once drawn to canvas).
   */
  values: Uint8ClampedArray | Uint8Array
}

/** Extracts the grayscale channel from a decoded ImageData-shaped input
 * (canvas getImageData() output has 4 bytes/pixel: R,G,B,A). Pure --
 * takes plain typed-array data, not a live canvas, so it's testable
 * without a real browser/WebGL context.
 */
export function decodeGrayscaleFromRGBA(rgba: Uint8ClampedArray | Uint8Array, width: number, height: number): HeightmapPixels {
  const values = new Uint8Array(width * height)
  for (let i = 0; i < width * height; i++) {
    values[i] = rgba[i * 4] // red channel; grayscale PNG => R === G === B
  }
  return { width, height, values }
}

export interface TerrainGeometryParams {
  pixels: HeightmapPixels
  elevationMinM: number
  elevationMaxM: number
  pixelSizeXM: number
  pixelSizeYM: number
  /** User-adjustable relief exaggeration -- disaster terrain relief is
   * often visually subtle at true scale. Default range recommended in
   * the UI slider is ~1-3x.
   */
  exaggeration: number
  /** Caps the mesh's vertex grid independent of the heightmap PNG's own
   * resolution, to bound GPU geometry cost (see ADR 0012 §20).
   */
  maxResolution?: number
}

export interface TerrainGeometryResult {
  geometry: THREE.PlaneGeometry
  worldWidthM: number
  worldHeightM: number
  gridWidth: number
  gridHeight: number
}

/** Builds a displaced PlaneGeometry from decoded heightmap pixels, in the
 * DEM's own local meters (never lon/lat -- see the "native-CRS heightmap"
 * ADR decision). Z holds elevation before any scene-level rotation;
 * SceneView rotates the resulting mesh to make Z become world "up".
 * Pure geometry math -- runs and is testable in plain Node/jsdom, no
 * WebGL context required.
 */
export function buildTerrainGeometry(params: TerrainGeometryParams): TerrainGeometryResult {
  const { pixels, elevationMinM, elevationMaxM, pixelSizeXM, pixelSizeYM, exaggeration, maxResolution = 256 } = params

  if (pixels.width < 2 || pixels.height < 2) {
    throw new Error('Heightmap must be at least 2x2 pixels to build a terrain mesh')
  }

  const stepX = Math.max(1, Math.ceil(pixels.width / maxResolution))
  const stepY = Math.max(1, Math.ceil(pixels.height / maxResolution))
  const gridWidth = Math.max(2, Math.floor(pixels.width / stepX))
  const gridHeight = Math.max(2, Math.floor(pixels.height / stepY))

  const worldWidthM = pixels.width * pixelSizeXM
  const worldHeightM = pixels.height * pixelSizeYM
  const span = elevationMaxM - elevationMinM || 1

  const geometry = new THREE.PlaneGeometry(worldWidthM, worldHeightM, gridWidth - 1, gridHeight - 1)
  const position = geometry.getAttribute('position') as THREE.BufferAttribute

  for (let row = 0; row < gridHeight; row++) {
    for (let col = 0; col < gridWidth; col++) {
      const px = Math.min(pixels.width - 1, col * stepX)
      const py = Math.min(pixels.height - 1, row * stepY)
      const value = pixels.values[py * pixels.width + px]
      const elevationM = elevationMinM + (value / 255) * span
      const vertexIndex = row * gridWidth + col
      position.setZ(vertexIndex, elevationM * exaggeration)
    }
  }
  position.needsUpdate = true
  geometry.computeVertexNormals()

  return { geometry, worldWidthM, worldHeightM, gridWidth, gridHeight }
}

export interface CameraFraming {
  /** [x, y, z] -- world-space camera position (Y is "up", matching the
   * terrain mesh's own rotated orientation). */
  position: [number, number, number]
  /** [x, y, z] -- where the camera (and OrbitControls) should look. */
  target: [number, number, number]
  /** A far-clipping-plane value guaranteed to be well beyond `position`,
   * so a very large real-world DEM (tens of km across) is never clipped
   * by a fixed, too-small `far` value.
   */
  far: number
}

/** Computes a camera position + OrbitControls target that frames a
 * terrain of ANY real-world scale -- a few hundred meters for a small
 * survey tile, or over 100km for a full SRTM tile (see the Phase 11
 * "3D terrain invisible" bug: a hardcoded, scale-independent camera
 * position only happened to work for terrains coincidentally close to
 * ~1000 world-units across). Distance/height scale with the terrain's
 * own horizontal footprint and elevation range, not a fixed constant, so
 * the same formula frames a 10m plot and a 110km SRTM tile equally well.
 * Pure -- takes the already-computed geometry dimensions, not a live
 * Three.js object, so it's testable without a WebGL context.
 */
export function computeCameraFraming(params: {
  worldWidthM: number
  worldHeightM: number
  elevationMinM: number
  elevationMaxM: number
  exaggeration: number
}): CameraFraming {
  const { worldWidthM, worldHeightM, elevationMinM, elevationMaxM, exaggeration } = params

  const horizontalSpan = Math.max(worldWidthM, worldHeightM, 1)
  const elevationSpanM = Math.max(elevationMaxM - elevationMinM, 0)
  const exaggeratedElevationSpan = Math.max(elevationSpanM * exaggeration, horizontalSpan * 0.001)
  const centerElevation = ((elevationMinM + elevationMaxM) / 2) * exaggeration

  // Distance/height proportional to the terrain's own footprint -- ~0.9x
  // the span gives a comfortable overview angle under OrbitControls'
  // default vertical FOV; height is the larger of a fraction of the
  // horizontal span or twice the (exaggerated) elevation relief, so a
  // very flat-but-wide DEM still gets an overhead vantage point instead
  // of an edge-on sliver view.
  const distance = horizontalSpan * 0.9
  const height = Math.max(horizontalSpan * 0.6, exaggeratedElevationSpan * 2)

  return {
    position: [0, centerElevation + height, distance],
    target: [0, centerElevation, 0],
    far: Math.max(distance, height) * 3 + 100,
  }
}
