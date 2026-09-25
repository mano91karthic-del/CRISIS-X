// Phase 11: shared fetch helper for the Command Dashboard's new API
// calls. Deliberately a separate file from lib/api.ts (untouched, Data
// Hub-only) -- same apiFetch<T> pattern, not a new HTTP client library.

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new ApiError(
      `${init?.method ?? 'GET'} ${path} failed: ${res.status}${detail ? ` — ${detail}` : ''}`,
      res.status,
    )
  }
  return res.json() as Promise<T>
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}
