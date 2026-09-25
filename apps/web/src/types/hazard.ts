import type { DatasetFull } from './dataset'

export interface HazardScenario {
  id: string
  project_id: string
  hazard_type: 'flood' | 'landslide'
  name: string
  description: string | null
  input_datasets: Record<string, string>
  parameters: Record<string, unknown>
  rainfall_dataset_id: string | null
  scenario_id: string | null
  status: 'completed' | 'failed'
  error_message: string | null
  created_at: string
}

export interface HazardScenarioResult {
  scenario: HazardScenario
  datasets: DatasetFull[]
}
