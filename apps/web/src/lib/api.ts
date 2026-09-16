import type { Dataset, DatasetType, Project } from '../types/dataHub'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export interface HealthResponse {
  status: string
  service: string
  env: string
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/health`)
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status}`)
  }
  return res.json()
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${res.status}${detail ? ` — ${detail}` : ''}`)
  }
  return res.json() as Promise<T>
}

export async function listProjects(): Promise<Project[]> {
  return apiFetch<Project[]>('/projects')
}

export async function createProject(input: { name: string; description?: string }): Promise<Project> {
  return apiFetch<Project>('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export async function listDatasets(projectId: string): Promise<Dataset[]> {
  return apiFetch<Dataset[]>(`/projects/${projectId}/datasets`)
}

export async function uploadDataset(
  projectId: string,
  datasetType: DatasetType,
  file: File,
): Promise<Dataset> {
  const form = new FormData()
  form.append('dataset_type', datasetType)
  form.append('file', file)
  return apiFetch<Dataset>(`/projects/${projectId}/datasets`, {
    method: 'POST',
    body: form,
  })
}

export async function deleteDataset(datasetId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/datasets/${datasetId}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new Error(`DELETE /datasets/${datasetId} failed: ${res.status}`)
  }
}

export function datasetDownloadUrl(datasetId: string): string {
  return `${API_BASE_URL}/datasets/${datasetId}/download`
}
