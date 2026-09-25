import type { DatasetFull } from './dataset'

export interface DigitalTwin {
  id: string
  project_id: string
  name: string
  description: string | null
  version: number
  created_at: string
  updated_at: string
}

export type TwinLayerStatus = 'active' | 'superseded' | 'retired'

export interface TwinLayer {
  id: string
  twin_id: string
  dataset_id: string | null
  dataset_type: string
  category: string
  status: TwinLayerStatus
  registered_at_version: number
  provenance: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface TwinLayerResult {
  layer: TwinLayer
  dataset: DatasetFull | null
}

export interface TwinExtent {
  reference_crs: string | null
  bbox_min_x: number | null
  bbox_min_y: number | null
  bbox_max_x: number | null
  bbox_max_y: number | null
  crs_mismatch_layer_ids: string[]
  layers_without_extent: string[]
}

export interface TwinAcquisitionDateRange {
  earliest: string | null
  latest: string | null
  layers_without_acquisition_date: string[]
}

export interface DigitalTwinState {
  twin: DigitalTwin
  extent: TwinExtent
  acquisition_date_range: TwinAcquisitionDateRange
  layers_by_category: Record<string, TwinLayerResult[]>
  missing_recommended_layers: string[]
  limitations: string[]
}
