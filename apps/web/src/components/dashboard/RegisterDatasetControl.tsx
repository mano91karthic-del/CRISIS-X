import { useState } from 'react'
import { registerTwinLayer } from '../../lib/api/digitalTwin'
import type { Dataset } from '../../types/dataHub'

export interface ActiveLayerRef {
  datasetId: string
  datasetType: string
}

export interface RegisterDatasetControlProps {
  twinId: string
  /** The project's full dataset list (Data Hub's own listDatasets --
   * unfiltered). Filtering to registerable candidates happens here. */
  datasets: Dataset[]
  /** The twin's currently ACTIVE layers -- used both to exclude already-
   * registered datasets from the picker and to warn when registering a
   * new one of the same dataset_type will supersede an existing layer
   * (see app/api/digital_twin.py's one-active-layer-per-dataset_type
   * rule -- existing behavior, surfaced here, not changed). */
  activeLayers: ActiveLayerRef[]
  onRegistered: () => void
}

/** Exposes the Digital Twin's existing "register a validated dataset as
 * a layer" endpoint (POST /digital-twins/{id}/layers), which previously
 * had no UI anywhere in the app -- a validated dataset could only ever
 * become visible in the Command Dashboard if something called that
 * endpoint directly (e.g. a test or a manual API call). This is a thin,
 * additive picker; it creates no new backend behavior.
 */
export function RegisterDatasetControl({ twinId, datasets, activeLayers, onRegistered }: RegisterDatasetControlProps) {
  const [selectedId, setSelectedId] = useState('')
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const registeredIds = new Set(activeLayers.map((l) => l.datasetId))
  const candidates = datasets.filter((d) => d.status === 'validated' && !registeredIds.has(d.id))
  const selected = candidates.find((d) => d.id === selectedId)
  const conflictingLayer = selected ? activeLayers.find((l) => l.datasetType === selected.dataset_type) : undefined

  async function handleRegister() {
    if (!selected) return
    setRegistering(true)
    setError(null)
    try {
      await registerTwinLayer(twinId, selected.id)
      setSelectedId('')
      onRegistered()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to register dataset')
    } finally {
      setRegistering(false)
    }
  }

  if (candidates.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5 border-b border-slate-800 px-3 py-2">
      <label className="flex items-center gap-2 text-xs text-slate-400">
        Register dataset
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          aria-label="Select dataset to register"
          className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
        >
          <option value="">Choose a validated dataset…</option>
          {candidates.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.dataset_type})
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={handleRegister}
          disabled={!selected || registering}
          className="rounded bg-emerald-700 px-2 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          {registering ? 'Registering…' : 'Register'}
        </button>
      </label>
      {conflictingLayer && (
        <p className="text-[11px] text-amber-400">
          This will supersede the current active "{conflictingLayer.datasetType}" layer.
        </p>
      )}
      {error && <p className="text-[11px] text-red-400">{error}</p>}
    </div>
  )
}
