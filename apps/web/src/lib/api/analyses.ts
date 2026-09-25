import type { DatasetFull } from '../../types/dataset'
import type { ExposureAnalysis } from '../../types/exposure'
import type { HazardScenario } from '../../types/hazard'
import type { RiskAnalysis } from '../../types/risk'
import type { RouteAnalysis } from '../../types/routing'
import { apiFetch } from './client'

// Thin read helpers reused by the dashboard's info panels and the
// scenario comparison dropdown -- read-only wrappers over the Phase
// 4/6/7/8 endpoints, no new backend surface.

export async function getHazardScenario(id: string): Promise<HazardScenario> {
  return apiFetch<HazardScenario>(`/hazard-scenarios/${id}`)
}

export async function getExposureAnalysis(id: string): Promise<ExposureAnalysis> {
  return apiFetch<ExposureAnalysis>(`/exposure-analyses/${id}`)
}

export async function getRiskAnalysis(id: string): Promise<RiskAnalysis> {
  return apiFetch<RiskAnalysis>(`/risk-analyses/${id}`)
}

export async function getRouteAnalysis(id: string): Promise<RouteAnalysis> {
  return apiFetch<RouteAnalysis>(`/route-analyses/${id}`)
}

// Project-scoped listings, used to populate the baseline (no-scenario)
// info panels and the comparison dropdown's "left/baseline" options.
export async function listHazardScenarios(projectId: string): Promise<HazardScenario[]> {
  return apiFetch<HazardScenario[]>(`/projects/${projectId}/hazard-scenarios`)
}

export async function listExposureAnalyses(projectId: string): Promise<ExposureAnalysis[]> {
  return apiFetch<ExposureAnalysis[]>(`/projects/${projectId}/exposure-analyses`)
}

export async function listRiskAnalyses(projectId: string): Promise<RiskAnalysis[]> {
  return apiFetch<RiskAnalysis[]>(`/projects/${projectId}/risk-analyses`)
}

export async function listRouteAnalyses(projectId: string): Promise<RouteAnalysis[]> {
  return apiFetch<RouteAnalysis[]>(`/projects/${projectId}/route-analyses`)
}

/** HazardScenario itself carries no `results`/`limitations` field (see
 * app/models/hazard_scenario.py) -- limitations live on the output
 * Dataset's `provenance`, reused here rather than duplicated. Needed by
 * HazardInfoPanel, which otherwise has no way to surface them.
 */
export async function listHazardScenarioDatasets(hazardScenarioId: string): Promise<DatasetFull[]> {
  return apiFetch<DatasetFull[]>(`/hazard-scenarios/${hazardScenarioId}/datasets`)
}
