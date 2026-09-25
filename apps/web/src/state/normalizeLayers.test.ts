import { describe, expect, it } from 'vitest'
import { normalizeScenarioLayers, normalizeTwinLayers } from './normalizeLayers'
import type { DigitalTwinState } from '../types/digitalTwin'
import type { ScenarioState } from '../types/scenario'

function makeDataset(id: string): any {
  return { id, dataset_type: 'dem', crs: 'EPSG:32643' }
}

describe('normalizeTwinLayers', () => {
  const state: DigitalTwinState = {
    twin: { id: 't1', project_id: 'p1', name: 'T', description: null, version: 1, created_at: '', updated_at: '' },
    extent: { reference_crs: null, bbox_min_x: null, bbox_min_y: null, bbox_max_x: null, bbox_max_y: null, crs_mismatch_layer_ids: [], layers_without_extent: [] },
    acquisition_date_range: { earliest: null, latest: null, layers_without_acquisition_date: [] },
    layers_by_category: {
      terrain: [
        { layer: { id: 'l1', twin_id: 't1', dataset_id: 'd1', dataset_type: 'dem', category: 'terrain', status: 'active', registered_at_version: 1, provenance: {}, created_at: '', updated_at: '' }, dataset: makeDataset('d1') },
        { layer: { id: 'l2', twin_id: 't1', dataset_id: 'd2', dataset_type: 'slope', category: 'terrain', status: 'superseded', registered_at_version: 1, provenance: {}, created_at: '', updated_at: '' }, dataset: makeDataset('d2') },
      ],
    },
    missing_recommended_layers: [],
    limitations: [],
  }

  it('includes only active layers, never superseded/retired ones', () => {
    const result = normalizeTwinLayers(state)
    expect(result).toHaveLength(1)
    expect(result[0].layer.layerId).toBe('l1')
  })

  it('defaults visibility to false and opacity to a fixed default', () => {
    const [item] = normalizeTwinLayers(state)
    expect(item.layer.visible).toBe(false)
    expect(item.layer.opacity).toBeGreaterThan(0)
  })

  it('carries the dataset through unchanged', () => {
    const [item] = normalizeTwinLayers(state)
    expect(item.dataset?.id).toBe('d1')
  })
})

describe('normalizeScenarioLayers', () => {
  const state: ScenarioState = {
    scenario: { id: 's1', project_id: 'p1', twin_id: 't1', name: 'S', description: null, status: 'active', created_at: '', updated_at: '' },
    baseline_layers: [],
    layer_overrides: [],
    effective_layers: [
      { dataset_type: 'dem', category: 'terrain', source: 'baseline', dataset: makeDataset('d1'), can_override: true },
      { dataset_type: 'flood_inundation', category: 'hazard', source: 'override', dataset: makeDataset('d3'), can_override: false },
    ],
    derived_analyses: { hazard_scenarios: [], exposure_analyses: [], risk_analyses: [], route_analyses: [] },
  }

  it('uses dataset_type as the layer id, one row per effective layer', () => {
    const result = normalizeScenarioLayers(state)
    expect(result.map((r) => r.layer.layerId)).toEqual(['dem', 'flood_inundation'])
  })

  it('handles a null dataset (missing layer) without throwing', () => {
    const stateWithMissing: ScenarioState = {
      ...state,
      effective_layers: [{ dataset_type: 'roads', category: 'observation', source: 'baseline', dataset: null, can_override: true }],
    }
    const result = normalizeScenarioLayers(stateWithMissing)
    expect(result[0].dataset).toBeNull()
    expect(result[0].layer.datasetId).toBeNull()
  })
})
