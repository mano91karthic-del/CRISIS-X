import { useState, type FormEvent } from 'react'
import { DATASET_TYPES, type DatasetType } from '../types/dataHub'

interface UploadFormProps {
  onUpload: (datasetType: DatasetType, file: File) => Promise<void>
}

export function UploadForm({ onUpload }: UploadFormProps) {
  const [datasetType, setDatasetType] = useState<DatasetType>('other')
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setSubmitting(true)
    setError(null)
    try {
      await onUpload(datasetType, file)
      setFile(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/50 p-4"
    >
      <div className="flex flex-col gap-1">
        <label className="text-xs text-slate-500" htmlFor="dataset-type">
          Dataset type
        </label>
        <select
          id="dataset-type"
          value={datasetType}
          onChange={(e) => setDatasetType(e.target.value as DatasetType)}
          className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        >
          {DATASET_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs text-slate-500" htmlFor="dataset-file">
          File
        </label>
        <input
          id="dataset-file"
          type="file"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm text-slate-300"
        />
      </div>

      <button
        type="submit"
        disabled={!file || submitting}
        className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
      >
        {submitting ? 'Uploading…' : 'Upload dataset'}
      </button>

      {error && <p className="w-full text-sm text-red-400">{error}</p>}
    </form>
  )
}
