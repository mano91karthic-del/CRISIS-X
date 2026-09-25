import { useDashboardStore } from '../../../state/dashboardStore'
import { EmptyState } from '../states/EmptyState'

export function FeatureInspectorPanel() {
  const activeFeature = useDashboardStore((s) => s.activeFeature)

  if (!activeFeature) {
    return <EmptyState title="No feature selected" description="Click a vector layer on the map to inspect its attributes." />
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div>
        <h4 className="font-semibold text-slate-200">Feature attributes</h4>
        <p className="text-xs text-slate-500">dataset: {activeFeature.datasetId}</p>
      </div>
      <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-xs text-slate-300">
        {Object.entries(activeFeature.properties).map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="truncate text-slate-500">{key}</dt>
            <dd className="truncate">{String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
