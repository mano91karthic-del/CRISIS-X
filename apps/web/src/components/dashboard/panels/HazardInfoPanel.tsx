import type { HazardScenario } from '../../../types/hazard'
import { EmptyState } from '../states/EmptyState'

export interface HazardInfoPanelProps {
  hazard: HazardScenario | null
  /** The output Dataset's provenance.limitations -- HazardScenario
   * itself has no `results` column (see app/models/hazard_scenario.py);
   * limitations live on the produced Dataset's provenance, never
   * duplicated onto the scenario record.
   */
  limitations?: string[]
}

export function HazardInfoPanel({ hazard, limitations = [] }: HazardInfoPanelProps) {
  if (!hazard) return <EmptyState title="No hazard scenario yet" description="Run a flood or landslide scenario to see it here." />

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div>
        <h4 className="font-semibold text-slate-200">{hazard.name}</h4>
        <p className="text-xs text-slate-500">
          {hazard.hazard_type} · {hazard.status}
        </p>
      </div>
      <div>
        <h5 className="mb-1 text-xs font-semibold uppercase text-slate-500">Parameters used</h5>
        <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-xs text-slate-300">
          {Object.entries(hazard.parameters).map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-slate-500">{key}</dt>
              <dd>{JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      </div>
      {hazard.error_message && (
        <p className="rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-300">{hazard.error_message}</p>
      )}
      {limitations.length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/40 p-2">
          <h5 className="mb-1 text-xs font-semibold uppercase text-amber-400">Limitations</h5>
          <ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">
            {limitations.map((lim) => (
              <li key={lim}>{lim}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
