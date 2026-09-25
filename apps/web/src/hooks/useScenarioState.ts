import { useCallback, useEffect, useState } from 'react'
import { getScenarioState } from '../lib/api/scenarios'
import type { ScenarioState } from '../types/scenario'

export interface UseScenarioStateResult {
  state: ScenarioState | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useScenarioState(scenarioId: string | null): UseScenarioStateResult {
  const [state, setState] = useState<ScenarioState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    if (!scenarioId) {
      setState(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getScenarioState(scenarioId)
      .then((result) => {
        if (!cancelled) setState(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load scenario state')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [scenarioId, refreshToken])

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), [])

  return { state, loading, error, refresh }
}
