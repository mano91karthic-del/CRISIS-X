// Mirrors backend app/schemas/dataset.py::DatasetRead field-for-field.
// Deliberately separate from types/dataHub.ts's (Data Hub-only, smaller)
// Dataset type -- that file is untouched by Phase 11.
export interface DatasetFull {
  id: string
  project_id: string
  name: string
  dataset_type: string
  source_dataset_id: string | null
  origin: string
  terrain_x_package_id: string | null
  hazard_scenario_id: string | null
  eo_change_analysis_id: string | null
  exposure_analysis_id: string | null
  risk_analysis_id: string | null
  route_analysis_id: string | null
  acquisition_date: string | null
  source_filename: string
  file_format: string | null
  crs: string | null
  bbox_min_x: number | null
  bbox_min_y: number | null
  bbox_max_x: number | null
  bbox_max_y: number | null
  file_size_bytes: number
  checksum_sha256: string
  status: 'uploaded' | 'validated' | 'invalid'
  validation_message: string | null
  metadata_json: Record<string, unknown>
  provenance: Record<string, unknown>
  created_at: string
  updated_at: string
}
