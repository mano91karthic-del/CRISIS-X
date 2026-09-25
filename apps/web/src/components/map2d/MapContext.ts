import { createContext, useContext } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'

export const MapContext = createContext<MapLibreMap | null>(null)

/** Returns the live maplibre-gl Map instance, or null before it has
 * mounted -- every layer hook must handle the null case (skip adding
 * sources/layers until the map exists), never assume it's ready.
 */
export function useMapInstance(): MapLibreMap | null {
  return useContext(MapContext)
}
