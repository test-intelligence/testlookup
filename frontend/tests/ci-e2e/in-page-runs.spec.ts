/**
 * W27a PAN-03 IN-PAGE SHAPE (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.3, §4.1), hermetic, on the
 * CONVERTED `/runs` and `/runs/:runId`. It fails on the modal tree by design (`expectNoOverlay`); the flows
 * themselves are `characterize-runs.spec.ts` / `characterize-run-analysis.spec.ts`, which run unedited on both.
 *
 *  - `/runs`: Upload report opens under the page header (above the status banner and the table); Failure signatures
 *    opens between the status banner and the table, and the runs table keeps its width (`OVERFLOW /runs` with it
 *    open); no overlay;
 *  - `/runs/:id?tab=tests` at 1280 and 1440: a row → `?test=<id>` (pushed), the test in a pane BESIDE the table
 *    (≥ 440 px), nothing scrolling sideways; a refresh reopens it, Back closes it; `?test=not-a-uuid` → `Test case
 *    not found` with no request for it; `INPAGE /runs/:id`;
 *  - Timing: a bar → the same pane, `?test=`, Back closes;
 *  - Analysis: File defect's form is inside its failure block; the decision trail is a section between the verdict
 *    banner and "What failed".
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `runsUploadWorld()`, `runAnalysisWorld()`
 * (`fixtures-in-page.ts`), `withTiming(TIMED)` (`fixtures-timing.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack, precedes, tableBox } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, observed, openRollout, primaryOverflow } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { runAnalysisWorld, runsUploadWorld } from '../visual/production/fixtures-in-page'
import { TEST_CASE_ID, TEST_CASE_NAME } from '../visual/production/fixtures-pages'
import { TIMED, withTiming } from '../visual/production/fixtures-timing'

const runsReady = (p: Page) => p.locator('[data-status-banner]')
const testsReady = (p: Page) => p.locator('[data-primary] table')
const pane = (page: Page) => page.locator('aside[data-in-page="detail"]')

/** The whole URL is `<origin><pathAndQuery>`. */
async function expectUrl(page: Page, pathAndQuery: string) {
  const origin = new URL(page.url()).origin
  await expect(page).toHaveURL(`${origin}${pathAndQuery}`)
}

/** The first failing row of the Tests table, in the order the table shows it. */
async function firstFailing(page: Page, world: ReturnType<typeof runAnalysisWorld>) {
  const ids = await page.locator('[data-test-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-test-row') ?? ''))
  const row = ids.map((id) => world.tests.find((t) => t.id === id)).find((t) => t && (t.status === 'FAILED' || t.status === 'BROKEN'))
  expect(row, 'a failing row').toBeTruthy()
  return { id: String(row?.id), name: String(row?.test_name) }
}

test.describe('/runs at 1280 x 900', () => {
  test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('Upload report opens under the page header, above the banner and the table; no overlay', async ({ page }) => {
    watchNativeDialogs(page)
    const world = runsUploadWorld()
    const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready: runsReady, flags: { manual_upload: true } })
    await page.locator('[data-page-header]').getByRole('button', { name: 'Upload report' }).click()
    const upload = page.getByRole('region', { name: 'Upload test report' })
    await expect(upload).toBeVisible()
    await expectNoOverlay(page)
    expect(await precedes(page.locator('[data-page-header]'), upload), 'under the page header').toBe(true)
    expect(await precedes(upload, page.locator('[data-status-banner]')), 'above the status banner').toBe(true)
    expect(await precedes(upload, page.locator('[data-primary]')), 'above the runs table').toBe(true)
    await expectMainFits(page, '/runs @1280 with the upload open')
    inPageLine('/runs upload', 1280, { form: (await boxOf(upload)).width, slack: await minContentSlack(upload) })
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('Failure signatures opens between the banner and the table, which keeps its width', async ({ page }) => {
    watchNativeDialogs(page)
    const world = runsUploadWorld()
    const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready: runsReady })
    await networkQuiet(page, api)
    const table = page.locator('[data-primary] table').first()
    const closed = await tableBox(table)
    const closedPrimary = await boxOf(page.locator('[data-primary]'))

    await page.locator('[data-page-header]').getByRole('button', { name: /^Failure signatures/ }).click()
    const signatures = page.getByRole('complementary', { name: 'Failure signatures' })
    await expect(signatures).toBeVisible()
    await expectNoOverlay(page)
    expect(await precedes(page.locator('[data-status-banner]'), signatures), 'under the status banner').toBe(true)
    expect(await precedes(signatures, page.locator('[data-primary]')), 'above the runs table').toBe(true)
    const open = await tableBox(table)
    const openPrimary = await boxOf(page.locator('[data-primary]'))
    expect(Math.abs(open.table - closed.table), 'the runs table keeps its width').toBeLessThanOrEqual(1)
    expect(Math.abs(openPrimary.width - closedPrimary.width), 'the table card keeps its width').toBeLessThanOrEqual(1)
    const overflow = await primaryOverflow(page)
    console.log(`OVERFLOW /runs @1280 ${JSON.stringify({ ...overflow, signaturesOpen: true })}`)
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth)
    await expectMainFits(page, '/runs @1280 with the signatures open')
    inPageLine('/runs signatures', 1280, { table: open.table, box: open.box, section: (await boxOf(signatures)).width, slack: await minContentSlack(table) })

    await signatures.getByRole('button', { name: 'Close panel' }).click()
    await expect(signatures).toHaveCount(0)
    await page.locator('[data-signature-chip]').first().click()
    await expect(signatures).toBeVisible()
    await expect(signatures).toBeInViewport()
    assertHermetic(api, errors)
  })
})

for (const width of [1280, 1440]) {
  test.describe(`/runs/:id at ${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`Tests @${width}: a row opens the test beside the table (?test= pushed); refresh reopens, Back closes`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = runAnalysisWorld()
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, { handlers: world.handlers, ready: testsReady })
      const first = await firstFailing(page, world)
      await page.locator(`[data-test-row="${first.id}"]`).click()
      await expectUrl(page, `/runs/${RUN_ID}?tab=tests&test=${first.id}`)
      const opened = page.getByRole('complementary', { name: first.name, exact: true })
      await expect(opened).toBeVisible()
      await expect(opened.locator('[data-test-case-body]')).toBeVisible()
      await expectNoOverlay(page)

      const list = page.locator('section[data-primary][aria-label="Tests"]')
      const [paneBox, listBox] = [await boxOf(opened), await boxOf(list)]
      expect(paneBox.left, 'the pane starts at or right of the list').toBeGreaterThanOrEqual(listBox.right)
      expect(paneBox.width, 'the pane is at least 440 px').toBeGreaterThanOrEqual(440)
      await expectMainFits(page, `/runs/:id tests @${width}`)
      const table = await tableBox(list.locator('table').first())
      inPageLine('/runs/:id', width, { pane: paneBox.width, list: listBox.width, table: table.table, box: table.box, scrollsInsideItsCard: table.table > table.box })

      await page.reload()
      await expect(page.getByRole('complementary', { name: first.name, exact: true }), 'a refresh reopens it').toBeVisible()
      await page.goBack()
      await expectUrl(page, `/runs/${RUN_ID}?tab=tests`)
      await expect(pane(page)).toHaveCount(0)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  })
}

test.describe('/runs/:id at 1440 x 900', () => {
  test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('?test=not-a-uuid: the pane says "Test case not found" and nothing is read for it', async ({ page }) => {
    watchNativeDialogs(page)
    const world = runAnalysisWorld()
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests&test=not-a-uuid`, { handlers: world.handlers, ready: testsReady })
    await expect(pane(page)).toBeVisible()
    await expect(pane(page)).toContainText('Test case not found')
    await expect(page.getByRole('complementary', { name: 'Test', exact: true }), 'titled "Test" while unknown').toBeVisible()
    await networkQuiet(page, api)
    expect(observed(api).filter((line) => line.includes('not-a-uuid')), 'a malformed id never reaches a request').toEqual([])
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })

  test('Timing: a bar opens its test in the same pane (?test=), and Back closes it', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=timing`, {
      handlers: withTiming(TIMED),
      ready: (p) => p.locator('[data-chart="execution-timeline"]'),
    })
    await page.locator('[data-timeline-bar][data-status="failed"]').click()
    await expectUrl(page, `/runs/${RUN_ID}?tab=timing&test=${TEST_CASE_ID}`)
    const opened = page.getByRole('complementary', { name: TEST_CASE_NAME, exact: true })
    await expect(opened).toBeVisible()
    await expectNoOverlay(page)
    await expectMainFits(page, '/runs/:id timing @1440')
    inPageLine('/runs/:id timing', 1440, { pane: (await boxOf(opened)).width })
    await page.goBack()
    await expectUrl(page, `/runs/${RUN_ID}?tab=timing`)
    await expect(pane(page)).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('Analysis: File defect opens inside its failure block; the trail opens between the banner and "What failed"', async ({ page }) => {
    watchNativeDialogs(page)
    const world = runAnalysisWorld({ approval: null })
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, {
      handlers: world.handlers,
      ready: (p) => p.locator('section[aria-label="What failed"]'),
    })
    const whatFailed = page.locator('section[aria-label="What failed"]')
    await whatFailed.getByRole('button', { name: 'File defect' }).click()
    const form = page.getByRole('region', { name: 'Promote to Defect' })
    await expect(form).toBeVisible()
    await expectNoOverlay(page)
    const block = whatFailed
      .locator('div')
      .filter({ has: page.getByRole('button', { name: 'File defect' }) })
      .filter({ has: form })
      .last()
    await expect(block, 'the form is inside the failure block it was filed from').toContainText('Gateway timeouts')
    await expectMainFits(page, '/runs/:id analysis @1440 with File defect open')
    inPageLine('/runs/:id promote', 1440, { form: (await boxOf(form)).width, slack: await minContentSlack(form) })
    await form.getByRole('button', { name: 'Cancel' }).click()
    await expect(form).toHaveCount(0)

    await page.locator('section[aria-label="Release verdict"]').getByRole('button', { name: 'Decision trail' }).click()
    const trail = page.getByRole('region', { name: 'AI decision trail' })
    await expect(trail).toBeVisible()
    await expectNoOverlay(page)
    expect(await precedes(page.locator('section[aria-label="Release verdict"]'), trail), 'under the verdict banner').toBe(true)
    expect(await precedes(trail, whatFailed), 'above "What failed"').toBe(true)
    await expect(trail.getByRole('searchbox', { name: 'Search decision trail' })).toBeVisible()
    await expectMainFits(page, '/runs/:id analysis @1440 with the trail open')
    await networkQuiet(page, api)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })
})
