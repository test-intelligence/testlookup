/**
 * W23d AGT-14 — the `HTTP exchanges` tab of the FULL test page (`docs/deferred-scope-2026-10-13/work/W23d-contract.md`
 * §2.1 P-7, §4.1, §4.2, §6.3), hermetic, as a VIEWER and a QA engineer, at 1280 × 900 and 1440 × 900:
 *
 *  - `/runs/<run>/tests/<test>` makes NO exchanges request on load; opening `HTTP exchanges` makes ONE
 *    `GET /api/v1/runs/<run>/tests/<test>/http-exchanges`;
 *  - the first row's summary reads `GET /api/products/{id} 200`; opened: the `Authorization` value is a `[REDACTED]`
 *    badge, the JSON body is pretty-printed, a cut body says so; a `<script>` body is text (no element);
 *  - `total: 0` → the "none" and "how" sentences; a 503 → `Could not load the HTTP exchanges.` (never "none");
 *  - the Run page's side panel (`?tab=tests`, a row click) has NO `HTTP exchanges` tab and asks nothing for it;
 *  - the tab strip stays one line and nothing scrolls sideways with a row open (the slack is printed).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-payload-metrics.ts` (G-A).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { expectNoHorizontalOverflow, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { runAnalysisWorld } from '../visual/production/fixtures-in-page'
import { TEST_CASE_API, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'
import { HTTP_EXCHANGES_API, IMG_HEADER_VALUE, SCRIPT_BODY, testCaseHttpWorld } from '../visual/production/fixtures-payload-metrics'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

// The tab's one read, written out in full (the world answers the same whole path).
const EXCHANGES = `${TEST_CASE_API}/http-exchanges`
const NONE = 'No HTTP exchanges were captured for this test.'
const HOW = 'Capture is opt-in per run: pytest --testlookup-capture-http (or TESTLOOKUP_CAPTURE_HTTP=1).'
const TRUNCATED = 'Body cut to 16 KB when stored.'
const LOAD_ERROR = 'Could not load the HTTP exchanges.'

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_CASE_NAME })
const tabs = (p: Page) => p.getByRole('tablist', { name: 'Test case sections' })
const tab = (p: Page, name: string) => tabs(p).getByRole('tab', { name: new RegExp(`^${name}`) })
const panel = (p: Page) => p.locator('[data-test-case-tab="http"]')
const rows = (p: Page): Locator => panel(p).locator('[data-http-exchange]')

type World = ReturnType<typeof testCaseHttpWorld>

async function open(page: Page, world: World, user: typeof VIEWER, path = TEST_CASE_PATH) {
  watchNativeDialogs(page)
  return openRollout(page, path, { handlers: world.handlers, ready, user })
}

for (const [role, user] of [['VIEWER', VIEWER], ['QA engineer', QA_ENGINEER]] as const) {
  for (const width of [1280, 1440]) {
    test(`${role} @${width}: nothing on load; the tab asks once and shows the redacted exchanges as text`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const { api, errors } = await open(page, testCaseHttpWorld({ role: user }), user)
      await networkQuiet(page, api)
      expect(requestsTo(api, EXCHANGES), 'no exchanges read on load').toEqual([])

      await tab(page, 'HTTP exchanges').click()
      await expect(page).toHaveURL(/[?&]tab=http(&|$)/)
      await expect(rows(page).first()).toBeVisible()
      const summary = rows(page).first().locator('[data-http-exchange-summary]')
      await expect(summary).toContainText('GET')
      await expect(summary).toContainText('/api/products/{id}')
      await expect(summary).toContainText('200')

      // Open every row: the redacted header badge, a pretty JSON body, the cut-body note, the <script> body as text.
      const count = await rows(page).count()
      for (let i = 0; i < count; i += 1) await rows(page).nth(i).getByRole('button', { expanded: false }).click()
      await expect(panel(page).locator('[data-http-side="request"] [data-redacted]').first()).toHaveText('[REDACTED]')
      const auth = panel(page).locator('[data-http-headers] dt', { hasText: /^Authorization$/ }).first()
      await expect(auth).toBeVisible()
      const bodies = await panel(page).locator('[data-http-body]').allTextContents()
      expect(bodies.some((b) => b.includes('\n  "')), 'a JSON body is pretty-printed').toBe(true)
      await expect(panel(page).getByText(TRUNCATED, { exact: true }).first()).toBeVisible()
      await expect(panel(page).getByText(SCRIPT_BODY, { exact: true })).toBeVisible()
      await expect(panel(page).getByText(IMG_HEADER_VALUE, { exact: true })).toBeVisible()
      expect(await panel(page).locator('script, img').count(), 'captured content creates no element').toBe(0)
      expect(EXCHANGES).toBe(HTTP_EXCHANGES_API)

      await expectNoOverlay(page)
      await expectNoHorizontalOverflow(page, `http exchanges open @${width}`)
      const strip = await tabs(page).getByRole('tab').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON() as DOMRect))
      expect(new Set(strip.map((b) => Math.round(b.top))).size, 'the tab strip is one line').toBe(1)
      const box = await tabs(page).boundingBox()
      const slack = Math.round((box ? box.x + box.width : 0) - Math.max(...strip.map((b) => b.right)))
      console.log(`HTTP-EXCHANGES tabs @${width} ${JSON.stringify({ tabs: strip.length, slack })}`)
      await networkQuiet(page, api)
      expect(requestsTo(api, EXCHANGES), 'one exchanges read').toHaveLength(1)
      expect(api.unhandled).toEqual([])
      expect(errors).toEqual([])
    })
  }
}

test('total 0: the "none" and "how" sentences, never a blank panel', async ({ page }) => {
  const { api, errors } = await open(page, testCaseHttpWorld({ role: VIEWER, answer: 'empty' }), VIEWER, `${TEST_CASE_PATH}?tab=http`)
  await expect(panel(page).getByText(NONE, { exact: true })).toBeVisible()
  await expect(panel(page).getByText(HOW, { exact: true })).toBeVisible()
  await expect(rows(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, EXCHANGES)).toHaveLength(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a 503: "Could not load the HTTP exchanges." and Retry, never the "none captured" sentence', async ({ page }) => {
  const { api, errors } = await open(page, testCaseHttpWorld({ role: QA_ENGINEER, answer: 'unavailable' }), QA_ENGINEER, `${TEST_CASE_PATH}?tab=http`)
  await expect(panel(page).getByText(LOAD_ERROR, { exact: true })).toBeVisible()
  await expect(panel(page).getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(panel(page).getByText(NONE, { exact: true })).toHaveCount(0)
  await expect(page.getByText(/status code/)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, EXCHANGES).length).toBeGreaterThanOrEqual(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the Run page\'s side panel keeps its tab strip: no HTTP exchanges tab, no exchanges read', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, { handlers: world.handlers, ready: (p) => p.locator('[data-primary] table') })
  const first = world.tests.find((t) => t.status === 'FAILED' || t.status === 'BROKEN')
  await page.locator(`[data-test-row="${first?.id}"]`).click()
  const pane = opened(page, String(first?.test_name))
  await expect(pane.locator('[data-test-case-body]')).toHaveAttribute('data-compact', 'true')
  const names = await pane.getByRole('tablist', { name: 'Test case sections' }).getByRole('tab').allTextContents()
  expect(names.map((n) => n.trim().replace(/\s*\d+$/, ''))).toEqual(['History', 'Steps', 'Details'])
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/http-exchanges$/)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
