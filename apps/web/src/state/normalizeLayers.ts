import type { DatasetFull } from '../types/dataset'
import type { DigitalTwinState } from '../types/digitalTwin'
import type { ScenarioState } from '../types/scenario'
import type { LayerState } from './dashboardStore'

export interface NormalizedLayer {
  layer: LayerState
  dataset: DatasetFull | null
}

const DEFAULT_OPACITY = 0.8

/** Converts a Digital Twin's `/state` response into the dashboard's
 * generic LayerState shape -- only ACTIVE layers are surfaced (matching
 * the twin's own "current state" semantics); superseded/retired layers
 * are history, not something the map renders. Visibility always
 * defaults to false: nothing is auto-shown on load, the operator
 * chooses what to render.
 */
export function normalizeTwinLayers(state: DigitalTwinState): NormalizedLayer[] {
  const result: NormalizedLayer[] = []
  for (const [category, layerResults] of Object.entries(state.layers_by_category)) {
    for (const lr of layerResults) {
      if (lr.layer.status !== 'active') continue
      result.push({
        layer: {
          layerId: lr.layer.id,
          datasetId: lr.layer.dataset_id,
          datasetType: lr.layer.dataset_type,
          category,
          visible: false,
          opacity: DEFAULT_OPACITY,
          zIndex: 0,
        },
        dataset: lr.dataset,
      })
    }
  }
  return result
}

/** Converts a Scenario's `/state` response into the dashboard's generic
 * LayerState shape, using `effective_layers` (override-wins-over-
 * baseline, per app/services/scenario.py::resolve_effective_layers) --
 * this is deliberately what a scenario view renders, never the raw
 * baseline_layers/layer_overrides lists side by side.
 */
export function normalizeScenarioLayers(state: ScenarioState): NormalizedLayer[] {
  return state.effective_layers.map((effectiveLayer) => ({
    layer: {
      layerId: effectiveLayer.dataset_type,
      datasetId: effectiveLayer.dataset?.id ?? null,
      datasetType: effectiveLayer.dataset_type,
      category: effectiveLayer.category,
      visible: false,
      opacity: DEFAULT_OPACITY,
      zIndex: 0,
    },
    dataset: effectiveLayer.dataset,
  }))
}
