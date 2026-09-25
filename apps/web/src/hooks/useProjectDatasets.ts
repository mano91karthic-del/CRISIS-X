import { useCallback, useEffect, useState } from 'react'
import { listDatasets } from '../lib/api'
import type { Dataset } from '../types/dataHub'

export interface UseProjectDatasetsResult {
  datasets: Dataset[]
  loading: boolean
  error: string | null
  refresh: () => void
}

/** Read-only reuse of lib/api.ts's existing listDatasets (the same call
 * the Data Hub page already makes) -- no new backend endpoint, no edit
 * to lib/api.ts. Lets the Command Dashboard see a project's full dataset
 * list (including ones not yet registered as a Digital Twin layer)
 * without duplicating Data Hub's own fetching logic.
 */
export function useProjectDatasets(projectId: string | null): UseProjectDatasetsResult {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    if (!projectId) {
      setDatasets([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    listDatasets(projectId)
      .then((result) => {
        if (!cancelled) setDatasets(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load datasets')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, refreshToken])

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), [])

  return { datasets, loading, error, refresh }
}
