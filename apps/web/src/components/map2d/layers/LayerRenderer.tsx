import type { LayerState } from '../../../state/dashboardStore'
import { useRasterImageLayer } from './useRasterImageLayer'
import { useVectorLayer } from './useVectorLayer'
import { useRouteLayer } from './useRouteLayer'
import {
  CLASS_COLORED_VECTOR_TYPES,
  COLOR_PROPERTY_BY_DATASET_TYPE,
  DEFAULT_FALLBACK_COLOR,
  FALLBACK_COLOR_BY_DATASET_TYPE,
  isRasterDatasetType,
} from './datasetTypeClassification'

const ROUTE_TYPES = new Set(['route_shortest', 'route_hazard_aware', 'route_blocked_segments'])

/** One instance per active LayerState -- calls exactly one of
 * useRasterImageLayer/useVectorLayer/useRouteLayer unconditionally
 * (datasetId gated to null when not applicable), so hook call order
 * stays stable across renders regardless of which branch is "active."
 * Renders nothing itself; it's a pure side-effect component that adds/
 * removes MapLibre sources+layers via its hooks.
 */
export function LayerRenderer({ layer }: { layer: LayerState }) {
  const isRaster = isRasterDatasetType(layer.datasetType)
  const isRoute = ROUTE_TYPES.has(layer.datasetType)

  useRasterImageLayer({
    layerId: layer.layerId,
    datasetId: isRaster ? layer.datasetId : null,
    visible: layer.visible && isRaster,
    opacity: layer.opacity,
  })

  useRouteLayer({
    layerId: layer.layerId,
    datasetId: isRoute ? layer.datasetId : null,
    datasetType: (isRoute ? layer.datasetType : 'route_shortest') as 'route_shortest' | 'route_hazard_aware' | 'route_blocked_segments',
    visible: layer.visible && isRoute,
    opacity: layer.opacity,
  })

  useVectorLayer({
    layerId: layer.layerId,
    datasetId: !isRaster && !isRoute ? layer.datasetId : null,
    visible: layer.visible && !isRaster && !isRoute,
    opacity: layer.opacity,
    fallbackColor: FALLBACK_COLOR_BY_DATASET_TYPE[layer.datasetType] ?? DEFAULT_FALLBACK_COLOR,
    colorProperty: CLASS_COLORED_VECTOR_TYPES.has(layer.datasetType) ? COLOR_PROPERTY_BY_DATASET_TYPE[layer.datasetType] : undefined,
  })

  return null
}
