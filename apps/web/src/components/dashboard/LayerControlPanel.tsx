import { useEffect } from 'react'
import { useDashboardStore } from '../../state/dashboardStore'
import type { NormalizedLayer } from '../../state/normalizeLayers'
import { LayerControlItem } from './LayerControlItem'
import { MissingLayerNotice } from './states/MissingLayerNotice'
import { EmptyState } from './states/EmptyState'

export interface LayerControlPanelProps {
  normalizedLayers: NormalizedLayer[]
  missingRecommended?: string[]
}

const CATEGORY_ORDER = ['terrain', 'hazard', 'exposure', 'risk', 'route', 'observation']

// Safe Route is a special UI-only layer that maps to the existing hazard-aware route.
// It appears below flood_depth in the hazard category section.
const SAFE_ROUTE_LAYER_ID = 'safe_route'

export function LayerControlPanel({ normalizedLayers, missingRecommended = [] }: LayerControlPanelProps) {
  const layers = useDashboardStore((s) => s.layers)
  const setLayers = useDashboardStore((s) => s.setLayers)
  const safeRouteVisible = useDashboardStore((s) => s.layers[SAFE_ROUTE_LAYER_ID]?.visible ?? false)
  const setLayerVisible = useDashboardStore((s) => s.setLayerVisible)

  useEffect(() => {
    setLayers(normalizedLayers.map((n) => n.layer))
  }, [normalizedLayers, setLayers])

  const datasetById = new Map(normalizedLayers.map((n) => [n.layer.layerId, n.dataset]))

  const byCategory = new Map<string, string[]>()
  for (const layer of Object.values(layers)) {
    const list = byCategory.get(layer.category) ?? []
    list.push(layer.layerId)
    byCategory.set(layer.category, list)
  }

  if (normalizedLayers.length === 0 && missingRecommended.length === 0) {
    return <EmptyState title="No layers registered" description="Register datasets on the Digital Twin to see them here." />
  }

  // Check if flood_depth exists in hazard category for Safe Route placement
  const hazardLayers = byCategory.get('hazard') ?? []
  const hasFloodDepth = hazardLayers.some((id) => layers[id]?.datasetType === 'flood_depth')

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {CATEGORY_ORDER.filter((c) => byCategory.has(c)).map((category) => (
        <div key={category}>
          <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{category}</h3>
          <div className="flex flex-col gap-0.5">
            {byCategory.get(category)!.map((layerId) => {
              const layer = layers[layerId]
              const dataset = datasetById.get(layerId)
              const legendLabel = category === 'hazard' || category === 'risk' ? layer.datasetType : undefined
              return (
                <LayerControlItem
                  key={layerId}
                  layer={layer}
                  hasCrs={!!dataset?.crs}
                  hasError={!!dataset && !dataset.crs}
                  legendLabel={legendLabel}
                />
              )
            })}
          </div>
        </div>
      ))}
      {/* Safe Route - appears below flood_depth in hazard section */}
      {hasFloodDepth && (
        <div className="flex flex-col gap-1 rounded px-2 py-1.5 hover:bg-slate-900">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={safeRouteVisible}
              onChange={(e) => setLayerVisible(SAFE_ROUTE_LAYER_ID, e.target.checked)}
              aria-label="Toggle Safe Route visibility"
            />
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400" aria-hidden="true" />
            <span className="flex-1 truncate text-sm text-slate-200">Safe Route</span>
          </div>
        </div>
      )}
      {missingRecommended.length > 0 && (
        <div>
          <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Missing</h3>
          <div className="flex flex-col gap-0.5">
            {missingRecommended.map((category) => (
              <MissingLayerNotice key={category} label={category} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
