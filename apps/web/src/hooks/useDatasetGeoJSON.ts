import { useEffect, useState } from 'react'
import { getDatasetGeoJson } from '../lib/api/previews'
import { isValidGeoJsonEnvelope } from '../geo/geojsonGuards'
import type { GeoJsonPreviewEnvelope } from '../types/preview'

// Simple in-memory cache, keyed by dataset id -- per Phase 11's approved
// "no React Query at this scale" decision. Never persisted, cleared on
// full page reload; a dataset is immutable once validated (Phase 1-10
// never mutate a Dataset's file in place), so caching indefinitely for
// the session's lifetime is safe.
const cache = new Map<string, Promise<GeoJsonPreviewEnvelope>>()

export interface UseDatasetGeoJSONResult {
  data: GeoJsonPreviewEnvelope | null
  loading: boolean
  error: string | null
}

/** Fetches a vector Dataset's WGS84 GeoJSON preview -- lazy, only call
 * this for a dataset the user has actually toggled visible (see
 * MapView's layer hooks). `enabled` lets a caller defer fetching until
 * that's true without violating the rules of hooks.
 */
export function useDatasetGeoJSON(datasetId: string | null, enabled: boolean): UseDatasetGeoJSONResult {
  const [data, setData] = useState<GeoJsonPreviewEnvelope | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!datasetId || !enabled) {
      setData(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)

    let promise = cache.get(datasetId)
    if (!promise) {
      promise = getDatasetGeoJson(datasetId)
      cache.set(datasetId, promise)
    }

    promise
      .then((result) => {
        if (cancelled) return
        if (!isValidGeoJsonEnvelope(result)) {
          setError('Malformed GeoJSON response from server')
          return
        }
        setData(result)
      })
      .catch((err) => {
        cache.delete(datasetId)
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load GeoJSON preview')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [datasetId, enabled])

  return { data, loading, error }
}
