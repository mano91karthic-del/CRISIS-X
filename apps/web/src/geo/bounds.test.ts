import { describe, expect, it } from 'vitest'
import { isValidBounds, isWgs84Crs, toImageSourceCoordinates, toMapLibreBounds, unionBounds } from './bounds'

describe('isValidBounds', () => {
  it('accepts a well-formed bounds object', () => {
    expect(isValidBounds({ west: -1, south: -1, east: 1, north: 1 })).toBe(true)
  })

  it('rejects null/undefined', () => {
    expect(isValidBounds(null)).toBe(false)
    expect(isValidBounds(undefined)).toBe(false)
  })

  it('rejects NaN/Infinity components', () => {
    expect(isValidBounds({ west: NaN, south: -1, east: 1, north: 1 })).toBe(false)
    expect(isValidBounds({ west: -1, south: -1, east: Infinity, north: 1 })).toBe(false)
  })

  it('rejects an inverted box (west > east)', () => {
    expect(isValidBounds({ west: 5, south: -1, east: 1, north: 1 })).toBe(false)
  })
})

describe('unionBounds', () => {
  it('unions multiple valid boxes into their bounding envelope', () => {
    const result = unionBounds([
      { west: 0, south: 0, east: 1, north: 1 },
      { west: -1, south: -1, east: 0.5, north: 0.5 },
    ])
    expect(result).toEqual({ west: -1, south: -1, east: 1, north: 1 })
  })

  it('skips invalid/missing entries rather than poisoning the union', () => {
    const result = unionBounds([null, { west: 0, south: 0, east: 1, north: 1 }, undefined])
    expect(result).toEqual({ west: 0, south: 0, east: 1, north: 1 })
  })

  it('returns null when nothing valid was given -- never a guessed default extent', () => {
    expect(unionBounds([null, undefined])).toBeNull()
    expect(unionBounds([])).toBeNull()
  })
})

describe('toMapLibreBounds / toImageSourceCoordinates', () => {
  const b = { west: -10, south: -5, east: 10, north: 5 }

  it('produces MapLibre fitBounds-shaped [[sw],[ne]]', () => {
    expect(toMapLibreBounds(b)).toEqual([
      [-10, -5],
      [10, 5],
    ])
  })

  it('produces an image-source coordinates array clockwise from top-left', () => {
    expect(toImageSourceCoordinates(b)).toEqual([
      [-10, 5],
      [10, 5],
      [10, -5],
      [-10, -5],
    ])
  })
})

describe('isWgs84Crs', () => {
  it('recognizes EPSG:4326 in its common string forms', () => {
    expect(isWgs84Crs('EPSG:4326')).toBe(true)
    expect(isWgs84Crs('WGS 84')).toBe(true)
    expect(isWgs84Crs('epsg:4326')).toBe(true)
  })

  it('rejects a projected CRS', () => {
    expect(isWgs84Crs('EPSG:32643')).toBe(false)
    expect(isWgs84Crs('EPSG:32644')).toBe(false)
  })

  it('rejects null/undefined without throwing', () => {
    expect(isWgs84Crs(null)).toBe(false)
    expect(isWgs84Crs(undefined)).toBe(false)
  })
})
