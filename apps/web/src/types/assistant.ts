/** Phase 12: mirrors app/schemas/assistant.py field-for-field. */

export interface AssistantFeatureContext {
  dataset_id: string | null
  feature_id: string | number | null
  properties: Record<string, unknown>
}

export interface AssistantQueryRequest {
  question: string
  project_id: string
  twin_id?: string | null
  scenario_id?: string | null
  active_feature?: AssistantFeatureContext | null
}

export type AssistantEvidenceStatus = 'success' | 'unavailable' | 'error'

/** One read-only tool call's result -- `data` shape varies per `tool`
 * (see apps/api/app/services/assistant/tools.py), so it's left as
 * `unknown` here rather than guessed at; the panel renders it generically.
 */
export interface AssistantEvidence {
  tool: string
  status: AssistantEvidenceStatus
  data: unknown
  limitations: string[]
  provenance: Record<string, unknown> | null
  detail: string | null
}

export interface AssistantQueryResponse {
  answer: string
  evidence: AssistantEvidence[]
  unavailable: string[]
  /** The (small) subset of evidence limitations judged relevant to this
   * question -- already woven into `answer`'s prose; shown again here
   * as its own labeled section for scannability. Never every
   * limitation across all evidence.
   */
  relevant_limitations: string[]
}
