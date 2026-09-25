import type { RouteAnalysis, RouteSummary } from '../../../types/routing'
import { EmptyState } from '../states/EmptyState'

function RouteBadge({ label, route }: { label: string; route: RouteSummary | undefined }) {
  if (!route) return null
  return (
    <div className="rounded border border-slate-800 bg-slate-900/50 p-2 text-xs">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold text-slate-300">{label}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${route.feasible ? 'bg-emerald-900 text-emerald-300' : 'bg-red-900 text-red-300'}`}>
          {route.feasible ? 'feasible' : 'infeasible'}
        </span>
      </div>
      {route.feasible && (
        <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-slate-400">
          <dt>distance_m</dt>
          <dd>{route.distance_m?.toFixed(1)}</dd>
          <dt>hazard_component_m</dt>
          <dd>{route.hazard_component_m?.toFixed(1)}</dd>
          <dt>cost_m_equivalent</dt>
          <dd>{route.cost_m_equivalent?.toFixed(1)}</dd>
        </dl>
      )}
    </div>
  )
}

export function RouteInfoPanel({ route }: { route: RouteAnalysis | null }) {
  if (!route) return <EmptyState title="No route analysis yet" description="Run a route analysis to see it here." />

  const results = route.results

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div>
        <h4 className="font-semibold text-slate-200">{route.name}</h4>
        <p className="text-xs text-slate-500">{route.status}</p>
      </div>
      {results.disconnected_due_to_blocking && (
        <p className="rounded border border-amber-900 bg-amber-950/40 px-2 py-1 text-xs text-amber-300">
          No route exists because required segments are blocked (disconnected_due_to_blocking).
        </p>
      )}
      <RouteBadge label="Shortest route" route={results.shortest_route} />
      <RouteBadge label="Hazard-aware route" route={results.hazard_aware_route} />
      {results.graph_summary && (
        <div className="rounded border border-slate-800 bg-slate-900/50 p-2 text-xs text-slate-400">
          <p>node_count = {results.graph_summary.node_count}</p>
          <p>edge_count = {results.graph_summary.edge_count}</p>
          <p>blocked_edge_count = {results.graph_summary.blocked_edge_count}</p>
        </div>
      )}
      {results.scenario_blocking && (
        <div className="rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-300">
          <p>Scenario blocking active: {results.scenario_blocking.resolved_edge_count} edge(s) blocked</p>
        </div>
      )}
      {route.error_message && (
        <p className="rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-300">{route.error_message}</p>
      )}
      {(results.limitations ?? []).length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/40 p-2">
          <h5 className="mb-1 text-xs font-semibold uppercase text-amber-400">Limitations</h5>
          <ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">
            {results.limitations!.map((lim) => (
              <li key={lim}>{lim}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
