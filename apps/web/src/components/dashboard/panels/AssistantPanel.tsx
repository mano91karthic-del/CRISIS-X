import { useState } from 'react'
import { useDashboardStore } from '../../../state/dashboardStore'
import { useAssistant } from '../../../hooks/useAssistant'
import { ErrorBanner } from '../states/ErrorBanner'
import { EmptyState } from '../states/EmptyState'

/** Phase 12: read-only AI Assistant panel. Reads existing dashboard
 * context (projectId/twinId/scenarioId/activeFeature) straight from the
 * Zustand store -- no new global state is introduced (see
 * hooks/useAssistant.ts for the per-question answer/evidence state,
 * which is local to this panel, matching every other InfoPanelDock tab).
 *
 * Layout is Answer-first (dominant), with Relevant limitations and
 * Unavailable information as their own small labeled sections, and the
 * per-tool Evidence/Sources list collapsed by default (<details>) --
 * available for transparency without competing with the answer for
 * attention.
 */
export function AssistantPanel() {
  const projectId = useDashboardStore((s) => s.projectId)
  const twinId = useDashboardStore((s) => s.twinId)
  const scenarioId = useDashboardStore((s) => s.scenarioId)
  const activeFeature = useDashboardStore((s) => s.activeFeature)

  const { answer, evidence, unavailable, relevantLimitations, loading, error, ask } = useAssistant({
    projectId,
    twinId,
    scenarioId,
    activeFeature: activeFeature
      ? { dataset_id: activeFeature.datasetId, feature_id: activeFeature.featureId, properties: activeFeature.properties }
      : null,
  })

  const [question, setQuestion] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed) return
    void ask(trimmed)
  }

  if (!projectId) {
    return <EmptyState title="No project selected" description="Select a project to ask the assistant about it." />
  }

  return (
    <div className="flex h-full flex-col gap-3 p-3 text-sm">
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <label className="text-xs font-semibold uppercase tracking-wide text-slate-500" htmlFor="assistant-question">
          Ask about this project
        </label>
        <textarea
          id="assistant-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          placeholder="e.g. Which areas have the highest landslide risk?"
          className="w-full resize-none rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600"
        />
        {activeFeature && (
          <p className="text-[11px] text-slate-500">Using the selected feature ({activeFeature.datasetId}) as context.</p>
        )}
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="self-start rounded bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
        >
          {loading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {error && <ErrorBanner message={error} />}

      {loading && <p className="text-xs text-slate-500">Gathering evidence and generating an answer…</p>}

      {!loading && answer && (
        <div className="flex flex-col gap-3 overflow-y-auto">
          {/* Answer: the dominant section -- larger, higher-contrast text than everything below it. */}
          <div className="rounded-md border border-emerald-900/60 bg-slate-900 p-3">
            <p data-testid="assistant-answer" className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">
              {answer}
            </p>
          </div>

          {relevantLimitations.length > 0 && (
            <div className="rounded border border-amber-900 bg-amber-950/40 p-2">
              <h5 className="mb-1 text-xs font-semibold uppercase text-amber-400">Relevant limitations</h5>
              <ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">
                {relevantLimitations.map((lim) => (
                  <li key={lim}>{lim}</li>
                ))}
              </ul>
            </div>
          )}

          {unavailable.length > 0 && (
            <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
              <h5 className="mb-1 text-xs font-semibold uppercase text-slate-400">Unavailable information</h5>
              <ul className="list-disc space-y-1 pl-4 text-xs text-slate-400">
                {unavailable.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {evidence.length > 0 && (
            <details className="rounded border border-slate-800" open>
              <summary className="cursor-pointer select-none px-2 py-1.5 text-xs font-semibold uppercase text-slate-500 hover:text-slate-300">
                Evidence / sources ({evidence.length})
              </summary>
              <ul className="flex flex-col gap-2 p-2 pt-0">
                {evidence.map((item, index) => (
                  <li key={`${item.tool}-${index}`} className="rounded border border-slate-800 p-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[11px] text-slate-300">{item.tool}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                          item.status === 'success'
                            ? 'bg-emerald-900/60 text-emerald-300'
                            : item.status === 'unavailable'
                              ? 'bg-slate-800 text-slate-400'
                              : 'bg-red-900/60 text-red-300'
                        }`}
                      >
                        {item.status}
                      </span>
                    </div>
                    {item.detail && <p className="mt-1 text-[11px] text-slate-500">{item.detail}</p>}
                    {item.limitations.length > 0 && (
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-amber-200">
                        {item.limitations.map((lim) => (
                          <li key={lim}>{lim}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {!loading && !answer && !error && (
        <EmptyState
          title="Ask a question"
          description="The assistant answers only from CRISIS-X's own computed hazard, exposure, risk, route, and twin data -- it will say so if something hasn't been computed yet."
        />
      )}
    </div>
  )
}
