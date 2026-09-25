import type { StyleSpecification } from 'maplibre-gl'

// Phase 11 decision (see docs/architecture/0012-phase-11-command-dashboard.md):
// zero external basemap imagery by default -- a single background layer
// in the app's own palette. Keeps the dashboard fully offline-capable
// and avoids any hidden dependency on a live third-party tile service,
// consistent with "no live APIs." All real content comes from the
// project's own dataset-derived sources (image/geojson).
//
// Optional, opt-in config point: set VITE_BASEMAP_STYLE_URL to swap in a
// self-hosted or public vector tile style for real-world reference
// context (roads/coastlines/place names) -- unused unless explicitly set.
export const BASEMAP_STYLE_URL: string | undefined = import.meta.env.VITE_BASEMAP_STYLE_URL

export const BLANK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0f172a' }, // slate-900, matches the app palette
    },
  ],
}
