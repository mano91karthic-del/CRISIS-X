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

export function LayerControlPanel({ normalizedLayers, missingRecommended = [] }: LayerControlPanelProps) {
  const layers = useDashboardStore((s) => s.layers)
  const setLayers = useDashboardStore((s) => s.setLayers)

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
