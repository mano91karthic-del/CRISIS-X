import { useEffect, useRef } from 'react'
import type { ExpressionSpecification, GeoJSONSource, MapLayerMouseEvent } from 'maplibre-gl'
import { useMapInstance } from '../MapContext'
import { useDatasetGeoJSON } from '../../../hooks/useDatasetGeoJSON'
import { LEGEND_COLORS, UNKNOWN_LABEL_COLOR } from '../../../geo/legendColors'
import { useDashboardStore } from '../../../state/dashboardStore'

export interface UseVectorLayerOptions {
  layerId: string
  datasetId: string | null
  visible: boolean
  opacity: number
  /** Flat color used when `colorProperty` is not set or a feature lacks it. */
  fallbackColor: string
  /** When set, colors each feature by looking up this GeoJSON property
   * value in the shared LEGEND_COLORS table (e.g. "risk_class",
   * "hazard_class_label") -- never a per-layer ad hoc palette.
   */
  colorProperty?: string
}

function buildColorExpression(colorProperty: string | undefined, fallback: string): string | ExpressionSpecification {
  if (!colorProperty) return fallback
  const stops: (string | number)[] = []
  for (const [label, color] of Object.entries(LEGEND_COLORS)) {
    stops.push(label, color)
  }
  return ['match', ['get', colorProperty], ...stops, UNKNOWN_LABEL_COLOR] as unknown as ExpressionSpecification
}

/** Adds/updates a MapLibre `geojson` source + fill/line/circle layer(s)
 * for one vector Dataset, based on the geometry type actually present.
 * Fetches lazily (only when `visible`) via useDatasetGeoJSON, which
 * already guarantees WGS84 -- this hook never reprojects. Also wires
 * click/hover interaction directly on the layers it just added (see ADR
 * 0012 §17) -- inline, not via a separate hook, since the layer ids are
 * only known synchronously inside this same effect.
 */
export function useVectorLayer(options: UseVectorLayerOptions) {
  const { layerId, datasetId, visible, opacity, fallbackColor, colorProperty } = options
  const map = useMapInstance()
  const { data, loading, error } = useDatasetGeoJSON(datasetId, visible)
  const addedLayerIds = useRef<string[]>([])
  const setActiveFeature = useDashboardStore((s) => s.setActiveFeature)

  const sourceId = `ds-${layerId}`

  useEffect(() => {
    if (!map || !data || !visible) return

    const geomType = data.features[0]?.geometry?.type ?? null
    if (!geomType) return

    const existingSource = map.getSource(sourceId)
    if (!existingSource) {
      map.addSource(sourceId, { type: 'geojson', data: data as unknown as GeoJSON.FeatureCollection })
    } else if (existingSource.type === 'geojson') {
      ;(existingSource as GeoJSONSource).setData(data as unknown as GeoJSON.FeatureCollection)
    }

    const colorExpr = buildColorExpression(colorProperty, fallbackColor)
    const newLayerIds: string[] = []

    if (geomType.includes('Polygon')) {
      const fillId = `${sourceId}-fill`
      const lineId = `${sourceId}-outline`
      if (!map.getLayer(fillId)) {
        map.addLayer({
          id: fillId,
          type: 'fill',
          source: sourceId,
          paint: { 'fill-color': colorExpr, 'fill-opacity': opacity },
        })
      }
      if (!map.getLayer(lineId)) {
        map.addLayer({ id: lineId, type: 'line', source: sourceId, paint: { 'line-color': colorExpr, 'line-width': 1 } })
      }
      newLayerIds.push(fillId, lineId)
    } else if (geomType.includes('LineString')) {
      const lineId = `${sourceId}-line`
      if (!map.getLayer(lineId)) {
        map.addLayer({
          id: lineId,
          type: 'line',
          source: sourceId,
          paint: { 'line-color': colorExpr, 'line-width': 3, 'line-opacity': opacity },
        })
      }
      newLayerIds.push(lineId)
    } else {
      const circleId = `${sourceId}-circle`
      if (!map.getLayer(circleId)) {
        map.addLayer({
          id: circleId,
          type: 'circle',
          source: sourceId,
          paint: { 'circle-color': colorExpr, 'circle-radius': 5, 'circle-opacity': opacity },
        })
      }
      newLayerIds.push(circleId)
    }

    addedLayerIds.current = newLayerIds

    const handleClick = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0]
      if (!feature || !datasetId) return
      setActiveFeature({ datasetId, featureId: feature.id ?? null, properties: feature.properties ?? {} })
    }
    const handleEnter = () => {
      map.getCanvas().style.cursor = 'pointer'
    }
    const handleLeave = () => {
      map.getCanvas().style.cursor = ''
    }
    map.on('click', newLayerIds, handleClick)
    map.on('mouseenter', newLayerIds, handleEnter)
    map.on('mouseleave', newLayerIds, handleLeave)

    return () => {
      map.off('click', newLayerIds, handleClick)
      map.off('mouseenter', newLayerIds, handleEnter)
      map.off('mouseleave', newLayerIds, handleLeave)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, data, visible])

  useEffect(() => {
    if (!map) return
    for (const id of addedLayerIds.current) {
      if (!map.getLayer(id)) continue
      map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
      const kind = map.getLayer(id)?.type
      if (kind === 'fill') map.setPaintProperty(id, 'fill-opacity', opacity)
      if (kind === 'line') map.setPaintProperty(id, 'line-opacity', opacity)
      if (kind === 'circle') map.setPaintProperty(id, 'circle-opacity', opacity)
    }
  }, [map, visible, opacity])

  useEffect(() => {
    return () => {
      if (!map) return
      for (const id of addedLayerIds.current) {
        if (map.getLayer(id)) map.removeLayer(id)
      }
      if (map.getSource(sourceId)) map.removeSource(sourceId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, datasetId])

  return { loading, error, layerIds: addedLayerIds.current, sourceId }
}
