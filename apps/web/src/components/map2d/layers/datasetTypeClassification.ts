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
  'flood_depth',
  'landslide_susceptibility',
  'landslide_susceptibility_screening',
  'eo_change_mask',
  'rainfall',
])

export function isRasterDatasetType(datasetType: string): boolean {
  return RASTER_DATASET_TYPES.has(datasetType)
}

// Vector dataset_types that carry a hazard-class-derived label worth
// color-coding by the shared legend (exposure features, risk features).
export const CLASS_COLORED_VECTOR_TYPES = new Set(['exposure_features', 'risk_classification'])

// Which GeoJSON feature property carries the class label to color by,
// for each CLASS_COLORED_VECTOR_TYPES entry -- shared by the 2D map
// (LayerRenderer.tsx) and the 3D terrain overlay (terrainOverlay.ts) so
// a risk-classified building/road is colored identically in both views.
export const COLOR_PROPERTY_BY_DATASET_TYPE: Record<string, string> = {
  risk_classification: 'risk_class',
  exposure_features: 'hazard_class_label',
}

// Fixed per-dataset_type fallback color for vector layers that are NOT
// class-colored (see CLASS_COLORED_VECTOR_TYPES above) -- so roads,
// buildings, and the point-infrastructure types are visually
// distinguishable from each other on the map, instead of every one of
// them rendering as the same flat grey. This is styling only: it never
// implies a classification the backend didn't compute (that's exactly
// what CLASS_COLORED_VECTOR_TYPES is for) -- a hospital is always drawn
// in this color whether or not it's been analyzed for exposure/risk.
export const FALLBACK_COLOR_BY_DATASET_TYPE: Record<string, string> = {
  roads: '#64748b', // slate -- neutral base infrastructure
  buildings: '#b8b0a8', // warm stone -- realistic neutral building material, distinct from roads
  population: '#a78bfa', // violet
  hospitals: '#0ea5e9', // sky blue -- common medical-facility cartographic convention
  shelters: '#22c55e', // green -- signals "safe destination"
  critical_infrastructure: '#f59e0b', // amber -- signals "important, be aware"
  safe_zones: '#06b6d4', // cyan -- distinct from shelters, same "safe" family
}
export const DEFAULT_FALLBACK_COLOR = '#64748b'

// Route dataset_types are always a fixed color regardless of which
// route analysis produced them -- "blocked" must always read the same
// visually, and shortest vs hazard-aware must always be tellable apart.
export const ROUTE_COLOR_BY_DATASET_TYPE: Record<string, string> = {
  route_shortest: '#94a3b8', // neutral slate -- the plain shortest path
  route_hazard_aware: '#22d3ee', // accent cyan -- the hazard-aware path
  route_blocked_segments: '#ef4444', // high-contrast red -- always visually "blocked"
}
