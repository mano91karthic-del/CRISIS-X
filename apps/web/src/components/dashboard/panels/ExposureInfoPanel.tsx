import type { ExposureAnalysis } from '../../../types/exposure'
import { EmptyState } from '../states/EmptyState'

export function ExposureInfoPanel({ exposure }: { exposure: ExposureAnalysis | null }) {
  if (!exposure) return <EmptyState title="No exposure analysis yet" description="Run an exposure analysis to see it here." />

  const byClass = exposure.results.by_class ?? {}

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div>
        <h4 className="font-semibold text-slate-200">{exposure.name}</h4>
        <p className="text-xs text-slate-500">
          {exposure.hazard_dataset_type} × {exposure.exposure_dataset_type} · {exposure.status}
        </p>
      </div>
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-slate-500">
            <th className="pb-1 font-medium">Class</th>
            <th className="pb-1 font-medium">Count</th>
            <th className="pb-1 font-medium">Length (m)</th>
            <th className="pb-1 font-medium">Area (m²)</th>
            <th className="pb-1 font-medium">Population</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(byClass).map(([label, entry]) => (
            <tr key={label} className="border-t border-slate-800 text-slate-300">
              <td className="py-1">{label}</td>
              <td>{entry.count ?? '—'}</td>
              <td>{entry.length_m?.toFixed(1) ?? '—'}</td>
              <td>{entry.area_m2?.toFixed(1) ?? '—'}</td>
              <td>{entry.population_sum?.toFixed(1) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {exposure.error_message && (
        <p className="rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-300">{exposure.error_message}</p>
      )}
      {(exposure.results.limitations ?? []).length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/40 p-2">
          <h5 className="mb-1 text-xs font-semibold uppercase text-amber-400">Limitations</h5>
          <ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">
            {exposure.results.limitations!.map((lim) => (
              <li key={lim}>{lim}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
