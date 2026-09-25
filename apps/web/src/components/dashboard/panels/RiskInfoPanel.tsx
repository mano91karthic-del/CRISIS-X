import type { RiskAnalysis } from '../../../types/risk'
import { colorForLabel } from '../../../geo/legendColors'
import { EmptyState } from '../states/EmptyState'

export function RiskInfoPanel({ risk }: { risk: RiskAnalysis | null }) {
  if (!risk) return <EmptyState title="No risk analysis yet" description="Run a risk analysis to see it here." />

  const byClass = risk.results.by_class ?? {}

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div>
        <h4 className="font-semibold text-slate-200">{risk.name}</h4>
        <p className="text-xs text-slate-500">{risk.status}</p>
      </div>
      <div className="rounded border border-slate-800 bg-slate-900/50 p-2 text-xs">
        <p className="mb-1 font-semibold uppercase text-slate-500">User-declared assumptions (not measured facts)</p>
        <p className="text-slate-300">vulnerability_weight = {risk.vulnerability_weight}</p>
        <p className="text-slate-300">consequence_weight = {risk.consequence_weight}</p>
      </div>
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-slate-500">
            <th className="pb-1 font-medium">Class</th>
            <th className="pb-1 font-medium">risk_score</th>
            <th className="pb-1 font-medium">risk_class</th>
            <th className="pb-1 font-medium">Exposure qty (unchanged)</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(byClass).map(([label, entry]) => (
            <tr key={label} className="border-t border-slate-800 text-slate-300">
              <td className="py-1">{label}</td>
              <td>{entry.risk_score.toFixed(3)}</td>
              <td>
                <span className="inline-flex items-center gap-1">
                  <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: colorForLabel(entry.risk_class) }} />
                  {entry.risk_class}
                </span>
              </td>
              <td>{entry.count ?? entry.length_m ?? entry.area_m2 ?? entry.population_sum ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {risk.error_message && (
        <p className="rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-300">{risk.error_message}</p>
      )}
      {(risk.results.limitations ?? []).length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/40 p-2">
          <h5 className="mb-1 text-xs font-semibold uppercase text-amber-400">Limitations</h5>
          <ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">
            {risk.results.limitations!.map((lim) => (
              <li key={lim}>{lim}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
