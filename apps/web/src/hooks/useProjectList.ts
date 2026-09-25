import { useEffect, useState } from 'react'
import { listProjects } from '../lib/api'
import type { Project } from '../types/dataHub'

export interface UseProjectListResult {
  projects: Project[]
  loading: boolean
  error: string | null
}

/** Read-only reuse of lib/api.ts's existing listProjects -- no new
 * backend call, no edit to lib/api.ts.
 */
export function useProjectList(): UseProjectListResult {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listProjects()
      .then((result) => {
        if (!cancelled) setProjects(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load projects')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { projects, loading, error }
}
