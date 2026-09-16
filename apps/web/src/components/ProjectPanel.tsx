import { useState, type FormEvent } from 'react'
import type { Project } from '../types/dataHub'

interface ProjectPanelProps {
  projects: Project[]
  selectedProjectId: string | null
  onSelect: (id: string) => void
  onCreate: (name: string, description: string) => Promise<void>
}

export function ProjectPanel({ projects, selectedProjectId, onSelect, onCreate }: ProjectPanelProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      await onCreate(name.trim(), description.trim())
      setName('')
      setDescription('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="text-lg font-semibold">Projects</h2>

      <ul className="flex flex-col gap-1">
        {projects.length === 0 && <li className="text-sm text-slate-500">No projects yet.</li>}
        {projects.map((project) => (
          <li key={project.id}>
            <button
              type="button"
              onClick={() => onSelect(project.id)}
              className={`w-full rounded px-3 py-2 text-left text-sm transition ${
                project.id === selectedProjectId
                  ? 'bg-emerald-600/20 text-emerald-300'
                  : 'text-slate-300 hover:bg-slate-800'
              }`}
            >
              {project.name}
            </button>
          </li>
        ))}
      </ul>

      <form onSubmit={handleSubmit} className="flex flex-col gap-2 border-t border-slate-800 pt-4">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New project name"
          className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        />
        <button
          type="submit"
          disabled={submitting || !name.trim()}
          className="rounded bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create project'}
        </button>
      </form>
    </div>
  )
}
