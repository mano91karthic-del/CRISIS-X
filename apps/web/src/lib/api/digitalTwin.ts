import type { DigitalTwin, DigitalTwinState, TwinLayerResult } from '../../types/digitalTwin'
import { apiFetch } from './client'

export async function getDigitalTwinByProject(projectId: string): Promise<DigitalTwin | null> {
  try {
    return await apiFetch<DigitalTwin>(`/projects/${projectId}/digital-twin`)
  } catch (err) {
    if (err instanceof Error && err.message.includes('404')) return null
    throw err
  }
}

export async function createDigitalTwin(projectId: string, name: string, description?: string): Promise<DigitalTwin> {
  return apiFetch<DigitalTwin>(`/projects/${projectId}/digital-twin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
}

export async function getDigitalTwinState(twinId: string): Promise<DigitalTwinState> {
  return apiFetch<DigitalTwinState>(`/digital-twins/${twinId}/state`)
}

export async function listTwinLayers(
  twinId: string,
  params?: { category?: string; dataset_type?: string; status?: string },
): Promise<TwinLayerResult[]> {
  const query = new URLSearchParams()
  if (params?.category) query.set('category', params.category)
  if (params?.dataset_type) query.set('dataset_type', params.dataset_type)
  if (params?.status) query.set('status', params.status)
  const qs = query.toString()
  return apiFetch<TwinLayerResult[]>(`/digital-twins/${twinId}/layers${qs ? `?${qs}` : ''}`)
}

/** Registers an already-validated Dataset as a Digital Twin layer --
 * thin wrapper around the existing POST /digital-twins/{id}/layers
 * backend endpoint (app/api/digital_twin.py::register_twin_layer), which
 * previously had no frontend caller anywhere in the app. Registering a
 * second dataset of the same dataset_type automatically supersedes the
 * twin's current active layer of that type -- existing backend behavior,
 * not introduced here.
 */
export async function registerTwinLayer(twinId: string, datasetId: string): Promise<TwinLayerResult> {
  return apiFetch<TwinLayerResult>(`/digital-twins/${twinId}/layers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id: datasetId }),
  })
}
