import type { AssistantQueryRequest, AssistantQueryResponse } from '../../types/assistant'
import { apiFetch } from './client'

/** Phase 12: read-only -- see apps/api/app/api/assistant.py. Performs
 * no dataset/scenario/twin mutation.
 */
export async function queryAssistant(
  projectId: string,
  request: AssistantQueryRequest,
): Promise<AssistantQueryResponse> {
  return apiFetch<AssistantQueryResponse>(`/projects/${projectId}/assistant/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}
