import type { Dataset } from '../types/dataHub'

interface DatasetTableProps {
  datasets: Dataset[]
  onDelete: (id: string) => void
  downloadUrl: (id: string) => string
}

const STATUS_STYLES: Record<Dataset['status'], string> = {
  validated: 'bg-emerald-600/20 text-emerald-300',
  invalid: 'bg-red-600/20 text-red-300',
  uploaded: 'bg-amber-600/20 text-amber-300',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DatasetTable({ datasets, onDelete, downloadUrl }: DatasetTableProps) {
  if (datasets.length === 0) {
    return <p className="text-sm text-slate-500">No datasets uploaded for this project yet.</p>
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-900/50 text-xs uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Type</th>
            <th className="px-3 py-2">Format</th>
            <th className="px-3 py-2">CRS</th>
            <th className="px-3 py-2">Size</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {datasets.map((dataset) => (
            <tr key={dataset.id}>
              <td className="px-3 py-2">{dataset.name}</td>
              <td className="px-3 py-2 text-slate-400">{dataset.dataset_type}</td>
              <td className="px-3 py-2 text-slate-400">{dataset.file_format ?? '—'}</td>
              <td className="px-3 py-2 text-slate-400">{dataset.crs ?? '—'}</td>
              <td className="px-3 py-2 text-slate-400">{formatBytes(dataset.file_size_bytes)}</td>
              <td className="px-3 py-2">
                <span className={`rounded-full px-2 py-1 text-xs ${STATUS_STYLES[dataset.status]}`}>
                  {dataset.status}
                </span>
                {dataset.validation_message && (
                  <p className="mt-1 max-w-xs text-xs text-slate-500">{dataset.validation_message}</p>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right">
                <a href={downloadUrl(dataset.id)} className="mr-3 text-emerald-400 hover:underline">
                  Download
                </a>
                <button
                  type="button"
                  onClick={() => onDelete(dataset.id)}
                  className="text-red-400 hover:underline"
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
