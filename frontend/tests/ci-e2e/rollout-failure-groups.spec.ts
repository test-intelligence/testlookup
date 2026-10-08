/**
 * /failures (Wave 3, VIZ-207 / VIZ-504, FK3), no viz flag asked since Phase
 * D, S5: failure groups
 * (one `/analytics/failure-groups?include=edges` read feeds the bubbles, the
 * related-groups view and the ranked table) and the systemic flake clusters
 * tab (`/analytics/systemic-clusters`, asked only when the tab opens, with no
 * `days`). UX redesign P3: the groups are the page's default tab ("Groups");
 * FK5's drill ladder and FK4's project scatter are the "By suite" and
 * "Scatter" tabs, each mounted (and read) only while its tab is open, so the
 * page's `rows` key has one host at a time ("one rows panel" below).
 *
 * Words asserted verbatim (plan 9.3 R2): "Linked groups fail in the same
 * tests. Position has no other meaning." and "Tests that fail together (last
 * 60 days, whole project)"; no "AI" anywhere in the section. Group labels
 * are raw error lines (attacker-influenced): text only, cut at 160.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, respond, type ApiHandlers } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectHostileAsText,
  expectInventory,
  expectNoErrorFrame,
  expectNoPrototypePollution,
  expectOneRowsPanel,
  mountEverySection,
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
  CHART_DATA_PATH,
  CHART_ROWS_PATH,
  CLUSTERS_EMPTY_IS_NORMAL,
  FAILURE_GROUP_SPECS,
  FAILURE_GROUPS_PATH,
  FAILURES_ON,
  groupFailures,
  HOSTILE_NAME,
  PROJECT_ID,
  RUN_ID,
  SYSTEMIC_CLUSTERS,
  SYSTEMIC_CLUSTERS_PATH,
  systemicClustersBody,
  TEST_SCATTER_PATH,
} from '../visual/production/fixtures'

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Failure verdict')
const GROUPS = { id: 'failures-groups', title: 'Failures grouped by error message' } as const
const RELATIONS_CAPTION = 'Linked groups fail in the same tests. Position has no other meaning.'
const CLUSTERS_TITLE = 'Tests that fail together (last 60 days, whole project)'

/**
 * The page's own reads, recorded with every flag off on main @ 03983f12
 * (Wave 3 C0) by `rollout-flag-off.spec.ts`, which S5 deleted when Failures,
 * its last page, stopped asking a flag: this list is now that inventory's home.
 * UX redesign P3: the suspects read left the load — the Suspects ranking is a
 * row's side panel, asked when the panel opens (`SUSPECTS_READ`, tested below).
 */
const FAILURES_PAGE_READS = [
  `GET /api/v1/saved-views?project_id=${P}&page=failures`,
  `GET /api/v1/analytics/failure-categories?project_id=${P}&days=30`,
  `GET /api/v1/analytics/flaky-tests?project_id=${P}&days=30`,
  `GET /api/v1/analytics/top-failing?project_id=${P}&days=30`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=30`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=1&days=30&status=FAILED`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
  `GET /api/v1/projects/${P}/defects/jira/metadata`,
  // Mute proposes a quarantine, which every quarantine endpoint refuses while
  // this flag is off: the row's Mute says so instead (browser E2E pass).
  `GET /api/v1/feature-flags/flaky_auto_quarantine/status?project_id=${P}`,
]

/** The suspect commits for the top row's test, against the latest failing run: asked by the Suspects panel only. */
const SUSPECTS_READ = `GET /api/v1/runs/${RUN_ID}/suspects?fingerprint=fp-top-0`

const GROUPS_READ = `GET ${FAILURE_GROUPS_PATH}?include=edges&project_id=${P}&days=30`
const LADDER_READ = `GET ${CHART_DATA_PATH}?metric=executions&group_by=suite&group_by=status&project_id=${P}&days=30`
const SCATTER_READ = `GET ${TEST_SCATTER_PATH}?min_executions=5&order=failures&project_id=${P}&days=30`

/**
 * The default tab (Groups) near: the shell with no viz flag lookup
 * (`SHELL_BASE`; S5 removed the catalogue, advanced and 3D lookups), the
 * page's reads, groups (with edges), the probe. UX redesign P3: the ladder
 * and the project scatter are tabs of their own, read when their tab opens
 * (the first test proves each).
 */
const INVENTORY_GROUPS_TAB = [...SHELL_BASE, ...FAILURES_PAGE_READS, GROUPS_READ, RUN_PROBE]

const open = (page: Page, handlers: ApiHandlers = FAILURES_ON) => openRollout(page, '/failures', { handlers, ready })

/** The page's section tabs (Groups · By suite · Scatter · Categories). */
const sectionTab = (page: Page, name: string) =>
  page.getByRole('tablist', { name: 'Failure analysis sections' }).getByRole('tab', { name, exact: true })

const groupsFrame = (page: Page) => sectionFrame(page, GROUPS.id, GROUPS.title)
const plot = (page: Page) => section(page, GROUPS.id).locator('[data-group-plot]')

test.describe('Failure groups (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('groups drawn from ONE read with edges; roll-ups stated; each section\'s read once, when its tab opens', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page)
    await mountEverySection(page, api)
    const frame = groupsFrame(page)
    await expectDrawn(frame, GROUPS.id)
    await expect(plot(page)).toHaveAttribute('data-group-plot', 'bubbles')
    await expect(section(page, GROUPS.id).locator('[data-group-plot] [data-group-id]')).toHaveCount(FAILURE_GROUP_SPECS.length)
    // The roll-ups are words under the plot, never circles.
    await expect(frame.locator('[data-group-rollup]').first()).toBeVisible()
    await expect(section(page, GROUPS.id).locator('[data-group-id="__NO_MESSAGE__"], [data-group-id="__SINGLETONS__"]')).toHaveCount(0)
    await expect(section(page, GROUPS.id)).not.toContainText(/\bAI\b/)
    await expectHostileAsText(page, section(page, GROUPS.id), 'failure groups')
    await expectNoErrorFrame(page)
    await networkQuiet(page, api)
    expectInventory(api, errors, INVENTORY_GROUPS_TAB, 'Failures, the Groups tab (the default)')
    expect(requestsTo(api, SYSTEMIC_CLUSTERS_PATH), 'clusters only when their tab opens').toEqual([])
    // P3: the ladder and the scatter are tabs; opening each makes its one read, and nothing else.
    await sectionTab(page, 'By suite').click()
    await mountEverySection(page, api)
    await expectDrawn(sectionFrame(page, 'failures-drill', 'Results by suite'), 'ladder')
    await sectionTab(page, 'Scatter').click()
    await mountEverySection(page, api)
    await expectDrawn(sectionFrame(page, 'scatter-project', 'Test duration vs failure rate'), 'scatter')
    await networkQuiet(page, api)
    expectInventory(api, errors, [...INVENTORY_GROUPS_TAB, LADDER_READ, SCATTER_READ], 'Failures, after opening By suite and Scatter')
    expect(console).toEqual([])
  })

  test('P3: the suspects read goes out only when a row\'s Suspects opens its side panel', async ({ page }) => {
    const { api, errors } = await open(page)
    await networkQuiet(page, api)
    expect(api.seen.filter((line) => line === SUSPECTS_READ), 'suspects at load').toEqual([])
    await page.getByRole('button', { name: 'Suspects for card declined shows reason' }).click()
    const panel = page.getByRole('complementary', { name: 'Suspects: card declined shows reason' })
    await expect(panel).toBeVisible()
    await expect(panel.locator('[data-testid="suspects-panel"]')).toContainText('No commit range available')
    await expect.poll(() => api.seen.filter((line) => line === SUSPECTS_READ).length).toBe(1)
    await panel.getByRole('button', { name: 'Close suspects' }).click()
    await expect(panel).toHaveCount(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('related groups: the same read, the caption verbatim', async ({ page }) => {
    const { api } = await open(page)
    await expectDrawn(groupsFrame(page), GROUPS.id)
    await groupsFrame(page).getByRole('button', { name: 'Related groups' }).click()
    await expect(plot(page)).toHaveAttribute('data-group-plot', 'relations')
    await expect(section(page, GROUPS.id).locator('[data-group-relations-caption]')).toContainText(RELATIONS_CAPTION)
    await networkQuiet(page, api)
    expect(requestsTo(api, FAILURE_GROUPS_PATH), 'both views from one read').toHaveLength(1)
  })

  test('keyboard: arrows walk the groups largest first; Enter opens the group; its "View rows" opens ONE rows panel', async ({
    page,
  }) => {
    const { api, errors } = await open(page)
    await mountEverySection(page, api)
    await expectDrawn(groupsFrame(page), GROUPS.id)
    await plot(page).focus()
    await page.keyboard.press('ArrowRight')
    await expect(plot(page)).toHaveAttribute('data-active-index', '0')
    await expect(section(page, GROUPS.id).locator('[data-group-readout]')).toContainText(FAILURE_GROUP_SPECS[0].label)
    await page.keyboard.press('Enter')
    const groupPanel = page.locator('[data-side-panel]').filter({ has: page.locator('[data-group-panel]') })
    await expect(groupPanel).toHaveCount(1)
    await expect(groupPanel.locator('[data-group-signature]')).toHaveText(FAILURE_GROUP_SPECS[0].signature)
    await groupPanel.locator('[data-group-view-rows]').click()
    const rows = await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric'), 'a group is failures').toBe('failures')
    expect(q.getAll('group_by')).toEqual(['error_signature'])
    expect(q.get('bucket_error_signature')).toBe(FAILURE_GROUP_SPECS[0].signature)
    expect(q.has('include'), 'the rows scope is the page scope, not the groups read').toBe(false)
    await expect(rows.locator('[data-rows-count]')).toContainText(`${groupFailures(FAILURE_GROUP_SPECS[0])} execution`)
    await expect(rows.locator('[data-rows-notice]')).toHaveCount(0)
    await expectHostileAsText(page, rows, 'group rows')
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  // P3: the three hosts of the page's one `rows` key are tabs now, so only the open tab's section can
  // answer it. Kept: ONE panel, from a click and from the shared URL; added: leaving the tab closes it.
  test('the readout\'s "View rows" (the rows intent) opens ONE panel; the shared URL reopens it; another tab closes it', async ({ page }) => {
    const { api } = await open(page)
    await mountEverySection(page, api)
    await plot(page).focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('ArrowRight')
    await section(page, GROUPS.id).locator('[data-mark-intent="rows"]').click()
    await expectOneRowsPanel(page, /^Executions in /)
    // Reload the shared URL: still exactly one panel (the groups section's).
    await page.goto(page.url())
    await mountEverySection(page, api)
    await expectOneRowsPanel(page, /^Executions in /)
    await sectionTab(page, 'By suite').click()
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]rows=/)
  })

  test('the ranked table: names are the buttons, hostile labels are text, Object-member signatures are data', async ({ page }) => {
    await open(page)
    await expectDrawn(groupsFrame(page), GROUPS.id)
    const table = section(page, GROUPS.id).locator('[data-group-table]')
    await expect(table).toContainText(HOSTILE_NAME)
    await expect(table).toContainText('constructor')
    await expect(table).toContainText('__proto__')
    await expectHostileAsText(page, section(page, GROUPS.id), 'groups table')
    await expectNoPrototypePollution(page, 'failure groups named constructor and __proto__')
  })

  test('clusters: asked only when the tab opens, with no days; keyed by membership; the title verbatim', async ({ page }) => {
    const { api, errors } = await open(page)
    await expectDrawn(groupsFrame(page), GROUPS.id)
    await networkQuiet(page, api)
    expect(requestsTo(api, SYSTEMIC_CLUSTERS_PATH)).toEqual([])
    await section(page, GROUPS.id).getByRole('tab', { name: 'Systemic flake clusters' }).click()
    const list = section(page, GROUPS.id).locator('[data-clusters-list]')
    await expect(list).toBeVisible({ timeout: 15_000 })
    await expect(section(page, GROUPS.id)).toContainText(CLUSTERS_TITLE)
    await expect(list.locator('[data-cluster-key]')).toHaveCount(SYSTEMIC_CLUSTERS.length)
    await expect(list.locator('[data-cluster-key]').first()).toHaveAttribute('data-cluster-key', SYSTEMIC_CLUSTERS[0].membership_key)
    await expect(section(page, GROUPS.id)).not.toContainText(/\bAI\b/)
    await expectHostileAsText(page, list, 'clusters')
    await networkQuiet(page, api)
    const asked = queryOf(api, SYSTEMIC_CLUSTERS_PATH)
    expect(asked).toHaveLength(1)
    expect(asked[0].has('days'), 'clusters are the sweep\'s 60 days, project-wide').toBe(false)
    expect(asked[0].get('project_id')).toBe(P)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('no clusters is a drawn answer: the server\'s own sentence', async ({ page }) => {
    const handlers: ApiHandlers = [[SYSTEMIC_CLUSTERS_PATH, () => systemicClustersBody(30, { empty: true })], ...FAILURES_ON]
    await open(page, handlers)
    await expectDrawn(groupsFrame(page), GROUPS.id)
    await section(page, GROUPS.id).getByRole('tab', { name: 'Systemic flake clusters' }).click()
    await expect(section(page, GROUPS.id).locator('[data-clusters-empty]')).toContainText(CLUSTERS_EMPTY_IS_NORMAL)
  })

  test('a 500 on the groups is its own error (no toast); the ladder and the scatter still draw', async ({ page }) => {
    const handlers: ApiHandlers = [[FAILURE_GROUPS_PATH, () => respond(500, { detail: 'planted groups failure' })], ...FAILURES_ON]
    const { api } = await open(page, handlers)
    await mountEverySection(page, api)
    await expect(groupsFrame(page)).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    await expect(page.getByText('planted groups failure')).toHaveCount(0)
    // P3: the other two sections are tabs; each still draws.
    await sectionTab(page, 'By suite').click()
    await mountEverySection(page, api)
    await expectDrawn(sectionFrame(page, 'failures-drill', 'Results by suite'), 'ladder')
    await sectionTab(page, 'Scatter').click()
    await mountEverySection(page, api)
    await expectDrawn(sectionFrame(page, 'scatter-project', 'Test duration vs failure rate'), 'scatter')
  })

  test('a stored 365-day window: every Wave 3 read asks for at most 90 days', async ({ page }) => {
    const { api } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready, days: 365 })
    await mountEverySection(page, api)
    // P3: every section is a tab; open each so all three read.
    for (const tab of ['By suite', 'Scatter']) {
      await sectionTab(page, tab).click()
      await mountEverySection(page, api)
    }
    expect(requestsTo(api, FAILURE_GROUPS_PATH).length).toBeGreaterThan(0)
    expect(requestsTo(api, CHART_DATA_PATH).length).toBeGreaterThan(0)
    expect(requestsTo(api, TEST_SCATTER_PATH).length).toBeGreaterThan(0)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the groups section, every impact (${theme}): bubbles, related groups, a group open, clusters`, async ({ page }) => {
      await openRollout(page, '/failures', { handlers: FAILURES_ON, ready, theme })
      await expectDrawn(groupsFrame(page), GROUPS.id)
      const only = [`[data-catalogue-section="${GROUPS.id}"]`]
      await expectNoBlockingViolations(page, theme, [], only)
      await groupsFrame(page).getByRole('button', { name: 'Related groups' }).click()
      await expectNoBlockingViolations(page, theme, [], only)
      await plot(page).focus()
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('Enter')
      await expect(page.locator('[data-group-panel]')).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], [...only, '[data-side-panel]'])
      await page.keyboard.press('Escape')
      await section(page, GROUPS.id).getByRole('tab', { name: 'Systemic flake clusters' }).click()
      await expect(section(page, GROUPS.id).locator('[data-clusters-list]')).toBeVisible({ timeout: 15_000 })
      await expectNoBlockingViolations(page, theme, [], only)
    })
  }
})

// P3 moved the groups up (the default tab, right under the Top failing table, where the verdict card,
// the body cards and nothing else used to sit): at 1280 x 600 their placeholder still starts beyond the
// near margin at load, so the proof is unchanged (`proveLazyMount` asserts that precondition itself).
test.describe('Failure groups, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no failure-groups read until the section is near, and it goes out before it is visible', async ({ page }) => {
    const { api, errors } = await open(page)
    await proveLazyMount(page, api, {
      label: GROUPS.id,
      section: GROUPS.id,
      asked: () => requestsTo(api, FAILURE_GROUPS_PATH).length,
      before: 0,
    })
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})
