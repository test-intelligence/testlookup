/**
 * The opt-in 3D test scatter (VIZ-508): three.js, laid OVER the 2D scatter on
 * Failures (`scatter-project`), which stays mounted underneath. No flag since
 * Phase D, S5: "View in 3D" is offered over every drawn scatter, 2D is the
 * default, and three is fetched only on the first click ((b) is that proof). What a unit test cannot see, here in a real browser under the
 * production CSP `<meta>`:
 *
 *   (a) this browser HAS WebGL 2 — a precondition that FAILS, never skips: a
 *       run without it would pass every case below by testing the fallback;
 *   (b) three is fetched only on "View in 3D", draws, rotates under a mouse
 *       drag, and runs with zero CSP violations and no console error, while
 *       the 2D chart stays drawn;
 *   (c) "Back to 2D" removes the view;
 *   (d) export while the 3D view shows is the 2D chart's (PNG and CSV);
 *   (e) a browser with no WebGL gets the notice and the 2D chart, and three
 *       is never fetched;
 *   (f) the browser taking the context back: the notice, back to 2D.
 *
 * Headless Chromium draws WebGL with SwiftShader only when told to (the two
 * launch flags below; this file only, so no other spec's GPU path changes).
 * The words are retyped from `scatter3d.model.ts`: it imports through the
 * `@/` alias, which Playwright's plain-Node transform cannot resolve.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { EXPORT, exportFile, exportFileName } from '../lib/chart-gallery-page'
import { decodePng } from '../lib/png'
import { cspViolations, watchCsp } from '../lib/csp'
import { bringNear, expectDrawn, networkQuiet, openRollout, section, sectionFrame, watchConsoleErrors } from '../lib/rollout'
import { FAILURES_ON } from '../visual/production/fixtures'

const SCATTER = { id: 'scatter-project', title: 'Test duration vs failure rate' } as const

const VIEW_3D = 'View in 3D'
const VIEW_2D = 'Back to 2D'
const NO_WEBGL = /needs WebGL 2/
const CONTEXT_LOST = /took its graphics context back/

/**
 * A request for three.js or the engine module that imports it: a dev-server
 * dep (`/node_modules/.vite/deps/three*.js`), an engine source module, or a
 * built chunk. NOT the view's loader (`engines/three/load.ts`): the view
 * imports it statically, so it arrives with the view even when WebGL is
 * missing and the engine is never asked for.
 */
const THREE_REQUEST = /\/\.vite\/deps\/three|\/engines\/three\/(?!load\.ts)|\/assets\/scatter3d-/

test.use({
  viewport: { width: 1280, height: 4000 },
  timezoneId: 'UTC',
  locale: 'en-US',
  reducedMotion: 'reduce',
  // Chromium's software WebGL; other browsers get their defaults.
  launchOptions: async ({ browserName }, use) =>
    use(browserName === 'chromium' ? { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } : {}),
})

const scatter = (page: Page) => section(page, SCATTER.id)
const frame = (page: Page) => sectionFrame(page, SCATTER.id, SCATTER.title)
const scatter2d = (page: Page) => scatter(page).locator('[data-chart-type="scatter"]')
const view3d = (page: Page) => scatter(page).locator('[data-scatter-3d]')
const toggle = (page: Page) => frame(page).locator('[data-scatter-view-toggle]')

/** Opens Failures (no flag set), brings the scatter near and waits for the 2D chart; every three request is recorded. */
async function openFailures(page: Page) {
  const threeRequests: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (THREE_REQUEST.test(path)) threeRequests.push(path)
  })
  const opened = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') })
  await bringNear(page, SCATTER.id)
  await expectDrawn(frame(page), SCATTER.id)
  await expect(scatter2d(page)).toHaveAttribute('data-chart-status', 'ready')
  return { ...opened, threeRequests }
}

/** "View in 3D", and the view drawn. */
async function enter3D(page: Page) {
  await frame(page).getByRole('button', { name: VIEW_3D }).click()
  await expect(view3d(page)).toHaveAttribute('data-chart-status', 'ready', { timeout: 20_000 })
}

test('(a) precondition: this browser has WebGL 2 (fails, never skips: without it every case below tests the fallback)', async ({
  page,
}) => {
  await page.goto('about:blank')
  const webgl2 = await page.evaluate(() => Boolean(document.createElement('canvas').getContext('webgl2')))
  expect(webgl2, 'no WebGL 2: headless Chromium needs --use-angle=swiftshader --enable-unsafe-swiftshader').toBe(true)
})

test('(b) three is fetched only on "View in 3D"; the view draws, rotates under a drag, under the production CSP, with the 2D chart still drawn', async ({
  page,
}) => {
  await watchCsp(page)
  const console = watchConsoleErrors(page)
  const { api, errors, threeRequests } = await openFailures(page)
  // The meta tag the dev server serves is the production policy.
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')
  expect(csp).toContain("script-src 'self'")
  expect(csp).not.toContain('unsafe-eval')
  await networkQuiet(page, api)
  expect(threeRequests, 'three before the reader asked for 3D').toEqual([])
  expect(await cspViolations(page)).toEqual([])
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')

  await enter3D(page)
  expect(threeRequests.length, 'three was fetched for the 3D view').toBeGreaterThan(0)
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(frame(page).getByRole('button', { name: VIEW_2D })).toBeVisible()
  const plot = view3d(page).locator('[data-scatter-3d-plot]')
  await expect(plot.locator('canvas')).toHaveCount(1)
  await expect(view3d(page).locator('[data-scatter-3d-tick]').first()).toBeAttached()
  await expect(view3d(page).locator('[data-scatter-key] [data-quadrant]')).toHaveCount(4)

  // A mouse drag rotates the camera.
  const before = await view3d(page).getAttribute('data-scatter-3d-azimuth')
  expect(before).not.toBeNull()
  const box = await plot.boundingBox()
  expect(box).not.toBeNull()
  if (!box) return
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) await page.mouse.move(box.x + box.width / 2 + i * 15, box.y + box.height / 2)
  await page.mouse.up()
  await expect.poll(() => view3d(page).getAttribute('data-scatter-3d-azimuth')).not.toBe(before)

  // The 2D chart under it is still drawn (and inert).
  await expect(scatter2d(page)).toHaveAttribute('data-chart-status', 'ready')
  expect(await scatter2d(page).evaluate((el) => el.closest('[inert]') !== null)).toBe(true)
  expect(await cspViolations(page)).toEqual([])
  expect(console).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('(c) "Back to 2D" removes the view and leaves the 2D chart drawn and live', async ({ page }) => {
  await openFailures(page)
  await enter3D(page)
  await frame(page).getByRole('button', { name: VIEW_2D }).click()
  await expect(view3d(page)).toHaveCount(0)
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(scatter2d(page)).toHaveAttribute('data-chart-status', 'ready')
  expect(await scatter2d(page).evaluate((el) => el.closest('[inert]') === null)).toBe(true)
})

test('(d) export while the 3D view shows is the 2D chart: a PNG the size of the 2D plot, and the CSV', async ({ page }) => {
  await openFailures(page)
  const plot2d = await scatter2d(page).boundingBox()
  await enter3D(page)
  const png = await exportFile(page, frame(page), EXPORT.png)
  expect(png.name).toMatch(exportFileName('png'))
  const image = decodePng(new Uint8Array(png.bytes))
  // At 2x, at least the 2D plot's width: drawn from the 2D ECharts instance under the view.
  expect(plot2d).not.toBeNull()
  expect(image.width).toBeGreaterThanOrEqual(Math.round((plot2d?.width ?? 0) * 2))
  await expect(frame(page).locator('[data-chart-export-error]')).toHaveCount(0)
  const csv = await exportFile(page, frame(page), EXPORT.csv)
  expect(csv.name).toMatch(exportFileName('csv'))
  expect(csv.bytes.toString('utf-8')).toContain('Failure rate')
  await expect(frame(page).locator('[data-chart-export-error]')).toHaveCount(0)
  // Still in 3D: exporting did not take the view down.
  await expect(view3d(page)).toHaveAttribute('data-chart-status', 'ready')
})

test('(e) no WebGL: the notice, the 2D chart, and three is never fetched', async ({ page }) => {
  // WebGL contexts refused; 2D canvases (ECharts) still work.
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      if (/webgl/i.test(String(type))) return null
      return (original as (...args: unknown[]) => RenderingContext | null).call(this, type, ...rest)
    } as typeof HTMLCanvasElement.prototype.getContext
  })
  const { api, threeRequests } = await openFailures(page)
  await frame(page).getByRole('button', { name: VIEW_3D }).click()
  await expect(scatter(page).locator('[data-scatter-3d-notice]')).toHaveText(NO_WEBGL)
  await expect(view3d(page)).toHaveCount(0)
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(scatter2d(page)).toHaveAttribute('data-chart-status', 'ready')
  await networkQuiet(page, api)
  expect(threeRequests).toEqual([])
})

test('(f) the browser takes the context back: the notice, the view gone, the 2D chart drawn', async ({ page }) => {
  await openFailures(page)
  await enter3D(page)
  const lost = await view3d(page)
    .locator('[data-scatter-3d-plot] canvas')
    .evaluate((canvas: HTMLCanvasElement) => {
      const extension = canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')
      extension?.loseContext()
      return Boolean(extension)
    })
  expect(lost, 'WEBGL_lose_context is available').toBe(true)
  await expect(scatter(page).locator('[data-scatter-3d-notice]')).toHaveText(CONTEXT_LOST)
  await expect(view3d(page)).toHaveCount(0)
  await expect(scatter2d(page)).toHaveAttribute('data-chart-status', 'ready')
})
