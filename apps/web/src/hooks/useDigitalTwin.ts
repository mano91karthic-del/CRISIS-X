import { useCallback, useEffect, useState } from 'react'
import { getDigitalTwinByProject } from '../lib/api/digitalTwin'
import type { DigitalTwin } from '../types/digitalTwin'

export interface UseDigitalTwinResult {
  twin: DigitalTwin | null
  loading: boolean
  error: string | null
  refresh: () => void
}

/** Resolves the (at most one, per Phase 9's uniqueness constraint)
 * DigitalTwin for a project. `twin === null` after loading means no
 * twin has been created yet for this project -- a real, distinct empty
 * state, not an error.
 */
export function useDigitalTwin(projectId: string | null): UseDigitalTwinResult {
  const [twin, setTwin] = useState<DigitalTwin | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    if (!projectId) {
      setTwin(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getDigitalTwinByProject(projectId)
      .then((result) => {
        if (!cancelled) setTwin(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load digital twin')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, refreshToken])

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), [])

  return { twin, loading, error, refresh }
}
