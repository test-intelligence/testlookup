/**
 * /coverage (Wave 3, VIZ-206 / VIZ-502, FK2): the test coverage map (a
 * treemap, one level per `/analytics/coverage-map` request, drilled through
 * the page URL `drill=suite~<key>&drill=class~<key>`) and the suite x
 * environment / release heatmap, mounted by the page's composite
 * (`CoverageAdvanced`). Since Phase D, S4 nothing on the page asks a flag
 * (migration 0195 turned the chart flags on everywhere), so the inventory
 * starts at `SHELL_BASE` and there is no flag matrix.
 *
 * The keyboard is the map's spine (EPIC VIZ-502): Tab to the treemap, arrows
 * walk its nodes in drawn order, Enter drills (or, on a test, opens the
 * test's executions in the page's one rows panel), Backspace goes up a
 * level, Escape only clears the highlight. Captions are asserted verbatim:
 * "Test execution coverage — not code coverage" (with the EM DASH, the
 * EPIC's words). Fixtures: the server's wire shape (`fixtures.ts`).
 *
 * UX redesign P3: the map and the heatmap are the page's two tabs (`?tab=`:
 * the map is the default, `heatmap` the other); each tab mounts only its own
 * section, so a load asks only its tab's read, and opening the other tab
 * asks the other's.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, respond, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectHostileAsText,
  expectInventory,
  expectNoErrorFrame,
  expectNoPrototypePollution,
  expectOneRowsPanel,
  networkQuiet,
  openRollout,
  proveLazyMount,
  queryOf,
  requestsTo,
  rowsPanels,
  RUN_PROBE,
  section,
  sectionFrame,
  SHELL_BASE,
  SHORT_VIEWPORT,
  watchConsoleErrors,
} from '../lib/rollout'
import { expectNoBlockingViolations } from '../lib/axe-gate'
import {
  CHART_ROWS_PATH,
  COVERAGE_MAP_PATH,
  COVERAGE_ON,
  HEATMAP_PATH,
  PROJECT_ID,
  rowsTotal,
  suiteKey,
  withoutObjectMemberLabels,
} from '../visual/production/fixtures'

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Run cadence')
const MAP = { id: 'coverage-map', title: 'Test coverage map' } as const
const ENV = { id: 'heatmap-suite_environment', title: 'Suite pass rate by environment' } as const
const CAPTION = 'Test execution coverage — not code coverage'

/** The page's own reads (recorded flag-off in Wave 3 C0, `rollout-flag-off.spec.ts` until S4). */
const PAGE_READS = [
  `GET /api/v1/saved-views?project_id=${P}&page=coverage`,
  `GET /api/v1/analytics/coverage?project_id=${P}&days=30`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=30`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=1&days=30`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
]
const MAP_LINE = `GET ${COVERAGE_MAP_PATH}?depth=1&project_id=${P}&days=30`
const ENV_LINE = `GET ${HEATMAP_PATH}?kind=suite_environment&project_id=${P}&days=30`

/**
 * A load on each tab, its section near: the shell, the page, then level 1 of
 * the map (the default tab) or the heatmap, and the probe. No flag lookup.
 * Before P3 one load asked both (one stack); each tab's share is now its own.
 */
const MAP_TAB_INVENTORY = [...SHELL_BASE, ...PAGE_READS, MAP_LINE, RUN_PROBE]
const HEATMAP_TAB_INVENTORY = [...SHELL_BASE, ...PAGE_READS, ENV_LINE, RUN_PROBE]

const open = (page: Page, path = '/coverage', handlers: ApiHandlers = COVERAGE_ON) => openRollout(page, path, { handlers, ready })
/** A page tab (UX redesign P3). */
const pageTab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Coverage views' }).getByRole('tab', { name, exact: true })

const treemap = (page: Page) => section(page, MAP.id).locator('[data-chart-keyboard="treemap"]')
const focusedLabel = (page: Page) => section(page, MAP.id).locator('[data-coverage-focused]')

test.describe('Coverage (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the map draws (the default tab); the caption is the EPIC\'s; exactly its read, once; the heatmap is not asked', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page)
    const map = sectionFrame(page, MAP.id, MAP.title)
    await expectDrawn(map, MAP.id)
    await expect(map).toContainText(CAPTION)
    await expect(map.locator('canvas').first()).toBeVisible()
    await expect(section(page, MAP.id).locator('[data-coverage-breadcrumb]')).toContainText('All suites')
    // The gaps have words, not only a colour: never run, last run unknown, not run in the window.
    for (const gap of ['Never run', 'Last run unknown', 'Not run in this window']) await expect(map).toContainText(gap)
    // The heatmap is the other tab's: not rendered at all (no section, no placeholder).
    await expect(section(page, ENV.id)).toHaveCount(0)
    await expect(page.locator(`[data-lazy-section="${ENV.id}"]`)).toHaveCount(0)
    await expectHostileAsText(page, page.locator('[data-coverage-advanced]'), 'Coverage map tab')
    await expectNoErrorFrame(page)
    await networkQuiet(page, api)
    expectInventory(api, errors, MAP_TAB_INVENTORY, 'Coverage, map tab')
    expect(console).toEqual([])
  })

  test('?tab=heatmap: the heatmap draws; exactly its read, once; the map is not asked', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page, '/coverage?tab=heatmap')
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await expect(section(page, MAP.id)).toHaveCount(0)
    await expect(page.locator(`[data-lazy-section="${MAP.id}"]`)).toHaveCount(0)
    await expectHostileAsText(page, page.locator('[data-coverage-advanced]'), 'Coverage heatmap tab')
    await expectNoErrorFrame(page)
    await networkQuiet(page, api)
    expectInventory(api, errors, HEATMAP_TAB_INVENTORY, 'Coverage, heatmap tab')
    expect(console).toEqual([])
  })

  test('opening the heatmap tab makes its read (and writes ?tab=); the map tab clears it', async ({ page }) => {
    const { api, errors } = await open(page)
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), MAP.id)
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap asked on the map tab').toEqual([])
    await pageTab(page, 'Env × release heatmap').click()
    await expect(page).toHaveURL(/[?&]tab=heatmap(&|$)/)
    await expect.poll(() => requestsTo(api, HEATMAP_PATH), { message: 'the heatmap read once its tab opens' }).toEqual([ENV_LINE])
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await expect(section(page, MAP.id), 'the map leaves with its tab').toHaveCount(0)
    await pageTab(page, 'Coverage map').click()
    await expect(page).not.toHaveURL(/[?&]tab=/)
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), MAP.id)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('keyboard: arrows walk the suites, Enter drills (URL + one depth-2 read), Backspace goes up, Escape only clears', async ({
    page,
  }) => {
    const { api, errors } = await open(page)
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), MAP.id)
    const group = treemap(page)
    await group.focus()
    await page.keyboard.press('ArrowRight')
    await expect(focusedLabel(page)).not.toHaveText('')
    const label = (await focusedLabel(page).textContent())?.trim() ?? ''
    const key = suiteKey(label)
    // Escape clears the highlight and nothing else: no URL change, focus stays.
    await page.keyboard.press('Escape')
    await expect(page).not.toHaveURL(/[?&]drill=/)
    await expect(group).toBeFocused()
    await page.keyboard.press('ArrowRight')
    await expect(focusedLabel(page)).toHaveText(label)
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(new RegExp(`[?&]drill=suite(%7E|~)${encodeURIComponent(key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`))
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), 'level 2')
    await expect(section(page, MAP.id).locator('[data-coverage-breadcrumb] li')).toHaveText(['All suites', label])
    await expect(treemap(page), 'focus follows the drill to the new level').toBeFocused()
    const depth2 = queryOf(api, COVERAGE_MAP_PATH, (q) => q.get('depth') === '2')
    expect(depth2, 'one depth-2 read').toHaveLength(1)
    expect(depth2[0].get('suite'), 'the suite KEY (the node id without its s: prefix)').toBe(key)
    await page.keyboard.press('Backspace')
    await expect(page).not.toHaveURL(/[?&]drill=/)
    await expect(section(page, MAP.id).locator('[data-coverage-breadcrumb] li')).toHaveText(['All suites'])
    await expect(treemap(page)).toBeFocused()
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a shared level-3 link: the class\'s tests; Enter on a test opens ONE rows panel (its executions, the drilled suite)', async ({
    page,
  }) => {
    const { api, errors } = await open(page, '/coverage?drill=suite~auth&drill=class~AuthSpec')
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), 'level 3')
    expect(queryOf(api, COVERAGE_MAP_PATH).map((q) => [q.get('depth'), q.get('suite'), q.get('class_key')])).toEqual([
      ['3', 'auth', 'AuthSpec'],
    ])
    await expect(section(page, MAP.id).locator('[data-coverage-breadcrumb] li')).toHaveText(['All suites', /auth/i, 'AuthSpec'])
    await treemap(page).focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    const panel = await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric')).toBe('executions')
    expect(q.getAll('group_by')).toEqual(['test'])
    expect(q.get('bucket_test')).toMatch(/^fp-auth-\d$/)
    expect(q.getAll('suite_name'), 'group_by=test needs one suite: the drilled one').toEqual(['auth'])
    const total = rowsTotal('executions', { test: q.get('bucket_test') ?? '' }, 30)
    await expect(panel.locator('[data-rows-count]')).toContainText(`${total} execution`)
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(treemap(page)).toBeFocused()
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  // R1B-4 (X2): the "View rows" BUTTON (not Enter on the map) opens the panel; the button is gone once focus
  // leaves the map, so Escape from the panel must bring focus back to the map, never drop it on <body>.
  test('the readout\'s View rows button, then Escape from the panel: focus is back on the map', async ({ page }) => {
    const { api, errors } = await open(page, '/coverage?drill=suite~auth&drill=class~AuthSpec')
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), 'level 3')
    await treemap(page).focus()
    await page.keyboard.press('ArrowRight')
    const button = section(page, MAP.id).locator('[data-mark-intent="rows"]')
    await expect(button).toBeVisible()
    await page.keyboard.press('Tab')
    await expect(button).toBeFocused()
    await page.keyboard.press('Enter')
    await expectOneRowsPanel(page, /^Executions in /)
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(treemap(page)).toBeFocused()
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('hostile names (markup, constructor, __proto__) as suite, class and test labels: text, and keys that stay data', async ({
    page,
  }) => {
    await open(page, '/coverage?drill=suite~__proto__')
    const map = sectionFrame(page, MAP.id, MAP.title)
    await expectDrawn(map, 'the suite named __proto__')
    await expect(section(page, MAP.id).locator('[data-coverage-breadcrumb] li')).toHaveText(['All suites', '__proto__'])
    await map.getByRole('button', { name: 'View as table' }).click()
    await expect(map.getByRole('table')).toContainText('constructor')
    await expectHostileAsText(page, section(page, MAP.id), 'map at the __proto__ suite')
    await expectNoPrototypePollution(page, 'Coverage with Object-member suite, class and test names')
  })

  test('colour by staleness and flaky share: the legend says what the colour means', async ({ page }) => {
    await open(page)
    const map = sectionFrame(page, MAP.id, MAP.title)
    await expectDrawn(map, MAP.id)
    const colourBy = section(page, MAP.id).getByRole('combobox', { name: /Colour by/ })
    const legend = section(page, MAP.id).locator('[data-coverage-legend]')
    const before = await legend.innerText()
    await colourBy.selectOption('staleness')
    await expect(legend).not.toHaveText(before)
    await colourBy.selectOption('flaky_share')
    await expect(legend).not.toHaveText(before)
  })

  test('a 500 on the map is the map\'s own error (no toast); the heatmap (its own tab) draws; Retry recovers', async ({ page }) => {
    let fail = true
    const fixture = COVERAGE_ON.find(([m]) => m === COVERAGE_MAP_PATH)?.[1]
    const handlers: ApiHandlers = [
      [COVERAGE_MAP_PATH, (request: ApiRequest) => (fail ? respond(500, { detail: 'planted map failure' }) : fixture?.(request))],
      ...COVERAGE_ON,
    ]
    const { api, errors } = await open(page, '/coverage', handlers)
    const map = sectionFrame(page, MAP.id, MAP.title)
    await section(page, MAP.id).scrollIntoViewIfNeeded()
    await expect(map).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    await expect(page.getByText('planted map failure')).toHaveCount(0)
    // The heatmap (the other tab since P3) still draws: the map's error is the map's alone.
    await pageTab(page, 'Env × release heatmap').click()
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await pageTab(page, 'Coverage map').click()
    await section(page, MAP.id).scrollIntoViewIfNeeded()
    await expect(map).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    fail = false
    await map.getByRole('button', { name: /retry/i }).click()
    await expectDrawn(map, 'after Retry')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a stored 365-day window: every Wave 3 read asks for at most 90 days', async ({ page }) => {
    const { api } = await openRollout(page, '/coverage', { handlers: COVERAGE_ON, ready, days: 365 })
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), MAP.id)
    // Both tabs' reads (P3: one tab each).
    await pageTab(page, 'Env × release heatmap').click()
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await networkQuiet(page, api)
    expect(requestsTo(api, COVERAGE_MAP_PATH).length).toBeGreaterThan(0)
    expect(requestsTo(api, HEATMAP_PATH).length).toBeGreaterThan(0)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the map, every impact (${theme}): idle, a node focused, the table open`, async ({ page }) => {
      // Without the Object-member names: a polluted page breaks axe itself (the hostile test above owns that defect).
      await openRollout(page, '/coverage', { handlers: withoutObjectMemberLabels(COVERAGE_ON), ready, theme })
      const map = sectionFrame(page, MAP.id, MAP.title)
      await section(page, MAP.id).scrollIntoViewIfNeeded()
      await expectDrawn(map, MAP.id)
      const only = [`[data-catalogue-section="${MAP.id}"]`]
      await expectNoBlockingViolations(page, theme, [], only)
      await treemap(page).focus()
      await page.keyboard.press('ArrowRight')
      await expect(section(page, MAP.id).locator('[data-mark-intent]').first()).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], only)
      await map.getByRole('button', { name: 'View as table' }).click()
      await expectNoBlockingViolations(page, theme, [], only)
    })
  }
})

// UX redesign P3 moved the map up (the verdict card above it is gone, the map
// tab starts right under the suite table): at the 600 px short screen its
// placeholder starts 246 px below the fold, inside the 200 px margin plus the
// proof's 50 px, so it is near at load and the proof would mean nothing.
// 500 px puts it ~350 px below the fold: the same proof, with room.
const SHORTER_VIEWPORT = { width: SHORT_VIEWPORT.width, height: 500 } as const

test.describe('Coverage, a short screen (1280 x 500)', () => {
  test.use({ viewport: { ...SHORTER_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no coverage-map read until the map is near, and it goes out before the map is visible', async ({ page }) => {
    const { api, errors } = await open(page)
    await proveLazyMount(page, api, {
      label: MAP.id,
      section: MAP.id,
      asked: () => requestsTo(api, COVERAGE_MAP_PATH).length,
      before: 0,
    })
    await expectDrawn(sectionFrame(page, MAP.id, MAP.title), MAP.id)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})
