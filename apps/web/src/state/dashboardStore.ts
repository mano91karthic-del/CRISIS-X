import { create } from 'zustand'

export interface LayerState {
  layerId: string
  datasetId: string | null
  datasetType: string
  category: string
  visible: boolean
  opacity: number
  zIndex: number
}

export interface ActiveFeature {
  datasetId: string
  featureId: string | number | null
  properties: Record<string, unknown>
  provenance?: Record<string, unknown>
}

export type ViewMode = '2d' | '3d' | 'split'

interface DashboardState {
  projectId: string | null
  twinId: string | null
  scenarioId: string | null
  viewMode: ViewMode

  layers: Record<string, LayerState>
  activeFeature: ActiveFeature | null

  terrainExaggeration: number

  setSelection: (selection: { projectId?: string | null; twinId?: string | null; scenarioId?: string | null }) => void
  setViewMode: (mode: ViewMode) => void

  setLayers: (layers: LayerState[]) => void
  setLayerVisible: (layerId: string, visible: boolean) => void
  setLayerOpacity: (layerId: string, opacity: number) => void
  reorderLayer: (layerId: string, zIndex: number) => void

  setActiveFeature: (feature: ActiveFeature | null) => void
  setTerrainExaggeration: (value: number) => void
}

export const useDashboardStore = create<DashboardState>((set) => ({
  projectId: null,
  twinId: null,
  scenarioId: null,
  viewMode: '2d',

  layers: {},
  activeFeature: null,

  terrainExaggeration: 1.5,

  setSelection: (selection) =>
    set((state) => ({
      projectId: selection.projectId !== undefined ? selection.projectId : state.projectId,
      twinId: selection.twinId !== undefined ? selection.twinId : state.twinId,
      scenarioId: selection.scenarioId !== undefined ? selection.scenarioId : state.scenarioId,
    })),

  setViewMode: (mode) => set({ viewMode: mode }),

  setLayers: (layers) =>
    set((state) => {
      // Preserve existing visibility/opacity for a layer id that
      // survives a refresh (e.g. re-fetching /state after a scenario
      // run) -- never resets a user's toggles just because the
      // underlying list was refetched.
      const next: Record<string, LayerState> = {}
      layers.forEach((layer, index) => {
        const existing = state.layers[layer.layerId]
        next[layer.layerId] = existing
          ? { ...layer, visible: existing.visible, opacity: existing.opacity, zIndex: existing.zIndex }
          : { ...layer, zIndex: index }
      })
      return { layers: next }
    }),

  setLayerVisible: (layerId, visible) =>
    set((state) => {
      const layer = state.layers[layerId]
      if (!layer) {
        // Layer doesn't exist yet (e.g. Safe Route synthetic ID) — create it
        return { layers: { ...state.layers, [layerId]: { layerId, datasetId: null, datasetType: 'safe_route', category: 'route', visible, opacity: 1, zIndex: 0 } } }
      }
      return { layers: { ...state.layers, [layerId]: { ...layer, visible } } }
    }),

  setLayerOpacity: (layerId, opacity) =>
    set((state) => {
      const layer = state.layers[layerId]
      if (!layer) return state
      return { layers: { ...state.layers, [layerId]: { ...layer, opacity } } }
    }),

  reorderLayer: (layerId, zIndex) =>
    set((state) => {
      const layer = state.layers[layerId]
      if (!layer) return state
      return { layers: { ...state.layers, [layerId]: { ...layer, zIndex } } }
    }),

  setActiveFeature: (feature) => set({ activeFeature: feature }),
  setTerrainExaggeration: (value) => set({ terrainExaggeration: value }),
}))
