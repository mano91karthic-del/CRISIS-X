import type { DatasetFull } from './dataset'

export interface ExposureByClassEntry {
  hazard_class_code: number
  count?: number
  sum?: number
  length_m?: number
  feature_count?: number
  area_m2?: number
  population_sum?: number
}

export interface ExposureAnalysis {
  id: string
  project_id: string
  name: string
  description: string | null
  hazard_dataset_id: string | null
  exposure_dataset_id: string | null
  hazard_dataset_type: string
  exposure_dataset_type: string
  population_field: string | null
  scenario_id: string | null
  parameters: Record<string, unknown>
  results: {
    method?: string
    hazard_dataset_type?: string
    reprojected_hazard_crs?: boolean
    crs?: string
    hazard_class_labels?: Record<string, string>
    by_class?: Record<string, ExposureByClassEntry>
    depth_statistics?: { min_depth_m: number; mean_depth_m: number; max_depth_m: number }
    limitations?: string[]
  }
  status: 'completed' | 'failed'
  error_message: string | null
  created_at: string
}

export interface ExposureAnalysisResult {
  analysis: ExposureAnalysis
  datasets: DatasetFull[]
}
