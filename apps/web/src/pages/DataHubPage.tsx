import { useEffect, useState } from 'react'
import {
  createProject,
  datasetDownloadUrl,
  deleteDataset,
  listDatasets,
  listProjects,
  uploadDataset,
} from '../lib/api'
import { DatasetTable } from '../components/DatasetTable'
import { ProjectPanel } from '../components/ProjectPanel'
import { UploadForm } from '../components/UploadForm'
import type { Dataset, DatasetType, Project } from '../types/dataHub'

export function DataHubPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  async function refreshProjects(preferSelect?: string) {
    try {
      const data = await listProjects()
      setProjects(data)
      setLoadError(null)
      if (preferSelect) {
        setSelectedProjectId(preferSelect)
      } else if (data.length > 0 && !selectedProjectId) {
        setSelectedProjectId(data[0].id)
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load projects')
    }
  }

  async function refreshDatasets(projectId: string) {
    try {
      const data = await listDatasets(projectId)
      setDatasets(data)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load datasets')
    }
  }

  useEffect(() => {
    void refreshProjects()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (selectedProjectId) {
      void refreshDatasets(selectedProjectId)
    } else {
      setDatasets([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProjectId])

  async function handleCreateProject(name: string, description: string) {
    const project = await createProject({ name, description: description || undefined })
    await refreshProjects(project.id)
  }

  async function handleUpload(datasetType: DatasetType, file: File) {
    if (!selectedProjectId) return
    await uploadDataset(selectedProjectId, datasetType, file)
    await refreshDatasets(selectedProjectId)
  }

  async function handleDelete(datasetId: string) {
    await deleteDataset(datasetId)
    if (selectedProjectId) {
      await refreshDatasets(selectedProjectId)
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Data Hub</h1>
        <p className="text-sm text-slate-400">
          Manage projects and catalog geospatial datasets: DEM/DSM, imagery, rainfall, weather,
          roads, buildings, population, and critical infrastructure.
        </p>
      </header>

      {loadError && (
        <p className="rounded border border-red-800 bg-red-950/50 p-3 text-sm text-red-300">{loadError}</p>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[280px_1fr]">
        <ProjectPanel
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelect={setSelectedProjectId}
          onCreate={handleCreateProject}
        />

        <div className="flex flex-col gap-4">
          {selectedProjectId ? (
            <>
              <UploadForm onUpload={handleUpload} />
              <DatasetTable datasets={datasets} onDelete={handleDelete} downloadUrl={datasetDownloadUrl} />
            </>
          ) : (
            <p className="text-sm text-slate-500">Create or select a project to manage datasets.</p>
          )}
        </div>
      </div>
    </div>
  )
}
