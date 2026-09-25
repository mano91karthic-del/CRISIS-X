import type { DatasetFull } from './dataset'
import type { ExposureByClassEntry } from './exposure'

export interface RiskByClassEntry extends ExposureByClassEntry {
  hazard_intensity_weight: number
  risk_score: number
  risk_class: string
}

export interface RiskAnalysis {
  id: string
  project_id: string
  name: string
  description: string | null
  exposure_analysis_id: string | null
  hazard_dataset_id: string | null
  exposure_dataset_id: string | null
  hazard_dataset_type: string
  exposure_dataset_type: string
  vulnerability_weight: number
  consequence_weight: number
  risk_breakpoints: number[]
  scenario_id: string | null
  parameters: Record<string, unknown>
  results: {
    method?: string
    hazard_dataset_type?: string
    vulnerability_weight?: number
    consequence_weight?: number
    risk_breakpoints?: number[]
    risk_class_legend?: Record<string, string>
    by_class?: Record<string, RiskByClassEntry>
    limitations?: string[]
  }
  status: 'completed' | 'failed'
  error_message: string | null
  created_at: string
}

export interface RiskAnalysisResult {
  analysis: RiskAnalysis
  datasets: DatasetFull[]
}
