import { useEffect } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useProjectList } from '../../hooks/useProjectList'
import { useDigitalTwin } from '../../hooks/useDigitalTwin'
import { useDigitalTwinState } from '../../hooks/useDigitalTwinState'
import { useScenarioList } from '../../hooks/useScenarioList'
import { useScenarioState } from '../../hooks/useScenarioState'
import { useProjectAnalyses } from '../../hooks/useProjectAnalyses'
import { useHazardLimitations } from '../../hooks/useHazardLimitations'
import { useProjectDatasets } from '../../hooks/useProjectDatasets'
import { createDigitalTwin } from '../../lib/api/digitalTwin'
import { createScenario } from '../../lib/api/scenarios'
import { normalizeScenarioLayers, normalizeTwinLayers } from '../../state/normalizeLayers'
import { useDashboardStore } from '../../state/dashboardStore'
import { isRasterDatasetType } from '../../components/map2d/layers/datasetTypeClassification'
import { DashboardLayout } from '../../components/dashboard/DashboardLayout'
import { TopBar } from '../../components/dashboard/TopBar'
import { LayerControlPanel } from '../../components/dashboard/LayerControlPanel'
import { RegisterDatasetControl } from '../../components/dashboard/RegisterDatasetControl'
import { LegendPanel } from '../../components/dashboard/LegendPanel'
import { InfoPanelDock } from '../../components/dashboard/InfoPanelDock'
import { ScenarioComparisonPanel } from '../../components/dashboard/ScenarioComparisonPanel'
import { HazardInfoPanel } from '../../components/dashboard/panels/HazardInfoPanel'
import { ExposureInfoPanel } from '../../components/dashboard/panels/ExposureInfoPanel'
import { RiskInfoPanel } from '../../components/dashboard/panels/RiskInfoPanel'
import { RouteInfoPanel } from '../../components/dashboard/panels/RouteInfoPanel'
import { FeatureInspectorPanel } from '../../components/dashboard/panels/FeatureInspectorPanel'
import { LoadingState } from '../../components/dashboard/states/LoadingState'
import { ErrorBanner } from '../../components/dashboard/states/ErrorBanner'
import { EmptyState } from '../../components/dashboard/states/EmptyState'
import { MapView } from '../../components/map2d/MapView'
import { DashboardMapLayers } from '../../components/map2d/DashboardMapLayers'
import { SceneView } from '../../components/view3d/SceneView'

export function DashboardPage() {
  const { projectId: projectIdParam } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const scenarioIdParam = searchParams.get('scenario')

  const { projects, loading: projectsLoading, error: projectsError } = useProjectList()
  const setSelection = useDashboardStore((s) => s.setSelection)
  const viewMode = useDashboardStore((s) => s.viewMode)
  const terrainExaggeration = useDashboardStore((s) => s.terrainExaggeration)

  const selectedProjectId = projectIdParam ?? null

  useEffect(() => {
    if (!selectedProjectId && projects.length > 0) {
      navigate(`/dashboard/projects/${projects[0].id}`, { replace: true })
    }
  }, [selectedProjectId, projects, navigate])

  const { twin, loading: twinLoading, error: twinError, refresh: refreshTwin } = useDigitalTwin(selectedProjectId)
  const { scenarios, refresh: refreshScenarios } = useScenarioList(twin?.id ?? null)
  const { state: twinState, loading: twinStateLoading, refresh: refreshTwinState } = useDigitalTwinState(twin?.id ?? null)
  const { state: scenarioState } = useScenarioState(scenarioIdParam)
  const projectAnalyses = useProjectAnalyses(selectedProjectId)
  const { datasets: projectDatasets } = useProjectDatasets(selectedProjectId)

  useEffect(() => {
    setSelection({ projectId: selectedProjectId, twinId: twin?.id ?? null, scenarioId: scenarioIdParam })
  }, [selectedProjectId, twin?.id, scenarioIdParam, setSelection])

  function handleSelectProject(id: string) {
    navigate(`/dashboard/projects/${id}`)
  }

  function handleSelectScenario(id: string | null) {
    setSearchParams(id ? { scenario: id } : {})
  }

  async function handleCreateTwin() {
    if (!selectedProjectId) return
    const projectName = projects.find((p) => p.id === selectedProjectId)?.name ?? 'Project'
    await createDigitalTwin(selectedProjectId, `${projectName} Twin`)
    refreshTwin()
  }

  async function handleCreateScenario() {
    if (!twin) return
    const name = window.prompt('Scenario name?', 'New scenario')
    if (!name) return
    const scenario = await createScenario(twin.id, name)
    refreshScenarios()
    handleSelectScenario(scenario.id)
  }

  const normalizedLayers = scenarioState
    ? normalizeScenarioLayers(scenarioState)
    : twinState
      ? normalizeTwinLayers(twinState)
      : []

  // The twin's own ACTIVE layers, independent of scenario view state --
  // registration always targets the twin itself (POST /digital-twins/{id}/layers),
  // never a scenario override, so RegisterDatasetControl must compare
  // against twinState directly, not the scenario-resolved normalizedLayers.
  const activeTwinLayers = Object.values(twinState?.layers_by_category ?? {})
    .flat()
    .filter((lr) => lr.layer.dataset_id)
    .map((lr) => ({ datasetId: lr.layer.dataset_id as string, datasetType: lr.layer.dataset_type }))

  const demLayer = normalizedLayers.find((l) => l.layer.datasetType === 'dem' || l.layer.datasetType === 'dsm')
  const drapeLayer = normalizedLayers.find(
    (l) => l.layer.visible && l.layer.category === 'hazard' && isRasterDatasetType(l.layer.datasetType),
  )

  const latestHazard = scenarioState?.derived_analyses.hazard_scenarios[0] ?? projectAnalyses.hazardScenarios[0] ?? null
  const hazardLimitations = useHazardLimitations(latestHazard?.id ?? null)
  const latestExposure = scenarioState?.derived_analyses.exposure_analyses[0] ?? projectAnalyses.exposureAnalyses[0] ?? null
  const latestRisk = scenarioState?.derived_analyses.risk_analyses[0] ?? projectAnalyses.riskAnalyses[0] ?? null
  const latestRoute = scenarioState?.derived_analyses.route_analyses[0] ?? projectAnalyses.routeAnalyses[0] ?? null

  const analysesByType = {
    exposure: [...projectAnalyses.exposureAnalyses, ...(scenarioState?.derived_analyses.exposure_analyses ?? [])].map((a) => ({
      id: a.id,
      label: `${a.name} (${a.status})`,
    })),
    risk: [...projectAnalyses.riskAnalyses, ...(scenarioState?.derived_analyses.risk_analyses ?? [])].map((a) => ({
      id: a.id,
      label: `${a.name} (${a.status})`,
    })),
    route: [...projectAnalyses.routeAnalyses, ...(scenarioState?.derived_analyses.route_analyses ?? [])].map((a) => ({
      id: a.id,
      label: `${a.name} (${a.status})`,
    })),
  }

  if (projectsLoading) return <LoadingState label="Loading projects…" />
  if (projectsError) return <ErrorBanner message={projectsError} />
  if (projects.length === 0) {
    return <EmptyState title="No projects yet" description="Create a project in the Data Hub first." />
  }

  return (
    <DashboardLayout
      topBar={
        <TopBar
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={handleSelectProject}
          twin={twin}
          scenarios={scenarios}
          selectedScenarioId={scenarioIdParam}
          onSelectScenario={handleSelectScenario}
          onCreateScenario={handleCreateScenario}
        />
      }
      layerControl={
        twinLoading || twinStateLoading ? (
          <LoadingState label="Loading digital twin…" />
        ) : twinError ? (
          <ErrorBanner message={twinError} onRetry={refreshTwin} />
        ) : !twin ? (
          <EmptyState
            title="No Digital Twin for this project"
            description="Create one to start registering layers."
            action={
              <button
                type="button"
                onClick={handleCreateTwin}
                className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white"
              >
                Create Digital Twin
              </button>
            }
          />
        ) : (
          <div className="flex h-full flex-col">
            <RegisterDatasetControl
              twinId={twin.id}
              datasets={projectDatasets}
              activeLayers={activeTwinLayers}
              onRegistered={refreshTwinState}
            />
            <LayerControlPanel
              normalizedLayers={normalizedLayers}
              missingRecommended={!scenarioState ? twinState?.missing_recommended_layers : undefined}
            />
          </div>
        )
      }
      viewport={
        twin ? (
          <>
            <MapView visible={viewMode === '2d'}>
              <DashboardMapLayers />
            </MapView>
            <SceneView
              visible={viewMode === '3d'}
              demDatasetId={demLayer?.dataset?.id ?? null}
              drapeDatasetId={drapeLayer?.dataset?.id ?? null}
              exaggeration={terrainExaggeration}
            />
          </>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-600">
            Select or create a Digital Twin to begin.
          </div>
        )
      }
      infoPanel={
        <InfoPanelDock
          tabs={[
            { id: 'hazard', label: 'Hazard', content: <HazardInfoPanel hazard={latestHazard} limitations={hazardLimitations} /> },
            { id: 'exposure', label: 'Exposure', content: <ExposureInfoPanel exposure={latestExposure} /> },
            { id: 'risk', label: 'Risk', content: <RiskInfoPanel risk={latestRisk} /> },
            { id: 'route', label: 'Route', content: <RouteInfoPanel route={latestRoute} /> },
            { id: 'feature', label: 'Feature', content: <FeatureInspectorPanel /> },
            { id: 'compare', label: 'Compare', content: <ScenarioComparisonPanel analysesByType={analysesByType} /> },
          ]}
        />
      }
      legend={<LegendPanel />}
    />
  )
}
