import type { GeoJsonPreviewEnvelope } from '../types/preview'

/** Runtime shape check on a /datasets/{id}/geojson response -- fails
 * loudly (returns false) rather than letting a malformed response reach
 * MapLibre, which would either throw deep inside the library or silently
 * render nothing with no diagnosis. Never attempts to "fix" or coerce a
 * malformed response client-side.
 */
export function isValidGeoJsonEnvelope(value: unknown): value is GeoJsonPreviewEnvelope {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    v.type === 'FeatureCollection' &&
    Array.isArray(v.features) &&
    typeof v.truncated === 'boolean' &&
    typeof v.total_feature_count === 'number' &&
    typeof v.source_crs === 'string'
  )
}

/** True when the backend truncated the feature list -- callers must
 * surface this (see components/dashboard/states/TruncationBanner.tsx),
 * never silently render a partial dataset as if it were complete.
 */
export function isTruncated(envelope: GeoJsonPreviewEnvelope): boolean {
  return envelope.truncated === true && envelope.total_feature_count > envelope.features.length
}
