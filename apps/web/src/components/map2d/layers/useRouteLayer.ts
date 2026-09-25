import { useVectorLayer } from './useVectorLayer'

const ROUTE_COLORS: Record<string, string> = {
  route_shortest: '#94a3b8', // neutral slate -- the plain shortest path
  route_hazard_aware: '#22d3ee', // accent cyan -- the hazard-aware path
  route_blocked_segments: '#ef4444', // high-contrast red -- always visually "blocked"
}

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
    fallbackColor: ROUTE_COLORS[datasetType],
  })
}
