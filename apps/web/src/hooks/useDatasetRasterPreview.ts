import { useEffect, useState } from 'react'
import { datasetPreviewPngUrl, getDatasetPreviewBounds } from '../lib/api/previews'
import type { RasterPreviewBounds } from '../types/preview'

const boundsCache = new Map<string, Promise<RasterPreviewBounds>>()

export interface UseDatasetRasterPreviewResult {
  pngUrl: string | null
  bounds: RasterPreviewBounds | null
  loading: boolean
  error: string | null
}

/** Pairs a raster Dataset's colorized `/preview.png` URL with its WGS84
 * `/preview-bounds`, for a MapLibre `image` source. Only the bounds are
 * actually fetched by this hook (needed to compute `coordinates`); the
 * PNG URL is handed directly to MapLibre, which does its own fetch.
 */
export function useDatasetRasterPreview(datasetId: string | null, enabled: boolean): UseDatasetRasterPreviewResult {
  const [bounds, setBounds] = useState<RasterPreviewBounds | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!datasetId || !enabled) {
      setBounds(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)

    let promise = boundsCache.get(datasetId)
    if (!promise) {
      promise = getDatasetPreviewBounds(datasetId)
      boundsCache.set(datasetId, promise)
    }

    promise
      .then((result) => {
        if (!cancelled) setBounds(result)
      })
      .catch((err) => {
        boundsCache.delete(datasetId)
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load raster preview bounds')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [datasetId, enabled])

  return {
    pngUrl: datasetId && enabled ? datasetPreviewPngUrl(datasetId) : null,
    bounds,
    loading,
    error,
  }
}
