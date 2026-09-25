import type { GeoJsonPreviewEnvelope, RasterPreviewBounds } from '../../types/preview'
import { apiFetch, apiUrl } from './client'

export async function getDatasetGeoJson(
  datasetId: string,
  params?: { limit?: number; precision?: number },
): Promise<GeoJsonPreviewEnvelope> {
  const query = new URLSearchParams()
  if (params?.limit !== undefined) query.set('limit', String(params.limit))
  if (params?.precision !== undefined) query.set('precision', String(params.precision))
  const qs = query.toString()
  return apiFetch<GeoJsonPreviewEnvelope>(`/datasets/${datasetId}/geojson${qs ? `?${qs}` : ''}`)
}

export function datasetPreviewPngUrl(datasetId: string, maxDim?: number): string {
  const qs = maxDim ? `?max_dim=${maxDim}` : ''
  return apiUrl(`/datasets/${datasetId}/preview.png${qs}`)
}

export async function getDatasetPreviewBounds(datasetId: string): Promise<RasterPreviewBounds> {
  return apiFetch<RasterPreviewBounds>(`/datasets/${datasetId}/preview-bounds`)
}

export function datasetHeightmapPngUrl(datasetId: string, maxDim?: number): string {
  const qs = maxDim ? `?max_dim=${maxDim}` : ''
  return apiUrl(`/datasets/${datasetId}/heightmap.png${qs}`)
}

export async function getDatasetHeightmapBounds(datasetId: string): Promise<RasterPreviewBounds> {
  return apiFetch<RasterPreviewBounds>(`/datasets/${datasetId}/heightmap-bounds`)
}
