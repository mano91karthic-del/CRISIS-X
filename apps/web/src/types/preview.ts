// Mirrors backend app/schemas/preview.py::RasterPreviewBoundsRead and
// app/services/preview.py::build_geojson_preview's envelope shape.

export interface RasterPreviewBounds {
  west: number
  south: number
  east: number
  north: number
  native_crs: string
  reprojection_applied: boolean
  nodata_present: boolean
  elevation_min_m: number | null
  elevation_max_m: number | null
  pixel_size_x_m: number | null
  pixel_size_y_m: number | null
}

// GeoJSON geometry/feature types kept intentionally minimal -- the
// dashboard never inspects geometry internals beyond handing the whole
// FeatureCollection to MapLibre, and reads `properties` generically in
// the feature inspector.
export interface GeoJsonFeature {
  type: 'Feature'
  geometry: { type: string; coordinates: unknown } | null
  properties: Record<string, unknown> | null
}

export interface GeoJsonPreviewEnvelope {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
  truncated: boolean
  total_feature_count: number
  source_crs: string
}
