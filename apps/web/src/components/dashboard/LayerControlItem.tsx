import { useDashboardStore, type LayerState } from '../../state/dashboardStore'
import { colorForLabel } from '../../geo/legendColors'

export interface LayerControlItemProps {
  layer: LayerState
  hasCrs: boolean
  hasError: boolean
  legendLabel?: string
}

export function LayerControlItem({ layer, hasCrs, hasError, legendLabel }: LayerControlItemProps) {
  const setLayerVisible = useDashboardStore((s) => s.setLayerVisible)
  const setLayerOpacity = useDashboardStore((s) => s.setLayerOpacity)

  const swatchColor = legendLabel ? colorForLabel(legendLabel) : '#64748b'

  return (
    <div className="flex flex-col gap-1 rounded px-2 py-1.5 hover:bg-slate-900">
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={layer.visible}
          onChange={(e) => setLayerVisible(layer.layerId, e.target.checked)}
          aria-label={`Toggle ${layer.datasetType} layer visibility`}
        />
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: swatchColor }} aria-hidden="true" />
        <span className="flex-1 truncate text-sm text-slate-200">{layer.datasetType}</span>
        {!hasCrs && (
          <span className="rounded bg-red-900/60 px-1.5 py-0.5 text-[10px] uppercase text-red-300" title="Dataset has no CRS">
            no crs
          </span>
        )}
        {hasError && (
          <span
            className="rounded bg-red-900/60 px-1.5 py-0.5 text-[10px] uppercase text-red-300"
            title="Could not render this layer"
          >
            crs error
          </span>
        )}
      </div>
      {layer.visible && (
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={layer.opacity}
          onChange={(e) => setLayerOpacity(layer.layerId, Number(e.target.value))}
          className="w-full accent-emerald-500"
          aria-label={`${layer.datasetType} opacity`}
        />
      )}
    </div>
  )
}
