import { useEffect, useState } from 'react'
import { listExposureAnalyses, listHazardScenarios, listRiskAnalyses, listRouteAnalyses } from '../lib/api/analyses'
import type { ExposureAnalysis } from '../types/exposure'
import type { HazardScenario } from '../types/hazard'
import type { RiskAnalysis } from '../types/risk'
import type { RouteAnalysis } from '../types/routing'

export interface ProjectAnalyses {
  hazardScenarios: HazardScenario[]
  exposureAnalyses: ExposureAnalysis[]
  riskAnalyses: RiskAnalysis[]
  routeAnalyses: RouteAnalysis[]
  loading: boolean
}

/** All of a project's own (non-scenario) analyses -- populates the
 * baseline info panels when no scenario is selected, and the comparison
 * panel's "left/baseline" dropdown options.
 */
export function useProjectAnalyses(projectId: string | null): ProjectAnalyses {
  const [hazardScenarios, setHazardScenarios] = useState<HazardScenario[]>([])
  const [exposureAnalyses, setExposureAnalyses] = useState<ExposureAnalysis[]>([])
  const [riskAnalyses, setRiskAnalyses] = useState<RiskAnalysis[]>([])
  const [routeAnalyses, setRouteAnalyses] = useState<RouteAnalysis[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!projectId) {
      setHazardScenarios([])
      setExposureAnalyses([])
      setRiskAnalyses([])
      setRouteAnalyses([])
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([
      listHazardScenarios(projectId),
      listExposureAnalyses(projectId),
      listRiskAnalyses(projectId),
      listRouteAnalyses(projectId),
    ])
      .then(([hazards, exposures, risks, routes]) => {
        if (cancelled) return
        setHazardScenarios(hazards)
        setExposureAnalyses(exposures)
        setRiskAnalyses(risks)
        setRouteAnalyses(routes)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  return { hazardScenarios, exposureAnalyses, riskAnalyses, routeAnalyses, loading }
}
