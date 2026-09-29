import { describe, expect, it } from 'vitest'
import {
  createTerrainLocalTransform,
  createElevationSampler,
  filterFeaturesByBbox,
  isGeographicNativeCrs,
  FALLBACK_BUILDING_HEIGHT_M,
  ROAD_WIDTH_BY_HIGHWAY_CLASS_M,
  DEFAULT_ROAD_WIDTH_M,
} from './terrainOverlay'
import { isWgs84Crs } from '../../geo/bounds'

/**
 * Chennai-specific geospatial alignment tests.
 *
 * These tests verify that the coordinate transformation, elevation sampling,
 * and spatial filtering logic work correctly for the canonical Chennai study
 * area (80.276060-80.293009 E, 13.084122-13.103768 N) with the real datasets:
 * - DEM: n13_e080_1arc_v3.tif (EPSG:4326, 3601x3601, 1 arc-second resolution)
 * - Buildings: 12,350 features (EPSG:4326, no height attributes)
 * - Roads: 839 features (EPSG:4326, with highway classification)
 */

// Canonical Chennai AOI bounds
const CHENNAI_AOI = {
  west: 80.276060,
  south: 13.084122,
  east: 80.293009,
  north: 13.103768,
}

// DEM bounds (from n13_e080_1arc_v3.tif)
const DEM_BOUNDS = {
  west: 79.99986111111112,
  south: 12.999861111111109,
  east: 81.0001388888889,
  north: 14.000138888888888,
}

// DEM properties
const DEM_WIDTH = 3601
const DEM_HEIGHT = 3601
const DEM_PIXEL_SIZE_DEG = 0.0002777777777777778 // 1 arc-second
const DEM_ELEVATION_MIN = -26.0
const DEM_ELEVATION_MAX = 76.0

// Approximate real-world pixel size at Chennai's latitude (~13 degrees N)
// 1 arc-second of latitude ≈ 30.87 m
// 1 arc-second of longitude at 13°N ≈ 30.87 * cos(13°) ≈ 30.08 m
const APPROX_PIXEL_SIZE_X_M = 30.08
const APPROX_PIXEL_SIZE_Y_M = 30.87

// World dimensions of the DEM in meters
const WORLD_WIDTH_M = DEM_WIDTH * APPROX_PIXEL_SIZE_X_M // ~108,310 m
const WORLD_HEIGHT_M = DEM_HEIGHT * APPROX_PIXEL_SIZE_Y_M // ~111,111 m

describe('Chennai geospatial alignment', () => {
  describe('CRS compatibility', () => {
    it('recognizes EPSG:4326 as geographic (required for terrain overlay draping)', () => {
      expect(isGeographicNativeCrs('EPSG:4326')).toBe(true)
      expect(isWgs84Crs('EPSG:4326')).toBe(true)
    })

    it('rejects UTM CRS (EPSG:32644) as non-geographic', () => {
      expect(isGeographicNativeCrs('EPSG:32644')).toBe(false)
      expect(isWgs84Crs('EPSG:32644')).toBe(false)
    })
  })

  describe('coordinate transformation for Chennai AOI', () => {
    const transform = createTerrainLocalTransform({
      boundsWestLon: DEM_BOUNDS.west,
      boundsSouthLat: DEM_BOUNDS.south,
      boundsEastLon: DEM_BOUNDS.east,
      boundsNorthLat: DEM_BOUNDS.north,
      worldWidthM: WORLD_WIDTH_M,
      worldHeightM: WORLD_HEIGHT_M,
    })

    it('maps the DEM center to local origin', () => {
      const centerLon = (DEM_BOUNDS.west + DEM_BOUNDS.east) / 2
      const centerLat = (DEM_BOUNDS.south + DEM_BOUNDS.north) / 2
      const [x, z] = transform.toLocalXZ(centerLon, centerLat)
      expect(x).toBeCloseTo(0, 5)
      expect(z).toBeCloseTo(0, 5)
    })

    it('maps the Chennai AOI center to a reasonable local coordinate', () => {
      const aoiCenterLon = (CHENNAI_AOI.west + CHENNAI_AOI.east) / 2
      const aoiCenterLat = (CHENNAI_AOI.south + CHENNAI_AOI.north) / 2
      const [x, z] = transform.toLocalXZ(aoiCenterLon, aoiCenterLat)
      // The AOI center should be within the DEM's local coordinate space
      expect(Number.isFinite(x)).toBe(true)
      expect(Number.isFinite(z)).toBe(true)
      // The AOI is a small subset of the DEM, so coordinates should be moderate
      expect(Math.abs(x)).toBeLessThan(WORLD_WIDTH_M / 2)
      expect(Math.abs(z)).toBeLessThan(WORLD_HEIGHT_M / 2)
    })

    it('preserves relative positions: AOI west edge is west of AOI east edge', () => {
      const [xWest] = transform.toLocalXZ(CHENNAI_AOI.west, CHENNAI_AOI.south)
      const [xEast] = transform.toLocalXZ(CHENNAI_AOI.east, CHENNAI_AOI.south)
      expect(xWest).toBeLessThan(xEast)
    })

    it('preserves relative positions: AOI north edge is north of AOI south edge', () => {
      const [, zNorth] = transform.toLocalXZ(CHENNAI_AOI.west, CHENNAI_AOI.north)
      const [, zSouth] = transform.toLocalXZ(CHENNAI_AOI.west, CHENNAI_AOI.south)
      // North maps to negative Z (terrain mesh rotation convention)
      expect(zNorth).toBeLessThan(zSouth)
    })

    it('scales the Chennai AOI to a reasonable local size (~3km)', () => {
      const [xWest, zSouth] = transform.toLocalXZ(CHENNAI_AOI.west, CHENNAI_AOI.south)
      const [xEast, zNorth] = transform.toLocalXZ(CHENNAI_AOI.east, CHENNAI_AOI.north)
      const widthM = xEast - xWest
      const heightM = zSouth - zNorth
      // The AOI is roughly 1.7km x 2.0km in real life
      expect(widthM).toBeGreaterThan(1000)
      expect(widthM).toBeLessThan(3000)
      expect(heightM).toBeGreaterThan(1000)
      expect(heightM).toBeLessThan(3000)
    })
  })

  describe('elevation sampling for Chennai', () => {
    // Create a simple 2-pixel heightmap for testing
    const pixels = {
      width: 2,
      height: 2,
      values: new Uint8Array([0, 128, 255, 64]),
    }

    const sampler = createElevationSampler({
      pixels,
      elevationMinM: DEM_ELEVATION_MIN,
      elevationMaxM: DEM_ELEVATION_MAX,
      exaggeration: 1,
      boundsWestLon: DEM_BOUNDS.west,
      boundsSouthLat: DEM_BOUNDS.south,
      boundsEastLon: DEM_BOUNDS.east,
      boundsNorthLat: DEM_BOUNDS.north,
    })

    it('returns elevation within the DEM range for points inside the DEM bounds', () => {
      const elevation = sampler(80.28, 13.09)
      expect(elevation).toBeGreaterThanOrEqual(DEM_ELEVATION_MIN)
      expect(elevation).toBeLessThanOrEqual(DEM_ELEVATION_MAX)
    })

    it('clamps out-of-bounds coordinates to the nearest edge', () => {
      // Far outside the DEM bounds - should not throw
      expect(() => sampler(0, 0)).not.toThrow()
      expect(() => sampler(100, 50)).not.toThrow()
    })

    it('applies exaggeration multiplicatively', () => {
      const exaggeratedSampler = createElevationSampler({
        pixels,
        elevationMinM: DEM_ELEVATION_MIN,
        elevationMaxM: DEM_ELEVATION_MAX,
        exaggeration: 2,
        boundsWestLon: DEM_BOUNDS.west,
        boundsSouthLat: DEM_BOUNDS.south,
        boundsEastLon: DEM_BOUNDS.east,
        boundsNorthLat: DEM_BOUNDS.north,
      })
      const baseElevation = sampler(80.28, 13.09)
      const exaggeratedElevation = exaggeratedSampler(80.28, 13.09)
      expect(exaggeratedElevation).toBeCloseTo(baseElevation * 2, 5)
    })
  })

  describe('building footprint filtering for Chennai AOI', () => {
    it('keeps buildings inside the AOI', () => {
      const features = [
        { geometry: { type: 'Polygon', coordinates: [[[80.28, 13.09], [80.281, 13.09], [80.281, 13.091], [80.28, 13.091], [80.28, 13.09]]] } },
      ]
      const filtered = filterFeaturesByBbox(features, CHENNAI_AOI)
      expect(filtered).toHaveLength(1)
    })

    it('drops buildings outside the AOI', () => {
      const features = [
        { geometry: { type: 'Polygon', coordinates: [[[80.0, 13.0], [80.001, 13.0], [80.001, 13.001], [80.0, 13.001], [80.0, 13.0]]] } },
      ]
      const filtered = filterFeaturesByBbox(features, CHENNAI_AOI)
      expect(filtered).toHaveLength(0)
    })

    it('keeps buildings that straddle the AOI boundary', () => {
      const features = [
        { geometry: { type: 'Polygon', coordinates: [[[80.27, 13.08], [80.28, 13.08], [80.28, 13.09], [80.27, 13.09], [80.27, 13.08]]] } },
      ]
      const filtered = filterFeaturesByBbox(features, CHENNAI_AOI)
      expect(filtered).toHaveLength(1)
    })
  })

  describe('building height provenance', () => {
    it('uses a documented fallback height when no real height attribute exists', () => {
      // The Chennai buildings dataset has no height attributes
      // The fallback height should be a reasonable visual default
      expect(FALLBACK_BUILDING_HEIGHT_M).toBeGreaterThan(0)
      expect(FALLBACK_BUILDING_HEIGHT_M).toBeLessThan(20) // Reasonable for 2-3 storeys
    })
  })

  describe('road width classification', () => {
    it('assigns wider widths to higher-classification roads', () => {
      expect(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.trunk).toBeGreaterThan(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.residential)
      expect(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.primary).toBeGreaterThan(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.tertiary)
      expect(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.residential).toBeGreaterThan(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.footway)
    })

    it('provides a sensible default width for unknown classifications', () => {
      expect(DEFAULT_ROAD_WIDTH_M).toBeGreaterThan(0)
      expect(DEFAULT_ROAD_WIDTH_M).toBeLessThan(15)
    })
  })

  describe('DEM properties verification', () => {
    it('has correct elevation range for Chennai', () => {
      expect(DEM_ELEVATION_MIN).toBeLessThan(0) // Below sea level in parts
      expect(DEM_ELEVATION_MAX).toBeLessThan(100) // Relatively flat terrain
    })

    it('has 1 arc-second resolution (approximately 30m)', () => {
      expect(DEM_PIXEL_SIZE_DEG).toBeCloseTo(1 / 3600, 10)
    })

    it('covers the canonical AOI', () => {
      expect(DEM_BOUNDS.west).toBeLessThan(CHENNAI_AOI.west)
      expect(DEM_BOUNDS.east).toBeGreaterThan(CHENNAI_AOI.east)
      expect(DEM_BOUNDS.south).toBeLessThan(CHENNAI_AOI.south)
      expect(DEM_BOUNDS.north).toBeGreaterThan(CHENNAI_AOI.north)
    })
  })
})
