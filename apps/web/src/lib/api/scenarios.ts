import type { ExposureAnalysisResult } from '../../types/exposure'
import type { HazardScenarioResult } from '../../types/hazard'
import type { RiskAnalysisResult } from '../../types/risk'
import type { RouteAnalysisResult } from '../../types/routing'
import type {
  ComparisonAnalysisType,
  Scenario,
  ScenarioComparison,
  ScenarioLayerOverrideResult,
  ScenarioState,
} from '../../types/scenario'
import { apiFetch } from './client'

export async function listScenarios(twinId: string, status?: string): Promise<Scenario[]> {
  const qs = status ? `?status=${status}` : ''
  return apiFetch<Scenario[]>(`/digital-twins/${twinId}/scenarios${qs}`)
}

export async function createScenario(twinId: string, name: string, description?: string): Promise<Scenario> {
  return apiFetch<Scenario>(`/digital-twins/${twinId}/scenarios`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
}

export async function getScenarioState(scenarioId: string): Promise<ScenarioState> {
  return apiFetch<ScenarioState>(`/scenarios/${scenarioId}/state`)
}

export async function archiveScenario(scenarioId: string): Promise<Scenario> {
  return apiFetch<Scenario>(`/scenarios/${scenarioId}/archive`, { method: 'POST' })
}

export async function addLayerOverride(
  scenarioId: string,
  datasetId: string,
  reason?: string,
): Promise<ScenarioLayerOverrideResult> {
  return apiFetch<ScenarioLayerOverrideResult>(`/scenarios/${scenarioId}/layer-overrides`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id: datasetId, reason }),
  })
}

export async function deleteLayerOverride(overrideId: string): Promise<void> {
  await apiFetch<void>(`/scenario-layer-overrides/${overrideId}`, { method: 'DELETE' })
}

export interface ScenarioRouteRunInput {
  name: string
  description?: string
  road_dataset_id?: string
  hazard_dataset_id?: string
  risk_analysis_id?: string
  origin: { lon: number; lat: number }
  destination: { lon: number; lat: number }
  hazard_penalty_weight: number
  block_threshold?: number
  node_snap_tolerance_m?: number
  max_snap_distance_m?: number
  blocked_segment_geometries?: unknown[]
  match_buffer_m?: number
}

export async function runScenarioRoute(scenarioId: string, input: ScenarioRouteRunInput): Promise<RouteAnalysisResult> {
  return apiFetch<RouteAnalysisResult>(`/scenarios/${scenarioId}/route-runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export interface ScenarioExposureRunInput {
  name: string
  description?: string
  hazard_dataset_id?: string
  exposure_dataset_id: string
  population_field?: string
}

export async function runScenarioExposure(
  scenarioId: string,
  input: ScenarioExposureRunInput,
): Promise<ExposureAnalysisResult> {
  return apiFetch<ExposureAnalysisResult>(`/scenarios/${scenarioId}/exposure-runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export interface ScenarioRiskRunInput {
  name: string
  description?: string
  exposure_analysis_id: string
  vulnerability_weight: number
  consequence_weight: number
  risk_breakpoints?: number[]
}

export async function runScenarioRisk(scenarioId: string, input: ScenarioRiskRunInput): Promise<RiskAnalysisResult> {
  return apiFetch<RiskAnalysisResult>(`/scenarios/${scenarioId}/risk-runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export interface ScenarioFloodRunInput {
  name: string
  description?: string
  dem_dataset_id?: string
  depth_above_drainage_m: number
  channel_threshold_cells?: number
  rainfall_dataset_id?: string
}

export async function runScenarioFlood(scenarioId: string, input: ScenarioFloodRunInput): Promise<HazardScenarioResult> {
  return apiFetch<HazardScenarioResult>(`/scenarios/${scenarioId}/hazard-runs/flood`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export interface ScenarioLandslideRunInput {
  name: string
  description?: string
  dem_dataset_id?: string
  slope_breakpoints_deg?: number[]
  rainfall_context?: string
  rainfall_dataset_id?: string
}

export async function runScenarioLandslide(
  scenarioId: string,
  input: ScenarioLandslideRunInput,
): Promise<HazardScenarioResult> {
  return apiFetch<HazardScenarioResult>(`/scenarios/${scenarioId}/hazard-runs/landslide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export async function compareScenarioAnalyses(
  analysisType: ComparisonAnalysisType,
  leftAnalysisId: string,
  rightAnalysisId: string,
): Promise<ScenarioComparison> {
  return apiFetch<ScenarioComparison>('/scenario-comparisons', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      analysis_type: analysisType,
      left_analysis_id: leftAnalysisId,
      right_analysis_id: rightAnalysisId,
    }),
  })
}
