import { useVectorLayer } from './useVectorLayer'
import { ROUTE_COLOR_BY_DATASET_TYPE } from './datasetTypeClassification'

export interface UseRouteLayerOptions {
  layerId: string
  datasetId: string | null
  datasetType: 'route_shortest' | 'route_hazard_aware' | 'route_blocked_segments'
  visible: boolean
  opacity: number
}

/** Thin wrapper over useVectorLayer with route-specific fixed colors --
 * shortest/hazard-aware/blocked are always visually distinguishable, and
 * "blocked" always renders in the same high-contrast color regardless of
 * which route it came from.
 */
export function useRouteLayer(options: UseRouteLayerOptions) {
  const { layerId, datasetId, datasetType, visible, opacity } = options
  return useVectorLayer({
    layerId,
    datasetId,
    visible,
    opacity,
    fallbackColor: ROUTE_COLOR_BY_DATASET_TYPE[datasetType],
  })
}
