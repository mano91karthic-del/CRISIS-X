import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { BASEMAP_STYLE_URL, BLANK_STYLE } from './mapStyle'
import { MapContext } from './MapContext'
import { toMapLibreBounds, type LonLatBounds } from '../../geo/bounds'

declare global {
  interface Window {
    __map?: MapLibreMap
  }
}

interface MapViewProps {
  visible: boolean
  children?: ReactNode
  /** Union of the currently-visible layers' own WGS84 bounds (never a
   * Dataset's raw, possibly non-WGS84 bbox_* -- see geo/bounds.ts) --
   * the map fits to this whenever it changes, so a freshly-loaded
   * project's real data is actually visible instead of the default
   * center=[0,0] zoom=1 view. null means "nothing to fit to yet,"
   * never a guessed extent.
   */
  fitBounds?: LonLatBounds | null
}

/** Owns the single maplibre-gl.Map instance's lifecycle. Created once on
 * mount, removed on unmount -- never recreated on project/twin/scenario/
 * layer changes (see docs/architecture/0012-phase-11-command-dashboard.md
 * §4). `visible` toggles CSS display only, so the WebGL context and
 * camera state survive switching to the 3D view and back.
 */
export function MapView({ visible, children, fitBounds }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapInstance, setMapInstance] = useState<MapLibreMap | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE_URL ?? BLANK_STYLE,
      center: [0, 0],
      zoom: 1,
    })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    mapRef.current = map

    map.on('load', () => {
      setMapInstance(map)
      // Only for the Playwright smoke spec's canvas/source assertions --
      // opt-in via env, never present unless explicitly built for E2E.
      if (import.meta.env.VITE_EXPOSE_MAP_FOR_TESTS === 'true') {
        window.__map = map
      }
    })

    return () => {
      map.remove()
      mapRef.current = null
      setMapInstance(null)
    }
  }, [])

  useEffect(() => {
    mapRef.current?.resize()
  }, [visible])

  // Stable key so this only re-fits when the ACTUAL bounds values
  // change (not on every unrelated re-render that recomputes an
  // equivalent-but-referentially-new `fitBounds` object) -- same
  // discipline as SceneView's vectorOverlaysKey.
  const fitBoundsKey = fitBounds ? `${fitBounds.west},${fitBounds.south},${fitBounds.east},${fitBounds.north}` : null
  useEffect(() => {
    const map = mapInstance
    if (!map || !fitBounds) return
    map.fitBounds(toMapLibreBounds(fitBounds), { padding: 48, animate: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapInstance, fitBoundsKey])

  return (
    <div className={visible ? 'h-full w-full' : 'hidden'}>
      <div ref={containerRef} className="h-full w-full" data-testid="map-2d-container" />
      <MapContext.Provider value={mapInstance}>{children}</MapContext.Provider>
    </div>
  )
}
