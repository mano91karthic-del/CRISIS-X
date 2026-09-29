import { test, expect, type APIRequestContext } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const API_BASE = 'http://localhost:8000'

/** Seeds a project via the real Phase 2->10 pipeline through the live
 * API (the exact same sequence apps/api/tests/test_scenario_lab_api.py's
 * own full-pipeline smoke test exercises), then hands back the ids the
 * spec needs to drive the dashboard UI. This is the first test in the
 * project to drive that pipeline through the UI rather than only the API.
 */
async function seedProject(request: APIRequestContext) {
  const project = await (await request.post(`${API_BASE}/projects`, { data: { name: 'E2E Smoke Project' } })).json()

  const demPath = path.join(__dirname, 'fixtures/dem.tif')
  const demUploadResp = await request.post(`${API_BASE}/projects/${project.id}/datasets`, {
    multipart: { dataset_type: 'dem', file: { name: 'dem.tif', mimeType: 'image/tiff', buffer: fs.readFileSync(demPath) } },
  })
  const dem = await demUploadResp.json()
  expect(demUploadResp.ok()).toBeTruthy()

  const slopeResp = await request.post(`${API_BASE}/datasets/${dem.id}/derive`, { data: { product: 'slope' } })
  expect(slopeResp.ok()).toBeTruthy()

  const hazardResp = await request.post(`${API_BASE}/projects/${project.id}/hazard-scenarios/landslide`, {
    data: { name: 'E2E landslide', dem_dataset_id: dem.id },
  })
  const hazardBody = await hazardResp.json()
  expect(hazardResp.ok()).toBeTruthy()
  const landslide = hazardBody.datasets[0]

  const roadsPath = path.join(__dirname, 'fixtures/roads.geojson')
  const roadsUploadResp = await request.post(`${API_BASE}/projects/${project.id}/datasets`, {
    multipart: {
      dataset_type: 'roads',
      file: { name: 'roads.geojson', mimeType: 'application/geo+json', buffer: fs.readFileSync(roadsPath) },
    },
  })
  const roads = await roadsUploadResp.json()
  expect(roadsUploadResp.ok()).toBeTruthy()

  const exposureResp = await request.post(`${API_BASE}/projects/${project.id}/exposure-analyses`, {
    data: { name: 'E2E exposure', hazard_dataset_id: landslide.id, exposure_dataset_id: roads.id },
  })
  const exposureBody = await exposureResp.json()
  expect(exposureResp.ok()).toBeTruthy()

  const riskResp = await request.post(`${API_BASE}/projects/${project.id}/risk-analyses`, {
    data: {
      name: 'E2E risk',
      exposure_analysis_id: exposureBody.analysis.id,
      vulnerability_weight: 0.6,
      consequence_weight: 0.9,
    },
  })
  expect(riskResp.ok()).toBeTruthy()

  const twinResp = await request.post(`${API_BASE}/projects/${project.id}/digital-twin`, { data: { name: 'E2E Twin' } })
  const twin = await twinResp.json()
  expect(twinResp.ok()).toBeTruthy()

  for (const datasetId of [dem.id, landslide.id, roads.id]) {
    const registerResp = await request.post(`${API_BASE}/digital-twins/${twin.id}/layers`, { data: { dataset_id: datasetId } })
    expect(registerResp.ok()).toBeTruthy()
  }

  return { project, twin }
}

test('Command Dashboard renders a seeded twin: 2D map layers + hazard info panel', async ({ page, request }) => {
  const { project, twin } = await seedProject(request)

  await page.goto(`/dashboard/projects/${project.id}`)

  // Layer control panel lists the categories the seeded twin actually has.
  await expect(page.getByText('terrain', { exact: true })).toBeVisible()
  await expect(page.getByText('hazard', { exact: true })).toBeVisible()
  await expect(page.getByText('observation', { exact: true })).toBeVisible()

  // Toggle the landslide hazard layer visible and confirm MapLibre actually
  // registered a new source for it (the one thing Vitest/jsdom cannot
  // verify). Targets the checkbox by its accessible name directly
  // (LayerControlItem sets aria-label="Toggle {datasetType} layer
  // visibility") rather than a text-containing div, which would be
  // ambiguous -- an ancestor wrapper div also contains that text and
  // multiple checkboxes.
  await page.getByRole('checkbox', { name: /landslide_susceptibility/i }).check()

  await page.waitForFunction(() => {
    const map = (window as unknown as { __map?: { getStyle: () => { sources: Record<string, unknown> } } }).__map
    if (!map) return false
    return Object.keys(map.getStyle().sources).some((id) => id.includes('ds-'))
  })

  const sourceIds = await page.evaluate(() => {
    const map = (window as unknown as { __map: { getStyle: () => { sources: Record<string, unknown> } } }).__map
    return Object.keys(map.getStyle().sources)
  })
  expect(sourceIds.some((id) => id.startsWith('ds-'))).toBeTruthy()

  // The map canvas itself has non-trivial pixel content (not a blank frame).
  const canvasDataUrl = await page.locator('[data-testid="map-2d-container"] canvas').first().evaluate((el) => {
    const canvas = el as HTMLCanvasElement
    return canvas.toDataURL()
  })
  expect(canvasDataUrl.length).toBeGreaterThan(1000)

  // Hazard info panel shows this analysis's real limitations text --
  // proves the panel is reading a genuine API response, not a fixture.
  // (Two real limitation strings both match "susceptibility screening",
  // proving this is live backend data, not a stub -- getAllByText
  // avoids the strict-mode ambiguity that proves.)
  await page.getByRole('tab', { name: 'Hazard' }).click()
  await expect(page.getByText(/susceptibility screening/i).first()).toBeVisible()
  expect(await page.getByText(/susceptibility screening/i).count()).toBeGreaterThanOrEqual(2)

  expect(twin.id).toBeTruthy() // twin was created and is reachable from this page's URL context
})

// Phase 11 "3D terrain invisible" bug: compute_heightmap_bounds()/build_heightmap()
// reported a geographic DEM's raw degree-sized pixel spacing as if it were
// already meters, so a real-world DEM was built as a sub-meter Three.js mesh --
// invisible against the camera. This spec proves the 3D view actually renders
// terrain at a sane, visible scale, not just that its HTTP requests returned 200.
test('Command Dashboard 3D view renders the seeded DEM as a visible, correctly scaled mesh', async ({ page, request }) => {
  const { project } = await seedProject(request)

  await page.goto(`/dashboard/projects/${project.id}`)

  await page.getByRole('tab', { name: '3d' }).click()
  await page.getByRole('checkbox', { name: /toggle dem layer visibility/i }).check()

  const container = page.locator('[data-testid="scene-3d-container"]')
  await expect(container).toBeVisible()
  await expect(container.locator('canvas')).toBeVisible()

  // Wait for the terrain mesh to actually be built (async: fetches
  // heightmap-bounds + heightmap.png, then constructs geometry).
  await page.waitForFunction(() => {
    const scene = (window as unknown as { __scene?: { children: { type: string }[] } }).__scene
    return !!scene && scene.children.some((c) => c.type === 'Mesh')
  })

  // Read the terrain mesh's real-world size against the camera's distance
  // to it -- this is exactly the ratio the original bug broke: a ~1-meter
  // mesh viewed from ~943 world-units away has a radius/distance ratio of
  // roughly 0.001, invisible in the viewport. A correctly scaled mesh
  // occupies a substantial fraction of the camera's view. This is a
  // stronger, non-flaky proof than sampling canvas.toDataURL() (which
  // can't distinguish "rendered background only" from "rendered terrain",
  // and is sensitive to WebGL preserveDrawingBuffer/compositing timing).
  const framing = await page.evaluate(() => {
    type Vec3Like = { x: number; y: number; z: number; distanceTo: (v: Vec3Like) => number; clone: () => Vec3Like }
    type MeshLike = {
      type: string
      geometry: { computeBoundingSphere: () => void; boundingSphere: { center: Vec3Like; radius: number } | null }
      localToWorld: (v: Vec3Like) => Vec3Like
    }
    const win = window as unknown as {
      __scene?: { children: MeshLike[] }
      __camera?: { position: Vec3Like }
    }
    const scene = win.__scene
    const camera = win.__camera
    if (!scene || !camera) return null
    const mesh = scene.children.find((c) => c.type === 'Mesh')
    if (!mesh) return null
    mesh.geometry.computeBoundingSphere()
    const sphere = mesh.geometry.boundingSphere
    if (!sphere) return null
    const worldCenter = mesh.localToWorld(sphere.center.clone())
    const distance = camera.position.distanceTo(worldCenter)
    return { radius: sphere.radius, distance }
  })

  expect(framing).not.toBeNull()
  expect(framing!.radius).toBeGreaterThan(0)
  expect(framing!.distance).toBeGreaterThan(0)
  expect(Number.isFinite(framing!.radius)).toBe(true)
  expect(Number.isFinite(framing!.distance)).toBe(true)
  expect(framing!.radius / framing!.distance).toBeGreaterThan(0.05)
})

// New visualization-integration phase: 3D road/route draping
// (terrainOverlay.ts). The e2e fixture DEM is a projected-CRS raster
// (EPSG:32643, matching its existing coordinate-system test coverage
// elsewhere) -- toggling the roads layer must NOT silently misplace
// overlay geometry against a CRS the coordinate transform doesn't
// support; it must gracefully add nothing. The correctness of the
// transform itself for a geographic-CRS DEM (the real-world case) is
// covered by terrainOverlay.test.ts's unit tests.
test('Command Dashboard 3D view does not add vector overlay geometry for a projected-CRS DEM', async ({ page, request }) => {
  const { project } = await seedProject(request)

  await page.goto(`/dashboard/projects/${project.id}`)
  await page.getByRole('tab', { name: '3d' }).click()
  await page.getByRole('checkbox', { name: /toggle dem layer visibility/i }).check()

  await page.waitForFunction(() => {
    const scene = (window as unknown as { __scene?: { children: { type: string }[] } }).__scene
    return !!scene && scene.children.some((c) => c.type === 'Mesh')
  })

  // Toggle roads visible -- for this projected-CRS DEM, no overlay Group
  // should ever be added (isGeographicNativeCrs guards this).
  await page.getByRole('checkbox', { name: /toggle roads layer visibility/i }).check()
  await page.waitForTimeout(300) // let any (incorrect) async overlay build settle, if it were to happen

  const childTypes = await page.evaluate(() => {
    const scene = (window as unknown as { __scene?: { children: { type: string }[] } }).__scene
    return scene ? scene.children.map((c) => c.type) : []
  })
  expect(childTypes.filter((t) => t === 'Group')).toHaveLength(0)
})

// Phase 12: AI Assistant. Uses the app's default FakeProvider (see
// apps/api/app/api/assistant.py::get_assistant_provider) -- no real AI
// provider/API key/network access is configured anywhere in this test
// run, proving the whole flow works fully offline and deterministically.
test('Command Dashboard Assistant tab answers a question with evidence, no real AI provider required', async ({ page, request }) => {
  const { project } = await seedProject(request)

  await page.goto(`/dashboard/projects/${project.id}`)

  await page.getByRole('tab', { name: 'Assistant' }).click()
  await page.getByLabel('Ask about this project').fill('What hazards are available for this project?')
  await page.getByRole('button', { name: /^ask$/i }).click()

  // Evidence/sources must still appear (secondary section) -- the
  // assistant is grounded in real tool calls against the seeded
  // project, not a canned static reply.
  await expect(page.getByText(/Evidence \/ sources/)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('list_hazard_scenarios', { exact: true })).toBeVisible()

  // The visible answer must read conversationally: it names the actual
  // seeded hazard, and never leaks internal tool names or "returned
  // data" tool-log phrasing into the prose itself.
  const answer = page.getByTestId('assistant-answer')
  await expect(answer).toBeVisible()
  const answerText = (await answer.innerText()).trim()
  expect(answerText.length).toBeGreaterThan(0)
  expect(answerText.toLowerCase()).toContain('landslide')
  expect(answerText).not.toContain('returned data')
  for (const toolName of [
    'get_twin_state',
    'list_hazard_scenarios',
    'list_exposure_analyses',
    'list_risk_analyses',
    'list_route_analyses',
    'find_highest_risk_classes',
  ]) {
    expect(answerText).not.toContain(toolName)
  }
})
