import { useEffect, useState } from 'react'
import { listHazardScenarioDatasets } from '../lib/api/analyses'

/** HazardScenario has no `results`/`limitations` of its own (see
 * app/models/hazard_scenario.py) -- limitations live on its output
 * Dataset's `provenance.limitations`. This fetches that output dataset
 * whenever the hazard scenario id changes, purely to surface the
 * disclosure text HazardInfoPanel needs -- never re-derives it.
 */
export function useHazardLimitations(hazardScenarioId: string | null): string[] {
  const [limitations, setLimitations] = useState<string[]>([])

  useEffect(() => {
    if (!hazardScenarioId) {
      setLimitations([])
      return
    }
    let cancelled = false
    listHazardScenarioDatasets(hazardScenarioId)
      .then((datasets) => {
        if (cancelled) return
        const found = datasets.find((d) => Array.isArray(d.provenance?.limitations))
        setLimitations((found?.provenance?.limitations as string[]) ?? [])
      })
      .catch(() => {
        if (!cancelled) setLimitations([])
      })
    return () => {
      cancelled = true
    }
  }, [hazardScenarioId])

  return limitations
}
