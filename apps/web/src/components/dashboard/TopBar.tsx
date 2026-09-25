import { useDashboardStore } from '../../state/dashboardStore'
import type { Project } from '../../types/dataHub'
import type { DigitalTwin } from '../../types/digitalTwin'
import type { Scenario } from '../../types/scenario'

export interface TopBarProps {
  projects: Project[]
  selectedProjectId: string | null
  onSelectProject: (projectId: string) => void
  twin: DigitalTwin | null
  scenarios: Scenario[]
  selectedScenarioId: string | null
  onSelectScenario: (scenarioId: string | null) => void
  onCreateScenario: () => void
}

export function TopBar({
  projects,
  selectedProjectId,
  onSelectProject,
  twin,
  scenarios,
  selectedScenarioId,
  onSelectScenario,
  onCreateScenario,
}: TopBarProps) {
  const viewMode = useDashboardStore((s) => s.viewMode)
  const setViewMode = useDashboardStore((s) => s.setViewMode)
  const terrainExaggeration = useDashboardStore((s) => s.terrainExaggeration)
  const setTerrainExaggeration = useDashboardStore((s) => s.setTerrainExaggeration)

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-950 px-3 py-2">
      <label className="flex items-center gap-1 text-xs text-slate-400">
        Project
        <select
          value={selectedProjectId ?? ''}
          onChange={(e) => onSelectProject(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
        >
          <option value="" disabled>
            Select a project…
          </option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <span className="text-xs text-slate-500">
        Twin: {twin ? <span className="text-emerald-400">v{twin.version}</span> : <span className="text-slate-600">none</span>}
      </span>

      <label className="flex items-center gap-1 text-xs text-slate-400">
        Scenario
        <select
          value={selectedScenarioId ?? ''}
          onChange={(e) => onSelectScenario(e.target.value === '' ? null : e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
          disabled={!twin}
        >
          <option value="">Baseline (no scenario)</option>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        onClick={onCreateScenario}
        disabled={!twin}
        className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-900 disabled:opacity-40"
      >
        + New Scenario
      </button>

      <div className="ml-auto flex items-center gap-2">
        {viewMode === '3d' && (
          <label className="flex items-center gap-1 text-xs text-slate-400">
            Terrain ×
            <input
              type="range"
              min={1}
              max={3}
              step={0.1}
              value={terrainExaggeration}
              onChange={(e) => setTerrainExaggeration(Number(e.target.value))}
              className="accent-emerald-500"
            />
            <span className="w-8 text-slate-300">{terrainExaggeration.toFixed(1)}</span>
          </label>
        )}
        <div role="tablist" className="flex overflow-hidden rounded border border-slate-700">
          {(['2d', '3d'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={viewMode === mode}
              onClick={() => setViewMode(mode)}
              className={`px-3 py-1 text-xs font-medium uppercase ${
                viewMode === mode ? 'bg-emerald-700 text-white' : 'bg-slate-900 text-slate-400 hover:bg-slate-800'
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
