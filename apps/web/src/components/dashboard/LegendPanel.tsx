import { useDashboardStore } from '../../state/dashboardStore'
import { LEGEND_COLORS, ORDINAL_SEVERITY_ORDER } from '../../geo/legendColors'
import { FALLBACK_COLOR_BY_DATASET_TYPE, ROUTE_COLOR_BY_DATASET_TYPE } from '../map2d/layers/datasetTypeClassification'

const CLASSIFIED_DATASET_TYPES = new Set(['landslide_susceptibility', 'risk_classification', 'flood_inundation', 'eo_change_mask'])

// Fixed-color (not class-colored) layer types worth a legend swatch when
// visible, so the fixed per-type colors added alongside this legend are
// always explained on screen rather than left to guesswork.
const FIXED_COLOR_LABEL_BY_DATASET_TYPE: Record<string, string> = {
  roads: 'roads',
  buildings: 'buildings',
  population: 'population',
  hospitals: 'hospitals',
  shelters: 'shelters',
  critical_infrastructure: 'critical infrastructure',
  safe_zones: 'safe zones',
  route_shortest: 'shortest route',
  route_hazard_aware: 'hazard-aware route',
  route_blocked_segments: 'blocked route segments',
}

/** Always-visible legend strip for every currently-visible layer -- never
 * a modal, since color meaning must be visible without a click (see ADR
 * 0012 §3/§13). Derives entirely from dashboardStore + the shared
 * legendColors table; no separate legend state.
 */
export function LegendPanel() {
  const layers = useDashboardStore((s) => s.layers)
  const visibleClassified = Object.values(layers).filter((l) => l.visible && CLASSIFIED_DATASET_TYPES.has(l.datasetType))
  const visibleFixedColor = Object.values(layers).filter(
    (l) => l.visible && FIXED_COLOR_LABEL_BY_DATASET_TYPE[l.datasetType] !== undefined,
  )

  if (visibleClassified.length === 0 && visibleFixedColor.length === 0) {
    return <div className="px-3 py-2 text-xs text-slate-600">No layers currently visible.</div>
  }

  return (
    <div className="flex flex-wrap items-center gap-4 overflow-x-auto px-3 py-2">
      {visibleFixedColor.map((layer) => (
        <div key={layer.layerId} className="flex items-center gap-1">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{
              backgroundColor:
                ROUTE_COLOR_BY_DATASET_TYPE[layer.datasetType] ?? FALLBACK_COLOR_BY_DATASET_TYPE[layer.datasetType],
            }}
          />
          <span className="text-[10px] text-slate-500">{FIXED_COLOR_LABEL_BY_DATASET_TYPE[layer.datasetType]}</span>
        </div>
      ))}
      {visibleClassified.map((layer) => (
        <div key={layer.layerId} className="flex items-center gap-2">
          <span className="text-xs font-medium text-slate-400">{layer.datasetType}:</span>
          {layer.datasetType === 'landslide_susceptibility' || layer.datasetType === 'risk_classification' ? (
            <div className="flex items-center gap-1">
              {ORDINAL_SEVERITY_ORDER.map((label) => (
                <div key={label} className="flex items-center gap-1">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: LEGEND_COLORS[label] }} />
                  <span className="text-[10px] text-slate-500">{label}</span>
                </div>
              ))}
            </div>
          ) : layer.datasetType === 'flood_inundation' ? (
            <div className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: LEGEND_COLORS.inundated }} />
              <span className="text-[10px] text-slate-500">inundated</span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: LEGEND_COLORS.changed }} />
                <span className="text-[10px] text-slate-500">changed</span>
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: LEGEND_COLORS.no_change }} />
                <span className="text-[10px] text-slate-500">no_change</span>
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
