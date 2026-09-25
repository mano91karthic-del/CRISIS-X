// Mirrors backend app/services/preview.py's raster-vs-vector split (by
// dataset_type, since the frontend's LayerState doesn't carry
// file_format -- the backend endpoints themselves are the authoritative
// 400 check; this is only used to pick which hook/mechanism to try).
export const RASTER_DATASET_TYPES = new Set([
  'dem',
  'dsm',
  'slope',
  'aspect',
  'flow_direction',
  'flow_accumulation',
  'flood_inundation',
  'landslide_susceptibility',
  'eo_change_mask',
])

export function isRasterDatasetType(datasetType: string): boolean {
  return RASTER_DATASET_TYPES.has(datasetType)
}

// Vector dataset_types that carry a hazard-class-derived label worth
// color-coding by the shared legend (exposure features, risk features).
export const CLASS_COLORED_VECTOR_TYPES = new Set(['exposure_features', 'risk_classification'])
