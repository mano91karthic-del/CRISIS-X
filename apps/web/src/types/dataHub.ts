export interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
}

export type DatasetType =
  | 'dem'
  | 'dsm'
  | 'imagery'
  | 'rainfall'
  | 'weather'
  | 'roads'
  | 'buildings'
  | 'population'
  | 'hospitals'
  | 'shelters'
  | 'critical_infrastructure'
  | 'safe_zones'
  | 'terrain_x_package'
  | 'other'

export const DATASET_TYPES: DatasetType[] = [
  'dem',
  'dsm',
  'imagery',
  'rainfall',
  'weather',
  'roads',
  'buildings',
  'population',
  'hospitals',
  'shelters',
  'critical_infrastructure',
  'safe_zones',
  'terrain_x_package',
  'other',
]

export type DatasetStatus = 'uploaded' | 'validated' | 'invalid'

export interface Dataset {
  id: string
  project_id: string
  name: string
  dataset_type: DatasetType
  source_filename: string
  file_format: string | null
  crs: string | null
  bbox_min_x: number | null
  bbox_min_y: number | null
  bbox_max_x: number | null
  bbox_max_y: number | null
  file_size_bytes: number
  checksum_sha256: string
  status: DatasetStatus
  validation_message: string | null
  metadata_json: Record<string, unknown>
  provenance: Record<string, unknown>
  created_at: string
  updated_at: string
}
