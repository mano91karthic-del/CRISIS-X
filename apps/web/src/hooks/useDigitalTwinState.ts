import { useCallback, useEffect, useState } from 'react'
import { getDigitalTwinState } from '../lib/api/digitalTwin'
import type { DigitalTwinState } from '../types/digitalTwin'

export interface UseDigitalTwinStateResult {
  state: DigitalTwinState | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useDigitalTwinState(twinId: string | null): UseDigitalTwinStateResult {
  const [state, setState] = useState<DigitalTwinState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    if (!twinId) {
      setState(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getDigitalTwinState(twinId)
      .then((result) => {
        if (!cancelled) setState(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load digital twin state')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [twinId, refreshToken])

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), [])

  return { state, loading, error, refresh }
}
