import type { DatasetFull } from './dataset'

export interface RoutePoint {
  lon: number
  lat: number
}

export interface RouteSummary {
  feasible: boolean
  distance_m?: number
  hazard_component_m?: number
  cost_m_equivalent?: number
  edge_ids?: number[]
}

export interface RouteAnalysis {
  id: string
  project_id: string
  name: string
  description: string | null
  road_dataset_id: string | null
  hazard_dataset_id: string | null
  hazard_dataset_type: string
  risk_analysis_id: string | null
  origin_lon: number
  origin_lat: number
  destination_lon: number
  destination_lat: number
  hazard_penalty_weight: number
  block_threshold: number
  node_snap_tolerance_m: number
  max_snap_distance_m: number
  scenario_id: string | null
  parameters: Record<string, unknown>
  results: {
    method?: string
    hazard_dataset_type?: string
    hazard_source?: string
    graph_summary?: {
      node_count: number
      edge_count: number
      blocked_edge_count: number
      total_road_length_m: number
      total_blocked_length_m: number
    }
    feasible?: boolean
    disconnected_due_to_blocking?: boolean
    shortest_route?: RouteSummary
    hazard_aware_route?: RouteSummary
    scenario_blocking?: {
      submitted_geometry_count: number
      match_buffer_m: number
      resolved_edge_ids: number[]
      resolved_edge_count: number
    }
    limitations?: string[]
  }
  status: 'completed' | 'failed'
  error_message: string | null
  created_at: string
}

export interface RouteAnalysisResult {
  analysis: RouteAnalysis
  datasets: DatasetFull[]
}
