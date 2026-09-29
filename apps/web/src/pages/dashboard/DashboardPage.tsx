import { useEffect, useMemo } from 'react'
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
import {
  CLASS_COLORED_VECTOR_TYPES,
  COLOR_PROPERTY_BY_DATASET_TYPE,
  FALLBACK_COLOR_BY_DATASET_TYPE,
  isRasterDatasetType,
  ROUTE_COLOR_BY_DATASET_TYPE,
} from '../../components/map2d/layers/datasetTypeClassification'
import { isWgs84Crs, unionBounds } from '../../geo/bounds'
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
import { AssistantPanel } from '../../components/dashboard/panels/AssistantPanel'
import { LoadingState } from '../../components/dashboard/states/LoadingState'
import { ErrorBanner } from '../../components/dashboard/states/ErrorBanner'
import { EmptyState } from '../../components/dashboard/states/EmptyState'
import { MapView } from '../../components/map2d/MapView'
import { DashboardMapLayers } from '../../components/map2d/DashboardMapLayers'
import { SceneView } from '../../components/view3d/SceneView'

// Line types (roads, routes), the raw `buildings` footprints (extruded
// with the uniform fallback height, flat-colored -- see
// terrainOverlay.ts::FALLBACK_BUILDING_HEIGHT_M), and the class-colored
// analysis outputs (risk_classification/exposure_features), which may be
// EITHER lines (roads as the exposure asset) or polygons (buildings as
// the asset) -- SceneView/terrainOverlay.ts dispatches by the fetched
// GeoJSON's own geometry type, not by this list.
const VECTOR_OVERLAY_TYPES = new Set([
  'roads',
  'buildings',
  'route_shortest',
  'route_hazard_aware',
  'route_blocked_segments',
  ...CLASS_COLORED_VECTOR_TYPES,
])

export function DashboardPage() {
  const { projectId: projectIdParam } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const scenarioIdParam = searchParams.get('scenario')

  const { projects, loading: projectsLoading, error: projectsError } = useProjectList()
  const setSelection = useDashboardStore((s) => s.setSelection)
  const setActiveFeature = useDashboardStore((s) => s.setActiveFeature)
  const viewMode = useDashboardStore((s) => s.viewMode)
  const terrainExaggeration = useDashboardStore((s) => s.terrainExaggeration)
  // The store's own `layers` -- kept in sync with (but not overwritten
  // by) normalizedLayers via LayerControlPanel's setLayers merge -- is
  // the ONLY place a checkbox toggle's `visible` actually lands.
  // normalizedLayers itself always reports visible:false (see
  // normalizeTwinLayers) since it's a fresh snapshot of server state,
  // never mutated by user interaction; checking visibility against it
  // directly (as this file used to for drapeLayer) meant the hazard
  // drape could never actually respond to a checkbox toggle.
  const storeLayers = useDashboardStore((s) => s.layers)

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

  // Memoized on the underlying server data, not recomputed as a fresh
  // array reference on every render: DashboardPage also subscribes to
  // the store's own `layers` (storeLayers, below) for interactive
  // visibility, and setLayers (dashboardStore.ts) always returns a new
  // object -- without this memo, LayerControlPanel's own
  // useEffect(() => setLayers(normalizedLayers...), [normalizedLayers])
  // would re-fire on every store update it just caused, looping.
  const normalizedLayers = useMemo(
    () => (scenarioState ? normalizeScenarioLayers(scenarioState) : twinState ? normalizeTwinLayers(twinState) : []),
    [scenarioState, twinState],
  )

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
    (l) => storeLayers[l.layer.layerId]?.visible && l.layer.category === 'hazard' && isRasterDatasetType(l.layer.datasetType),
  )

  // Roads/routes/risk/exposure draped directly onto the 3D terrain --
  // reuses the SAME visibility toggle and the SAME colors (fixed or
  // class-based) the 2D map already uses for these dataset_types (see
  // map2d/layers/datasetTypeClassification.ts), so a layer reads as the
  // same thing in both views. risk_classification/exposure_features get
  // a `colorProperty` so terrainOverlay.ts colors each feature by its
  // own class rather than one flat color; SceneView/terrainOverlay.ts
  // picks lines vs. flat polygons per-layer from the actual fetched
  // GeoJSON geometry (roads vs. buildings as the underlying asset).
  // Road surface dataset ID for 3D visualization (polygon surfaces, not centerlines)
  const ROAD_SURFACE_DATASET_ID = 'd6cf7473-084c-4fcc-9599-4c588749729b'

  const vectorOverlays = normalizedLayers
    .filter((l) => storeLayers[l.layer.layerId]?.visible && l.layer.datasetId && VECTOR_OVERLAY_TYPES.has(l.layer.datasetType))
    .map((l) => ({
      // Use polygon road surfaces for 3D rendering instead of LineString centerlines
      datasetId: l.layer.datasetType === 'roads' ? ROAD_SURFACE_DATASET_ID : (l.layer.datasetId as string),
      datasetType: l.layer.datasetType,
      color: ROUTE_COLOR_BY_DATASET_TYPE[l.layer.datasetType] ?? FALLBACK_COLOR_BY_DATASET_TYPE[l.layer.datasetType] ?? '#64748b',
      colorProperty: CLASS_COLORED_VECTOR_TYPES.has(l.layer.datasetType) ? COLOR_PROPERTY_BY_DATASET_TYPE[l.layer.datasetType] : undefined,
    }))

  // Clips building-sourced 3D overlays (risk_classification/
  // exposure_features when buildings are the underlying asset) to the
  // real roads dataset's own bbox, padded ~1.1km -- the buildings
  // dataset's full extent (city-wide, ~14km x 19km here) is far larger
  // than the roads/route network's own study area (~3km x 2.7km), so
  // rendering an arbitrary unfiltered slice of it would silently mix in
  // geographically unrelated buildings (see ADR 0013 IMPORTANT DATA
  // RULE). Always derived from `roads`, independent of that layer's own
  // visibility toggle, so building clipping stays correct even when
  // roads themselves are hidden.
  const studyAreaLayer = normalizedLayers.find((l) => l.layer.datasetType === 'study_area')
  const studyAreaDataset = studyAreaLayer?.dataset
  const roadsLayer = normalizedLayers.find((l) => l.layer.datasetType === 'roads')
  const roadsDatasetForClip = roadsLayer?.dataset
  const aoiBbox =
    studyAreaDataset &&
    isWgs84Crs(studyAreaDataset.crs) &&
    studyAreaDataset.bbox_min_x !== null &&
    studyAreaDataset.bbox_min_y !== null &&
    studyAreaDataset.bbox_max_x !== null &&
    studyAreaDataset.bbox_max_y !== null
      ? {
          west: studyAreaDataset.bbox_min_x,
          south: studyAreaDataset.bbox_min_y,
          east: studyAreaDataset.bbox_max_x,
          north: studyAreaDataset.bbox_max_y,
        }
      : roadsDatasetForClip &&
        isWgs84Crs(roadsDatasetForClip.crs) &&
        roadsDatasetForClip.bbox_min_x !== null &&
        roadsDatasetForClip.bbox_min_y !== null &&
        roadsDatasetForClip.bbox_max_x !== null &&
        roadsDatasetForClip.bbox_max_y !== null
        ? {
            west: roadsDatasetForClip.bbox_min_x,
            south: roadsDatasetForClip.bbox_min_y,
            east: roadsDatasetForClip.bbox_max_x,
            north: roadsDatasetForClip.bbox_max_y,
          }
        : null

  const buildingClipBbox = aoiBbox
    ? {
        west: aoiBbox.west - 0.005,
        south: aoiBbox.south - 0.005,
        east: aoiBbox.east + 0.005,
        north: aoiBbox.north + 0.005,
      }
    : null

  // 2D map auto-fit: unions the visible layers' own WGS84 bbox_* --
  // never a UTM/other-projected dataset's raw bbox (isWgs84Crs gates
  // this), and never DigitalTwinState.extent (Phase 9 never reprojects
  // that either). Without this, a freshly-loaded project's real data
  // (often a small area within a huge default view) is invisible at
  // the map's default center=[0,0] zoom=1 -- exactly the same class of
  // "real data exists but isn't actually visible" gap the 3D camera-
  // framing fix above addresses for the terrain view.
  const mapFitBounds = unionBounds(
    normalizedLayers
      .filter((l) => storeLayers[l.layer.layerId]?.visible && l.dataset && isWgs84Crs(l.dataset.crs))
      .map((l) => {
        const d = l.dataset!
        if (d.bbox_min_x === null || d.bbox_min_y === null || d.bbox_max_x === null || d.bbox_max_y === null) return null
        return { west: d.bbox_min_x, south: d.bbox_min_y, east: d.bbox_max_x, north: d.bbox_max_y }
      }),
  )

  const latestHazard = scenarioState?.derived_analyses.hazard_scenarios[0] ?? projectAnalyses.hazardScenarios[0] ?? null
  const hazardLimitations = useHazardLimitations(latestHazard?.id ?? null)
  const latestExposure = scenarioState?.derived_analyses.exposure_analyses[0] ?? projectAnalyses.exposureAnalyses[0] ?? null
  const latestRisk = scenarioState?.derived_analyses.risk_analyses[0] ?? projectAnalyses.riskAnalyses[0] ?? null
  const latestRoute = scenarioState?.derived_analyses.route_analyses[0] ?? projectAnalyses.routeAnalyses[0] ?? null

  // Real route-analysis origin/destination markers -- shown only while
  // at least one route_* layer is actually visible, matching how every
  // other 3D overlay is gated on its own checkbox.
  const routeLayerVisible = normalizedLayers.some(
    (l) => storeLayers[l.layer.layerId]?.visible && l.layer.datasetType.startsWith('route_'),
  )
  const routeEndpoints =
    routeLayerVisible && latestRoute
      ? [
          {
            origin: [latestRoute.origin_lon, latestRoute.origin_lat] as [number, number],
            destination: [latestRoute.destination_lon, latestRoute.destination_lat] as [number, number],
          },
        ]
      : []

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
            <MapView visible={viewMode === '2d'} fitBounds={mapFitBounds}>
              <DashboardMapLayers />
            </MapView>
            <SceneView
              visible={viewMode === '3d'}
              demDatasetId={demLayer?.dataset?.id ?? null}
              drapeDatasetId={drapeLayer?.dataset?.id ?? null}
              exaggeration={terrainExaggeration}
              vectorOverlays={vectorOverlays}
              buildingClipBbox={buildingClipBbox}
              aoiBbox={aoiBbox}
              routeEndpoints={routeEndpoints}
              onFeatureSelect={setActiveFeature}
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
            { id: 'assistant', label: 'Assistant', content: <AssistantPanel /> },
          ]}
        />
      }
      legend={<LegendPanel />}
    />
  )
}
