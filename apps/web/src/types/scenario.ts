import type { DatasetFull } from './dataset'
import type { ExposureAnalysis } from './exposure'
import type { HazardScenario } from './hazard'
import type { RiskAnalysis } from './risk'
import type { RouteAnalysis } from './routing'

export type ScenarioStatus = 'active' | 'archived'

export interface Scenario {
  id: string
  project_id: string
  twin_id: string
  name: string
  description: string | null
  status: ScenarioStatus
  created_at: string
  updated_at: string
}

export interface ScenarioBaselineLayer {
  id: string
  scenario_id: string
  twin_layer_id: string | null
  dataset_id: string | null
  dataset_type: string
  category: string
  created_at: string
}

export interface ScenarioBaselineLayerResult {
  layer: ScenarioBaselineLayer
  dataset: DatasetFull | null
}

export interface ScenarioLayerOverride {
  id: string
  scenario_id: string
  dataset_type: string
  category: string
  override_dataset_id: string | null
  reason: string | null
  created_at: string
}

export interface ScenarioLayerOverrideResult {
  override: ScenarioLayerOverride
  dataset: DatasetFull | null
}

export interface ScenarioEffectiveLayer {
  dataset_type: string
  category: string
  source: 'baseline' | 'override'
  dataset: DatasetFull | null
  can_override: boolean
}

export interface ScenarioDerivedAnalyses {
  hazard_scenarios: HazardScenario[]
  exposure_analyses: ExposureAnalysis[]
  risk_analyses: RiskAnalysis[]
  route_analyses: RouteAnalysis[]
}

export interface ScenarioState {
  scenario: Scenario
  baseline_layers: ScenarioBaselineLayerResult[]
  layer_overrides: ScenarioLayerOverrideResult[]
  effective_layers: ScenarioEffectiveLayer[]
  derived_analyses: ScenarioDerivedAnalyses
}

export type ComparisonAnalysisType = 'exposure' | 'risk' | 'route'

export interface ScenarioComparison {
  analysis_type: ComparisonAnalysisType
  left_analysis_id: string
  right_analysis_id: string
  diff: Record<string, unknown>
  comparability_warnings: string[]
}
