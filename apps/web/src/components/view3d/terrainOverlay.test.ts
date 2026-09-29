import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  buildLineFeatureOverlay,
  buildPolygonFeatureOverlay,
  buildRibbonFeatureOverlay,
  buildRouteMarker,
  computeCameraFramingForBox,
  createElevationSampler,
  createTerrainLocalTransform,
  disposeLineFeatureOverlay,
  filterFeaturesByBbox,
  isGeographicNativeCrs,
  FALLBACK_BUILDING_HEIGHT_M,
  DISPLAY_BUILDING_HEIGHT_MULTIPLIER,
  ROAD_WIDTH_BY_HIGHWAY_CLASS_M,
  DEFAULT_ROAD_WIDTH_M,
  ROUTE_WIDTH_M,
} from './terrainOverlay'

describe('isGeographicNativeCrs', () => {
  it('recognizes EPSG:4326 in its common string forms', () => {
    expect(isGeographicNativeCrs('EPSG:4326')).toBe(true)
    expect(isGeographicNativeCrs('WGS 84')).toBe(true)
    expect(isGeographicNativeCrs('epsg:4326')).toBe(true)
  })

  it('rejects a projected CRS', () => {
    expect(isGeographicNativeCrs('EPSG:32643')).toBe(false)
    expect(isGeographicNativeCrs('EPSG:32644')).toBe(false)
  })
})

describe('createTerrainLocalTransform', () => {
  const transform = createTerrainLocalTransform({
    boundsWestLon: 80.0,
    boundsSouthLat: 13.0,
    boundsEastLon: 80.1,
    boundsNorthLat: 13.1,
    worldWidthM: 1000,
    worldHeightM: 1000,
  })

  it('maps the bounds center to local origin', () => {
    const [x, z] = transform.toLocalXZ(80.05, 13.05)
    expect(x).toBeCloseTo(0, 5)
    expect(z).toBeCloseTo(0, 5)
  })

  it('maps west to negative X and east to positive X', () => {
    const [xWest] = transform.toLocalXZ(80.0, 13.05)
    const [xEast] = transform.toLocalXZ(80.1, 13.05)
    expect(xWest).toBeCloseTo(-500, 5)
    expect(xEast).toBeCloseTo(500, 5)
  })

  it('maps north to negative Z and south to positive Z (matches the terrain mesh rotation)', () => {
    const [, zNorth] = transform.toLocalXZ(80.05, 13.1)
    const [, zSouth] = transform.toLocalXZ(80.05, 13.0)
    expect(zNorth).toBeCloseTo(-500, 5)
    expect(zSouth).toBeCloseTo(500, 5)
  })

  it('scales proportionally to the terrain footprint, not a fixed constant', () => {
    const wide = createTerrainLocalTransform({
      boundsWestLon: 0,
      boundsSouthLat: 0,
      boundsEastLon: 1,
      boundsNorthLat: 1,
      worldWidthM: 111_000,
      worldHeightM: 111_000,
    })
    const [x] = wide.toLocalXZ(1, 0.5) // east edge
    expect(x).toBeCloseTo(55_500, 0)
  })
})

describe('createElevationSampler', () => {
  const pixels = { width: 2, height: 2, values: new Uint8Array([0, 255, 0, 255]) } // row0: [0,255], row1: [0,255]

  it('samples the west pixel as elevationMin and east pixel as elevationMax', () => {
    const sample = createElevationSampler({
      pixels,
      elevationMinM: 0,
      elevationMaxM: 100,
      exaggeration: 1,
      boundsWestLon: 0,
      boundsSouthLat: 0,
      boundsEastLon: 1,
      boundsNorthLat: 1,
    })
    expect(sample(0, 0.5)).toBeCloseTo(0, 0)
    expect(sample(1, 0.5)).toBeCloseTo(100, 0)
  })

  it('applies exaggeration multiplicatively', () => {
    const sample = createElevationSampler({
      pixels,
      elevationMinM: 0,
      elevationMaxM: 100,
      exaggeration: 3,
      boundsWestLon: 0,
      boundsSouthLat: 0,
      boundsEastLon: 1,
      boundsNorthLat: 1,
    })
    expect(sample(1, 0.5)).toBeCloseTo(300, 0)
  })

  it('clamps out-of-bounds lon/lat to the nearest edge pixel rather than throwing', () => {
    const sample = createElevationSampler({
      pixels,
      elevationMinM: 0,
      elevationMaxM: 100,
      exaggeration: 1,
      boundsWestLon: 0,
      boundsSouthLat: 0,
      boundsEastLon: 1,
      boundsNorthLat: 1,
    })
    expect(() => sample(-5, 50)).not.toThrow()
    expect(sample(-5, 0.5)).toBeCloseTo(0, 0)
  })
})

describe('computeCameraFramingForBox', () => {
  it('targets the box center', () => {
    const box = new THREE.Box3(new THREE.Vector3(-100, 0, -50), new THREE.Vector3(100, 20, 50))
    const framing = computeCameraFramingForBox(box)
    expect(framing.target).toEqual([0, 10, 0])
  })

  it('positions the camera proportionally further for a larger box', () => {
    const small = computeCameraFramingForBox(new THREE.Box3(new THREE.Vector3(-10, 0, -10), new THREE.Vector3(10, 0, 10)))
    const large = computeCameraFramingForBox(
      new THREE.Box3(new THREE.Vector3(-1000, 0, -1000), new THREE.Vector3(1000, 0, 1000)),
    )
    const smallDist = Math.hypot(...small.position)
    const largeDist = Math.hypot(...large.position)
    expect(largeDist).toBeGreaterThan(smallDist * 50)
  })

  it('never produces a degenerate frame for a zero-size (single-point) box', () => {
    const box = new THREE.Box3(new THREE.Vector3(5, 5, 5), new THREE.Vector3(5, 5, 5))
    const framing = computeCameraFramingForBox(box)
    for (const v of [...framing.position, ...framing.target]) {
      expect(Number.isFinite(v)).toBe(true)
    }
    expect(framing.far).toBeGreaterThan(0)
  })

  it('keeps far beyond the camera distance from target', () => {
    const box = new THREE.Box3(new THREE.Vector3(-500, -10, -300), new THREE.Vector3(500, 50, 300))
    const framing = computeCameraFramingForBox(box)
    const dist = Math.hypot(
      framing.position[0] - framing.target[0],
      framing.position[1] - framing.target[1],
      framing.position[2] - framing.target[2],
    )
    expect(framing.far).toBeGreaterThan(dist)
  })
})

describe('buildLineFeatureOverlay', () => {
  const transform = createTerrainLocalTransform({
    boundsWestLon: 0,
    boundsSouthLat: 0,
    boundsEastLon: 1,
    boundsNorthLat: 1,
    worldWidthM: 1000,
    worldHeightM: 1000,
  })
  const flatSampler = () => 10

  it('builds one THREE.Line per LineString feature', () => {
    const geojson = {
      features: [
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]] } },
        { geometry: { type: 'LineString', coordinates: [[0.2, 0.8], [0.8, 0.2]] } },
      ],
    }
    const group = buildLineFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#ff0000' })
    expect(group.children).toHaveLength(2)
    expect(group.children.every((c) => c instanceof THREE.Line)).toBe(true)
    disposeLineFeatureOverlay(group)
  })

  it('expands a MultiLineString into multiple lines', () => {
    const geojson = {
      features: [
        {
          geometry: {
            type: 'MultiLineString',
            coordinates: [
              [[0.1, 0.1], [0.2, 0.2]],
              [[0.5, 0.5], [0.6, 0.6]],
            ],
          },
        },
      ],
    }
    const group = buildLineFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#00ff00' })
    expect(group.children).toHaveLength(2)
  })

  it('skips non-line geometry and null geometry without throwing', () => {
    const geojson = {
      features: [
        { geometry: { type: 'Point', coordinates: [0.5, 0.5] } },
        { geometry: null },
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.2, 0.2]] } },
      ],
    }
    const group = buildLineFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#0000ff' })
    expect(group.children).toHaveLength(1)
  })

  it('raises the line above the sampled terrain elevation by heightOffsetM', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.5, 0.5], [0.6, 0.6]] } }] }
    const group = buildLineFeatureOverlay({
      geojson,
      transform,
      sampleElevationM: () => 50,
      datasetId: 'test-dataset', color: '#fff',
      heightOffsetM: 5,
    })
    const line = group.children[0] as THREE.Line
    const position = line.geometry.getAttribute('position')
    expect(position.getY(0)).toBeCloseTo(55, 5)
  })

  it('colors each feature by its own colorProperty value through the shared legend, not a single flat color', () => {
    const geojson = {
      features: [
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.2, 0.2]] }, properties: { risk_class: 'very_low' } },
        { geometry: { type: 'LineString', coordinates: [[0.3, 0.3], [0.4, 0.4]] }, properties: { risk_class: 'very_high' } },
      ],
    }
    const group = buildLineFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#64748b', colorProperty: 'risk_class' })
    const colors = group.children.map((c) => `#${((c as THREE.Line).material as THREE.LineBasicMaterial).color.getHexString()}`)
    expect(colors).toContain('#2c7bb6') // very_low, from geo/legendColors.ts
    expect(colors).toContain('#d7191c') // very_high
    expect(colors[0]).not.toBe(colors[1])
  })

  it('falls back to the flat color for a feature missing the colorProperty value', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.2, 0.2]] }, properties: {} }] }
    const group = buildLineFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#123456', colorProperty: 'risk_class' })
    const line = group.children[0] as THREE.Line
    expect(`#${(line.material as THREE.LineBasicMaterial).color.getHexString()}`).toBe('#123456')
  })
})

describe('buildPolygonFeatureOverlay', () => {
  const transform = createTerrainLocalTransform({
    boundsWestLon: 0,
    boundsSouthLat: 0,
    boundsEastLon: 1,
    boundsNorthLat: 1,
    worldWidthM: 1000,
    worldHeightM: 1000,
  })
  const flatSampler = () => 20

  it('builds one flat mesh per polygon feature, at the sampled elevation', () => {
    const geojson = {
      features: [
        {
          geometry: {
            type: 'Polygon',
            coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.12], [0.1, 0.1]]],
          },
        },
      ],
    }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#ff0000', heightOffsetM: 2 })
    expect(group.children).toHaveLength(1)
    const mesh = group.children[0] as THREE.Mesh
    expect(mesh).toBeInstanceOf(THREE.Mesh)
    const position = mesh.geometry.getAttribute('position')
    for (let i = 0; i < position.count; i++) {
      expect(position.getY(i)).toBeCloseTo(22, 5) // flat: sampled elevation (20) + heightOffsetM (2)
    }
    expect(mesh.geometry.getIndex()!.count).toBeGreaterThan(0) // real triangulated faces, not just a point cloud
  })

  it('expands a MultiPolygon into multiple meshes', () => {
    const square = [[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.12], [0.1, 0.1]]
    const geojson = {
      features: [
        { geometry: { type: 'MultiPolygon', coordinates: [[square], [square.map(([x, y]) => [x + 0.5, y + 0.5])]] } },
      ],
    }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#00ff00' })
    expect(group.children).toHaveLength(2)
  })

  it('handles a polygon with a hole without crashing, incorporating the hole ring into the triangulated mesh', () => {
    const outer = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]] as [number, number][]
    const hole = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7], [0.3, 0.3]] as [number, number][]
    const solidGroup = buildPolygonFeatureOverlay({
      geojson: { features: [{ geometry: { type: 'Polygon', coordinates: [outer] } }] },
      transform,
      sampleElevationM: flatSampler,
      datasetId: 'test-dataset', color: '#fff',
    })
    const holeGroup = buildPolygonFeatureOverlay({
      geojson: { features: [{ geometry: { type: 'Polygon', coordinates: [outer, hole] } }] },
      transform,
      sampleElevationM: flatSampler,
      datasetId: 'test-dataset', color: '#fff',
    })
    const solidVertexCount = (solidGroup.children[0] as THREE.Mesh).geometry.getAttribute('position').count
    const holeVertexCount = (holeGroup.children[0] as THREE.Mesh).geometry.getAttribute('position').count
    const holeTriCount = (holeGroup.children[0] as THREE.Mesh).geometry.getIndex()!.count
    expect(holeTriCount).toBeGreaterThan(0)
    // The hole ring's own vertices are incorporated into the mesh (exact
    // count isn't asserted: THREE.ShapeUtils.triangulateShape strips
    // each ring's redundant closing point as a side effect).
    expect(holeVertexCount).toBeGreaterThan(solidVertexCount)
  })

  it('skips non-polygon geometry and null geometry without throwing', () => {
    const geojson = {
      features: [
        { geometry: { type: 'Point', coordinates: [0.5, 0.5] } },
        { geometry: null },
        { geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] } },
      ],
    }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'test-dataset', color: '#0000ff' })
    expect(group.children).toHaveLength(1)
  })

  it('colors each feature by its deterministic building style', () => {
    const square = (offset: number) => [[0.1 + offset, 0.1], [0.12 + offset, 0.1], [0.12 + offset, 0.12], [0.1 + offset, 0.12], [0.1 + offset, 0.1]]
    const geojson = {
      features: [
        { geometry: { type: 'Polygon', coordinates: [square(0)] }, properties: { building_id: 'B00001' } },
        { geometry: { type: 'Polygon', coordinates: [square(0.3)] }, properties: { building_id: 'B00002' } },
      ],
    }
    const group = buildPolygonFeatureOverlay({
      geojson,
      transform,
      sampleElevationM: flatSampler,
      datasetId: 'test-dataset', color: '#64748b',
    })
    const colors = group.children.map((c) => `#${((c as THREE.Mesh).material as THREE.MeshStandardMaterial).color.getHexString()}`)
    // Each building gets a color from its deterministic style
    expect(colors).toHaveLength(2)
    expect(colors[0]).not.toBe(colors[1]) // Different styles = different colors
  })

  it('tags every mesh with OverlayFeatureUserData for click-to-select', () => {
    const geojson = {
      features: [
        { id: 'b-1', geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] }, properties: { foo: 'bar' } },
      ],
    }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'buildings-123', color: '#fff' })
    const mesh = group.children[0] as THREE.Mesh
    expect(mesh.userData).toMatchObject({ datasetId: 'buildings-123', featureId: 'b-1', properties: { foo: 'bar' } })
  })

  it('renders flat (topY === baseY) when heightM is omitted, preserving the original non-extruded behavior', () => {
    const geojson = { features: [{ geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] } }] }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', heightOffsetM: 0 })
    const mesh = group.children[0] as THREE.Mesh
    const position = mesh.geometry.getAttribute('position')
    for (let i = 0; i < position.count; i++) expect(position.getY(i)).toBeCloseTo(20, 5) // flat: no heightM passed, sampled elevation only
  })

  it('extrudes to heightM above the base when passed, adding side-wall vertices beyond just the top cap', () => {
    const geojson = { features: [{ geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.12], [0.1, 0.1]]] } }] }
    const flat = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', heightOffsetM: 0 })
    const extruded = buildPolygonFeatureOverlay({
      geojson,
      transform,
      sampleElevationM: flatSampler,
      datasetId: 'd',
      color: '#fff',
      heightOffsetM: 0,
      heightM: FALLBACK_BUILDING_HEIGHT_M,
    })

    const flatVertexCount = (flat.children[0] as THREE.Mesh).geometry.getAttribute('position').count
    const extrudedMesh = extruded.children[0] as THREE.Mesh
    const extrudedPosition = extrudedMesh.geometry.getAttribute('position')
    expect(extrudedPosition.count).toBeGreaterThan(flatVertexCount) // side walls added real geometry

    const ys = new Set<number>()
    for (let i = 0; i < extrudedPosition.count; i++) ys.add(Math.round(extrudedPosition.getY(i) * 100) / 100)
    expect(ys.has(20)).toBe(true) // base (flatSampler's 20, heightOffsetM: 0)
    expect(ys.has(20 + FALLBACK_BUILDING_HEIGHT_M * DISPLAY_BUILDING_HEIGHT_MULTIPLIER)).toBe(true) // top (fallback height x display multiplier)
  })

  it('uses a real heightProperty value over the fallback heightM when present', () => {
    const geojson = {
      features: [
        {
          geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] },
          properties: { real_height_m: 15 },
        },
      ],
    }
    const group = buildPolygonFeatureOverlay({
      geojson,
      transform,
      sampleElevationM: flatSampler,
      datasetId: 'd',
      color: '#fff',
      heightOffsetM: 0,
      heightProperty: 'real_height_m',
      heightM: FALLBACK_BUILDING_HEIGHT_M,
    })
    const mesh = group.children[0] as THREE.Mesh
    const position = mesh.geometry.getAttribute('position')
    let maxY = -Infinity
    for (let i = 0; i < position.count; i++) maxY = Math.max(maxY, position.getY(i))
    expect(maxY).toBeCloseTo(35, 1) // 20 (base) + 15 (real height, NOT the 8m fallback)
    expect((mesh.userData as { heightSource: string }).heightSource).toBe('real')
  })

  it('tags an extruded mesh with heightSource "fallback" when only the uniform heightM was used', () => {
    const geojson = { features: [{ geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] } }] }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', heightM: FALLBACK_BUILDING_HEIGHT_M })
    const mesh = group.children[0] as THREE.Mesh
    expect((mesh.userData as { heightSource: string }).heightSource).toBe('fallback')
  })

  it('surfaces the height provenance inside userData.properties, not just a sibling field, so it actually appears in the Feature Inspector panel', () => {
    const geojson = { features: [{ id: 'b-1', geometry: { type: 'Polygon', coordinates: [[[0.1, 0.1], [0.12, 0.1], [0.12, 0.12], [0.1, 0.1]]] }, properties: { corporation: 'Chennai' } }] }
    const group = buildPolygonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', heightM: FALLBACK_BUILDING_HEIGHT_M })
    const mesh = group.children[0] as THREE.Mesh
    const userData = mesh.userData as { properties: Record<string, unknown> }
    expect(userData.properties.corporation).toBe('Chennai') // original real properties preserved
    expect(String(userData.properties.visualization_height_source)).toContain('placeholder')
  })
})

describe('buildRibbonFeatureOverlay', () => {
  const transform = createTerrainLocalTransform({
    boundsWestLon: 0,
    boundsSouthLat: 0,
    boundsEastLon: 1,
    boundsNorthLat: 1,
    worldWidthM: 1000,
    worldHeightM: 1000,
  })
  const flatSampler = () => 10

  it('builds a Mesh (not a hairline Line) per LineString feature, with real triangulated width', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]] } }] }
    const group = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthM: 5 })
    expect(group.children).toHaveLength(1)
    const mesh = group.children[0] as THREE.Mesh
    expect(mesh).toBeInstanceOf(THREE.Mesh)
    expect(mesh.geometry.getIndex()!.count).toBeGreaterThan(0)
  })

  it('produces a wider footprint (larger XZ bounding box) for a larger widthM', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.1, 0.5], [0.9, 0.5]] } }] }
    const narrow = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthM: 1 })
    const wide = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthM: 20 })
    const bboxZSpan = (group: THREE.Group) => {
      const box = new THREE.Box3().setFromObject(group)
      return box.max.z - box.min.z
    }
    expect(bboxZSpan(wide)).toBeGreaterThan(bboxZSpan(narrow))
  })

  it('resolves per-feature width from widthProperty via ROAD_WIDTH_BY_HIGHWAY_CLASS_M', () => {
    const geojson = {
      features: [
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.5], [0.9, 0.5]] }, properties: { highway: 'footway' } },
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.9, 0.1]] }, properties: { highway: 'trunk' } },
      ],
    }
    const group = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthProperty: 'highway' })
    const zSpanOf = (mesh: THREE.Mesh) => {
      const box = new THREE.Box3().setFromObject(mesh)
      return box.max.z - box.min.z
    }
    expect(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.trunk).toBeGreaterThan(ROAD_WIDTH_BY_HIGHWAY_CLASS_M.footway)
    expect(zSpanOf(group.children[1] as THREE.Mesh)).toBeGreaterThan(zSpanOf(group.children[0] as THREE.Mesh))
  })

  it('falls back to DEFAULT_ROAD_WIDTH_M for a feature missing the widthProperty value', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.1, 0.5], [0.9, 0.5]] }, properties: {} }] }
    const withDefault = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthProperty: 'highway' })
    const withFixed = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff', widthM: DEFAULT_ROAD_WIDTH_M })
    const zSpan = (group: THREE.Group) => {
      const box = new THREE.Box3().setFromObject(group)
      return box.max.z - box.min.z
    }
    expect(zSpan(withDefault)).toBeCloseTo(zSpan(withFixed), 3)
  })

  it('uses the fixed ROUTE_WIDTH_M consistently regardless of any widthProperty on route features', () => {
    const geojson = { features: [{ geometry: { type: 'LineString', coordinates: [[0.1, 0.5], [0.9, 0.5]] } }] }
    const group = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#22d3ee', widthM: ROUTE_WIDTH_M })
    const box = new THREE.Box3().setFromObject(group)
    expect(box.max.z - box.min.z).toBeCloseTo(ROUTE_WIDTH_M, 3)
  })

  it('skips non-line and null geometry without throwing', () => {
    const geojson = {
      features: [
        { geometry: { type: 'Point', coordinates: [0.5, 0.5] } },
        { geometry: null },
        { geometry: { type: 'LineString', coordinates: [[0.1, 0.1], [0.2, 0.2]] } },
      ],
    }
    const group = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'd', color: '#fff' })
    expect(group.children).toHaveLength(1)
  })

  it('tags each mesh with OverlayFeatureUserData for click-to-select', () => {
    const geojson = { features: [{ id: 42, geometry: { type: 'LineString', coordinates: [[0.1, 0.5], [0.9, 0.5]] }, properties: { name: 'Main St' } }] }
    const group = buildRibbonFeatureOverlay({ geojson, transform, sampleElevationM: flatSampler, datasetId: 'roads-1', color: '#fff' })
    const mesh = group.children[0] as THREE.Mesh
    expect(mesh.userData).toMatchObject({ datasetId: 'roads-1', featureId: 42, properties: { name: 'Main St' } })
  })
})

describe('buildRouteMarker', () => {
  const transform = createTerrainLocalTransform({
    boundsWestLon: 0,
    boundsSouthLat: 0,
    boundsEastLon: 1,
    boundsNorthLat: 1,
    worldWidthM: 1000,
    worldHeightM: 1000,
  })

  it('places a cone mesh at the given lon/lat, above the sampled terrain elevation', () => {
    const marker = buildRouteMarker({ lonLat: [0.5, 0.5], transform, sampleElevationM: () => 30, color: '#22c55e' })
    expect(marker).toBeInstanceOf(THREE.Mesh)
    expect(marker.geometry).toBeInstanceOf(THREE.ConeGeometry)
    expect(marker.position.x).toBeCloseTo(0, 5) // lon 0.5 is the bounds center -> local x=0
    expect(marker.position.y).toBeGreaterThan(30) // sits above the base elevation (cone extends upward from its base)
  })

  it('uses the requested color', () => {
    const marker = buildRouteMarker({ lonLat: [0.5, 0.5], transform, sampleElevationM: () => 0, color: '#ef4444' })
    const material = marker.material as THREE.MeshStandardMaterial
    expect(`#${material.color.getHexString()}`).toBe('#ef4444')
  })
})

describe('filterFeaturesByBbox', () => {
  const bbox = { west: 80.0, south: 13.0, east: 80.1, north: 13.1 }

  it('keeps a feature whose point falls inside the bbox', () => {
    const features = [{ geometry: { type: 'Point', coordinates: [80.05, 13.05] } }]
    expect(filterFeaturesByBbox(features, bbox)).toHaveLength(1)
  })

  it('drops a feature entirely outside the bbox', () => {
    const features = [{ geometry: { type: 'Point', coordinates: [81.5, 13.05] } }]
    expect(filterFeaturesByBbox(features, bbox)).toHaveLength(0)
  })

  it('keeps a polygon feature with at least one vertex inside the bbox, even if others fall outside', () => {
    const features = [
      {
        geometry: {
          type: 'Polygon',
          coordinates: [[[80.05, 13.05], [82.0, 13.05], [82.0, 15.0], [80.05, 13.05]]],
        },
      },
    ]
    expect(filterFeaturesByBbox(features, bbox)).toHaveLength(1)
  })

  it('drops a null-geometry feature', () => {
    const features = [{ geometry: null }]
    expect(filterFeaturesByBbox(features, bbox)).toHaveLength(0)
  })

  it('filters a realistic mixed set down to only the intersecting features', () => {
    const inside = { geometry: { type: 'Point', coordinates: [80.02, 13.02] } }
    const outside = { geometry: { type: 'Point', coordinates: [79.0, 12.0] } }
    const result = filterFeaturesByBbox([inside, outside, inside], bbox)
    expect(result).toHaveLength(2)
  })
})
