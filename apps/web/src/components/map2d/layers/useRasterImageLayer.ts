import { useEffect, useRef } from 'react'
import { useMapInstance } from '../MapContext'
import { useDatasetRasterPreview } from '../../../hooks/useDatasetRasterPreview'
import { toImageSourceCoordinates } from '../../../geo/bounds'

export interface UseRasterImageLayerOptions {
  layerId: string
  datasetId: string | null
  visible: boolean
  opacity: number
}

/** Adds/updates a MapLibre `image` source + `raster` layer from a
 * dataset's `/preview.png` + `/preview-bounds` -- image sources have no
 * in-place resize, so a dataset-id change removes and re-adds rather
 * than attempting `updateImage`.
 */
export function useRasterImageLayer(options: UseRasterImageLayerOptions) {
  const { layerId, datasetId, visible, opacity } = options
  const map = useMapInstance()
  const { pngUrl, bounds, loading, error } = useDatasetRasterPreview(datasetId, visible)
  const sourceId = `ds-${layerId}`
  const rasterLayerId = `${sourceId}-raster`
  const addedRef = useRef(false)

  useEffect(() => {
    if (!map || !pngUrl || !bounds || !visible) return

    const coordinates = toImageSourceCoordinates(bounds)

    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, { type: 'image', url: pngUrl, coordinates })
      map.addLayer({ id: rasterLayerId, type: 'raster', source: sourceId, paint: { 'raster-opacity': opacity } })
      addedRef.current = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, pngUrl, bounds, visible])

  useEffect(() => {
    if (!map || !addedRef.current) return
    if (map.getLayer(rasterLayerId)) {
      map.setLayoutProperty(rasterLayerId, 'visibility', visible ? 'visible' : 'none')
      map.setPaintProperty(rasterLayerId, 'raster-opacity', opacity)
    }
  }, [map, visible, opacity, rasterLayerId])

  useEffect(() => {
    return () => {
      if (!map) return
      if (map.getLayer(rasterLayerId)) map.removeLayer(rasterLayerId)
      if (map.getSource(sourceId)) map.removeSource(sourceId)
      addedRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, datasetId])

  return { loading, error, layerId: rasterLayerId, sourceId }
}
