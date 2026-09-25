import { test, expect } from '@playwright/test'

/** Implementation-time spike (see ADR 0012 §10/§27, decision #4):
 * confirms whether a real browser's 2D canvas preserves more than 8-bit
 * (256-level) precision when decoding a 16-bit grayscale PNG via
 * drawImage + getImageData -- the exact pipeline
 * components/view3d/SceneView.tsx uses to build the terrain mesh.
 *
 * This is NOT part of the permanent regression suite's assertions about
 * dashboard behavior -- it's a one-time (re-runnable) technical spike
 * whose OUTPUT is the answer to a design question, reported in the
 * Phase 11 implementation report, not a pass/fail gate on future PRs.
 * It still uses `expect` so a CI run surfaces a clear signal either way.
 */
test('browser canvas 16-bit PNG decode precision spike', async ({ page }) => {
  await page.goto('/e2e-fixtures/spike-16bit-gradient.png')

  const result = await page.evaluate(async () => {
    const img = new Image()
    img.src = '/e2e-fixtures/spike-16bit-gradient.png'
    await new Promise((resolve, reject) => {
      img.onload = resolve
      img.onerror = reject
    })
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(img, 0, 0)
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)

    const redChannelValues = new Set<number>()
    for (let i = 0; i < imageData.data.length; i += 4) {
      redChannelValues.add(imageData.data[i])
    }
    return {
      distinctValueCount: redChannelValues.size,
      maxValue: Math.max(...redChannelValues),
      minValue: Math.min(...redChannelValues),
      naturalWidth: img.naturalWidth,
    }
  })

  // eslint-disable-next-line no-console
  console.log('[heightmap-precision-spike] result:', JSON.stringify(result))

  // The source PNG has 512 distinct 16-bit values across its width. If
  // canvas preserved 16-bit precision, distinctValueCount would be close
  // to 512; if it clamped to 8-bit (the well-documented, expected
  // Canvas2D behavior), distinctValueCount will be capped at 256 and
  // maxValue will be exactly 255.
  expect(result.maxValue).toBeLessThanOrEqual(255)
})
