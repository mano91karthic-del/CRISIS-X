import { useCallback, useEffect, useState } from 'react'
import { listScenarios } from '../lib/api/scenarios'
import type { Scenario } from '../types/scenario'

export interface UseScenarioListResult {
  scenarios: Scenario[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useScenarioList(twinId: string | null): UseScenarioListResult {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    if (!twinId) {
      setScenarios([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    listScenarios(twinId)
      .then((result) => {
        if (!cancelled) setScenarios(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load scenarios')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [twinId, refreshToken])

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), [])

  return { scenarios, loading, error, refresh }
}
