import { describe, expect, it } from 'vitest'
import { buildTerrainGeometry, computeCameraFraming, decodeGrayscaleFromRGBA } from './buildTerrainMesh'

describe('decodeGrayscaleFromRGBA', () => {
  it('extracts the red channel from a 2x1 RGBA buffer', () => {
    const rgba = new Uint8ClampedArray([10, 10, 10, 255, 200, 200, 200, 255])
    const result = decodeGrayscaleFromRGBA(rgba, 2, 1)
    expect(Array.from(result.values)).toEqual([10, 200])
    expect(result.width).toBe(2)
    expect(result.height).toBe(1)
  })
})

describe('buildTerrainGeometry', () => {
  const flatPixels = { width: 4, height: 4, values: new Uint8Array(16).fill(128) }

  it('produces a geometry sized in local meters from pixelSize', () => {
    const result = buildTerrainGeometry({
      pixels: flatPixels,
      elevationMinM: 0,
      elevationMaxM: 100,
      pixelSizeXM: 10,
      pixelSizeYM: 10,
      exaggeration: 1,
    })
    expect(result.worldWidthM).toBe(40)
    expect(result.worldHeightM).toBe(40)
  })

  it('displaces vertices between elevationMinM and elevationMaxM', () => {
    const values = new Uint8Array(16)
    values[0] = 0 // -> elevationMinM
    values[15] = 255 // -> elevationMaxM
    const result = buildTerrainGeometry({
      pixels: { width: 4, height: 4, values },
      elevationMinM: 10,
      elevationMaxM: 50,
      pixelSizeXM: 1,
      pixelSizeYM: 1,
      exaggeration: 1,
      maxResolution: 4,
    })
    const position = result.geometry.getAttribute('position')
    const zValues: number[] = []
    for (let i = 0; i < position.count; i++) zValues.push(position.getZ(i))
    expect(Math.min(...zValues)).toBeCloseTo(10, 5)
    expect(Math.max(...zValues)).toBeCloseTo(50, 5)
  })

  it('applies the exaggeration factor multiplicatively to elevation', () => {
    const values = new Uint8Array(16).fill(255) // all at elevationMaxM
    const base = buildTerrainGeometry({
      pixels: { width: 4, height: 4, values },
      elevationMinM: 0,
      elevationMaxM: 100,
      pixelSizeXM: 1,
      pixelSizeYM: 1,
      exaggeration: 1,
    })
    const exaggerated = buildTerrainGeometry({
      pixels: { width: 4, height: 4, values },
      elevationMinM: 0,
      elevationMaxM: 100,
      pixelSizeXM: 1,
      pixelSizeYM: 1,
      exaggeration: 3,
    })
    const baseZ = base.geometry.getAttribute('position').getZ(0)
    const exaggeratedZ = exaggerated.geometry.getAttribute('position').getZ(0)
    expect(exaggeratedZ).toBeCloseTo(baseZ * 3, 5)
  })

  it('caps the mesh resolution independent of the source pixel grid', () => {
    const bigPixels = { width: 1000, height: 1000, values: new Uint8Array(1_000_000).fill(0) }
    const result = buildTerrainGeometry({
      pixels: bigPixels,
      elevationMinM: 0,
      elevationMaxM: 10,
      pixelSizeXM: 1,
      pixelSizeYM: 1,
      exaggeration: 1,
      maxResolution: 64,
    })
    expect(result.gridWidth).toBeLessThanOrEqual(64)
    expect(result.gridHeight).toBeLessThanOrEqual(64)
  })

  it('rejects a heightmap smaller than 2x2', () => {
    expect(() =>
      buildTerrainGeometry({
        pixels: { width: 1, height: 1, values: new Uint8Array([1]) },
        elevationMinM: 0,
        elevationMaxM: 10,
        pixelSizeXM: 1,
        pixelSizeYM: 1,
        exaggeration: 1,
      }),
    ).toThrow()
  })
})

// Phase 11 "3D terrain invisible" bug fix: the camera must frame a
// terrain regardless of its real-world scale, not assume a fixed
// ~1000-world-unit size (see docs/architecture/0012-phase-11-command-dashboard.md).
describe('computeCameraFraming', () => {
  it('positions the camera proportionally further away for a larger terrain', () => {
    const small = computeCameraFraming({
      worldWidthM: 100,
      worldHeightM: 100,
      elevationMinM: 0,
      elevationMaxM: 10,
      exaggeration: 1,
    })
    const large = computeCameraFraming({
      worldWidthM: 108_000, // real Chennai SRTM-tile scale
      worldHeightM: 108_000,
      elevationMinM: -26,
      elevationMaxM: 76,
      exaggeration: 1,
    })

    const smallDistance = Math.hypot(small.position[0], small.position[1], small.position[2])
    const largeDistance = Math.hypot(large.position[0], large.position[1], large.position[2])

    expect(largeDistance).toBeGreaterThan(smallDistance * 100)
  })

  it('never produces a degenerate (NaN/zero) camera position for a large real-world DEM', () => {
    const framing = computeCameraFraming({
      worldWidthM: 108_293,
      worldHeightM: 110_680,
      elevationMinM: -26,
      elevationMaxM: 76,
      exaggeration: 1.5,
    })

    for (const v of [...framing.position, ...framing.target]) {
      expect(Number.isFinite(v)).toBe(true)
    }
    expect(Math.hypot(...framing.position)).toBeGreaterThan(0)
  })

  it('keeps the far clipping plane well beyond the camera distance, for any scale', () => {
    for (const worldSize of [10, 1_000, 108_000]) {
      const framing = computeCameraFraming({
        worldWidthM: worldSize,
        worldHeightM: worldSize,
        elevationMinM: 0,
        elevationMaxM: worldSize * 0.01,
        exaggeration: 1,
      })
      const cameraDistanceFromTarget = Math.hypot(
        framing.position[0] - framing.target[0],
        framing.position[1] - framing.target[1],
        framing.position[2] - framing.target[2],
      )
      expect(framing.far).toBeGreaterThan(cameraDistanceFromTarget)
    }
  })

  it('targets the terrain at its horizontal center, tracking its mid-elevation', () => {
    const framing = computeCameraFraming({
      worldWidthM: 1000,
      worldHeightM: 1000,
      elevationMinM: 100,
      elevationMaxM: 300,
      exaggeration: 2,
    })
    expect(framing.target[0]).toBe(0)
    expect(framing.target[2]).toBe(0)
    // mid-elevation (200) * exaggeration (2) = 400
    expect(framing.target[1]).toBeCloseTo(400, 5)
  })

  it('raises the camera higher when exaggeration increases relief, for the same footprint', () => {
    const base = { worldWidthM: 500, worldHeightM: 500, elevationMinM: 0, elevationMaxM: 400 }
    const low = computeCameraFraming({ ...base, exaggeration: 1 })
    const high = computeCameraFraming({ ...base, exaggeration: 5 })
    expect(high.position[1]).toBeGreaterThan(low.position[1])
  })

  it('does not collapse to a zero-size frame for a perfectly flat terrain', () => {
    const framing = computeCameraFraming({
      worldWidthM: 500,
      worldHeightM: 500,
      elevationMinM: 10,
      elevationMaxM: 10,
      exaggeration: 1,
    })
    expect(framing.position[1]).toBeGreaterThan(0)
    expect(Number.isFinite(framing.far)).toBe(true)
  })
})
