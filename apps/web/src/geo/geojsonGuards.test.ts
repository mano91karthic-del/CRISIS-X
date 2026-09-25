import { describe, expect, it } from 'vitest'
import { isTruncated, isValidGeoJsonEnvelope } from './geojsonGuards'
import type { GeoJsonPreviewEnvelope } from '../types/preview'

const validEnvelope: GeoJsonPreviewEnvelope = {
  type: 'FeatureCollection',
  features: [],
  truncated: false,
  total_feature_count: 0,
  source_crs: 'EPSG:32643',
}

describe('isValidGeoJsonEnvelope', () => {
  it('accepts a well-formed envelope', () => {
    expect(isValidGeoJsonEnvelope(validEnvelope)).toBe(true)
  })

  it('rejects a bare FeatureCollection missing the envelope fields', () => {
    expect(isValidGeoJsonEnvelope({ type: 'FeatureCollection', features: [] })).toBe(false)
  })

  it('rejects null, arrays, and primitives', () => {
    expect(isValidGeoJsonEnvelope(null)).toBe(false)
    expect(isValidGeoJsonEnvelope([])).toBe(false)
    expect(isValidGeoJsonEnvelope('not an object')).toBe(false)
  })

  it('rejects a malformed features field (not an array)', () => {
    expect(isValidGeoJsonEnvelope({ ...validEnvelope, features: 'oops' })).toBe(false)
  })
})

describe('isTruncated', () => {
  it('is false when truncated=false', () => {
    expect(isTruncated(validEnvelope)).toBe(false)
  })

  it('is true when truncated=true and features are fewer than total_feature_count', () => {
    const truncated: GeoJsonPreviewEnvelope = {
      ...validEnvelope,
      truncated: true,
      total_feature_count: 100,
      features: new Array(20).fill({ type: 'Feature', geometry: null, properties: null }),
    }
    expect(isTruncated(truncated)).toBe(true)
  })

  it('is false if truncated=true but counts happen to match (defensive, should not occur from the backend)', () => {
    const inconsistent: GeoJsonPreviewEnvelope = {
      ...validEnvelope,
      truncated: true,
      total_feature_count: 0,
      features: [],
    }
    expect(isTruncated(inconsistent)).toBe(false)
  })
})
