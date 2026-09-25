import { useState } from 'react'
import { compareScenarioAnalyses } from '../../lib/api/scenarios'
import type { ComparisonAnalysisType, ScenarioComparison } from '../../types/scenario'
import { LoadingState } from './states/LoadingState'
import { ErrorBanner } from './states/ErrorBanner'
import { EmptyState } from './states/EmptyState'

export interface AnalysisOption {
  id: string
  label: string
}

export interface ScenarioComparisonPanelProps {
  analysesByType: Record<ComparisonAnalysisType, AnalysisOption[]>
}

function renderDeltaCell(value: unknown): string {
  if (value && typeof value === 'object' && 'delta' in (value as Record<string, unknown>)) {
    const entry = value as { left: number; right: number; delta: number; pct_change: number | null }
    const pct = entry.pct_change === null ? '—' : `${(entry.pct_change * 100).toFixed(1)}%`
    return `${entry.left} → ${entry.right} (Δ ${entry.delta >= 0 ? '+' : ''}${entry.delta.toFixed(2)}, ${pct})`
  }
  if (value && typeof value === 'object' && 'changed' in (value as Record<string, unknown>)) {
    const entry = value as { left: string; right: string; changed: boolean }
    return `${entry.left} → ${entry.right}${entry.changed ? ' (changed)' : ''}`
  }
  return JSON.stringify(value)
}

/** Renders exactly the `diff` object the backend's /scenario-comparisons
 * response returns -- generic key-walk because the diff shape differs by
 * analysis_type (per-hazard-class for exposure/risk, per-route-metric
 * for route). Never recomputes a number client-side.
 */
function DiffTable({ diff }: { diff: Record<string, unknown> }) {
  return (
    <div className="flex flex-col gap-2">
      {Object.entries(diff).map(([groupKey, groupValue]) => (
        <div key={groupKey} className="rounded border border-slate-800 p-2 text-xs">
          <p className="mb-1 font-semibold text-slate-300">{groupKey}</p>
          {typeof groupValue === 'object' && groupValue !== null ? (
            <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-slate-400">
              {Object.entries(groupValue as Record<string, unknown>).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt>{k}</dt>
                  <dd>{renderDeltaCell(v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <span>{String(groupValue)}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export function ScenarioComparisonPanel({ analysesByType }: ScenarioComparisonPanelProps) {
  const [analysisType, setAnalysisType] = useState<ComparisonAnalysisType>('risk')
  const [leftId, setLeftId] = useState('')
  const [rightId, setRightId] = useState('')
  const [comparison, setComparison] = useState<ScenarioComparison | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const options = analysesByType[analysisType] ?? []

  async function runComparison() {
    if (!leftId || !rightId) return
    setLoading(true)
    setError(null)
    try {
      const result = await compareScenarioAnalyses(analysisType, leftId, rightId)
      setComparison(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Comparison failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-xs text-slate-400">
          Type
          <select
            value={analysisType}
            onChange={(e) => {
              setAnalysisType(e.target.value as ComparisonAnalysisType)
              setLeftId('')
              setRightId('')
              setComparison(null)
            }}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
          >
            <option value="exposure">Exposure</option>
            <option value="risk">Risk</option>
            <option value="route">Route</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-slate-400">
          Left (baseline)
          <select
            value={leftId}
            onChange={(e) => setLeftId(e.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
          >
            <option value="">Select…</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-slate-400">
          Right (scenario)
          <select
            value={rightId}
            onChange={(e) => setRightId(e.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
          >
            <option value="">Select…</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={runComparison}
          disabled={!leftId || !rightId || loading}
          className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
        >
          Compare
        </button>
      </div>

      {loading && <LoadingState label="Computing comparison…" />}
      {error && <ErrorBanner message={error} onRetry={runComparison} />}

      {comparison && (
        <div className="flex flex-col gap-2">
          {comparison.comparability_warnings.length > 0 && (
            <div className="rounded border border-amber-900 bg-amber-950/40 p-2 text-xs text-amber-300">
              {comparison.comparability_warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          )}
          <DiffTable diff={comparison.diff} />
        </div>
      )}

      {!comparison && !loading && !error && (
        <EmptyState title="No comparison yet" description="Pick two analyses of the same type and compare." />
      )}
    </div>
  )
}
