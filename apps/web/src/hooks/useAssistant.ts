import { useCallback, useState } from 'react'
import { queryAssistant } from '../lib/api/assistant'
import type { AssistantEvidence, AssistantFeatureContext } from '../types/assistant'

export interface UseAssistantResult {
  answer: string | null
  evidence: AssistantEvidence[]
  unavailable: string[]
  relevantLimitations: string[]
  loading: boolean
  error: string | null
  ask: (question: string) => Promise<void>
}

export interface AssistantContext {
  projectId: string | null
  twinId?: string | null
  scenarioId?: string | null
  activeFeature?: AssistantFeatureContext | null
}

/** Phase 12: submits a question to the read-only AI Assistant endpoint.
 * Follows the same {loading, error} shape as the dashboard's other
 * fetch hooks (see useDigitalTwinState.ts) -- one difference is this is
 * submit-triggered (`ask`), not effect-triggered on mount, since a
 * question only fires on explicit user action.
 */
export function useAssistant(context: AssistantContext): UseAssistantResult {
  const [answer, setAnswer] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<AssistantEvidence[]>([])
  const [unavailable, setUnavailable] = useState<string[]>([])
  const [relevantLimitations, setRelevantLimitations] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ask = useCallback(
    async (question: string) => {
      if (loading) return // prevent duplicate submissions while a question is in flight
      if (!context.projectId) {
        setError('No project selected')
        return
      }
      setLoading(true)
      setError(null)
      try {
        const result = await queryAssistant(context.projectId, {
          question,
          project_id: context.projectId,
          twin_id: context.twinId ?? null,
          scenario_id: context.scenarioId ?? null,
          active_feature: context.activeFeature ?? null,
        })
        setAnswer(result.answer)
        setEvidence(result.evidence)
        setUnavailable(result.unavailable)
        setRelevantLimitations(result.relevant_limitations)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to reach the assistant')
      } finally {
        setLoading(false)
      }
    },
    [loading, context.projectId, context.twinId, context.scenarioId, context.activeFeature],
  )

  return { answer, evidence, unavailable, relevantLimitations, loading, error, ask }
}
