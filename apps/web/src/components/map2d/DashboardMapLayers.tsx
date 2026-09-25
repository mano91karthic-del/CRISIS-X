import { useDashboardStore } from '../../state/dashboardStore'
import { LayerRenderer } from './layers/LayerRenderer'

/** Renders one LayerRenderer per layer currently known to the dashboard
 * store -- meant to be mounted as MapView's child, inside its
 * MapContext.Provider, so each LayerRenderer's hooks can reach the live
 * map instance.
 */
export function DashboardMapLayers() {
  const layers = useDashboardStore((s) => s.layers)
  return (
    <>
      {Object.values(layers).map((layer) => (
        <LayerRenderer key={layer.layerId} layer={layer} />
      ))}
    </>
  )
}
