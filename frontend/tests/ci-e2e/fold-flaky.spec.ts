/**
 * UX redesign P4 item 4 (`02-design-spec.md` §5 "Flaky tests"), on /flaky,
 * hermetic: one page for the job that was split over Flaky Coach and
 * Quarantine, tabs Detected · Proposed · Quarantined · History.
 *
 * At 1440 x 900 the page's primary content (`[data-primary]`, the Detected
 * table) starts at most 300 px below the top of `#main-content`. The
 * scroller's `scrollHeight` is printed (FOLD line) so the page's height before
 * / after can be compared: measured on the unchanged page first (P4 agent C's
 * report, `docs/viz-work/p4-agent-C.md`: 1182 px, a card list under four
 * recommendation tiles, no table).
 *
 * Also held here: the request inventory of one load, each tab's section (the
 * settled requests asked only by History), "Propose quarantine" from a
 * detected row (and the state shown once a request is live), the side panel,
 * and the two old URLs landing on their tabs.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers } from '../lib/production-pages'
import { MAIN, SHELL_BASE, expectInventory, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { LAYOUT, PROJECT_ID, isoAgo } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const P = PROJECT_ID

const RECOMMENDATIONS = ['QUARANTINE', 'INVESTIGATE', 'MONITOR', 'INVESTIGATE', 'QUARANTINE', 'MONITOR'] as const

/** Twelve flaky tests, the first three recommended for quarantine or investigation. */
const ENTRIES = Array.from({ length: 12 }, (_, i) => ({
  test_fingerprint: `fp-${String(i).padStart(2, '0')}`,
  test_name: `test_checkout_flow_${i}`,
  suite_name: i % 2 === 0 ? 'Checkout' : 'Payments',
  failure_rate: 0.6 - i * 0.04,
  total_runs: 30,
  failed_runs: 18 - i,
  flaky_since: isoAgo(20 - i),
  last_failure_at: isoAgo(i % 3),
  quarantine_recommendation: RECOMMENDATIONS[i % RECOMMENDATIONS.length],
  stabilization_actions: ['Wait for the network to settle before asserting'],
  impact_score: 90 - i * 5,
  status_history: ['PASSED', 'FAILED', 'PASSED', 'FAILED', 'PASSED', 'PASSED', 'FAILED', 'PASSED'],
  flaky_confidence_low: 0.4,
  flaky_confidence_high: 0.7,
  flaky_likely_cause: i === 0 ? 'Timing: a wait races the response' : null,
}))

const COACH = { project_id: P, total_flaky: 9, quarantine_candidates: 4, entries: ENTRIES }

function quarantineRow(i: number, status: string, fingerprint = `fp-q${i}`) {
  return {
    id: `00000000-0000-4000-8000-0000000003${String(i).padStart(2, '0')}`,
    project_id: P,
    test_fingerprint: fingerprint,
    test_name: `test_quarantine_${i}`,
    suite_name: 'Checkout',
    status,
    detection_method: 'flip_rate',
    flip_rate: 0.3,
    flip_window_size: 10,
    pass_count: 7,
    fail_count: 3,
    detected_at: isoAgo(6),
    last_failure_at: isoAgo(1),
    proposed_at: isoAgo(5),
    approved_at: null,
    approved_by_user_id: null,
    rejected_at: null,
    rejected_by_user_id: null,
    quarantine_start: null,
    quarantine_expires_at: null,
    quarantine_duration_days: 14,
    recheck_at: null,
    rationale: null,
    reviewer_notes: status === 'REJECTED' ? 'Real regression, not a flake' : null,
    owner_user_id: null,
    owner_name: 'QA Lead',
    defect_id: null,
    defect_jira_key: null,
    defect_jira_url: null,
    defect_external_status: null,
    defect_external_status_conflict: false,
    sla_days: null,
    stale_at: null,
    stale: false,
    consecutive_passes: 0,
    ready_to_promote: false,
    created_at: isoAgo(6),
    updated_at: isoAgo(1),
  }
}

/** The first detected test already has a live proposal; one more is quarantined; two are settled. */
const LIVE = [
  quarantineRow(1, 'PROPOSED', 'fp-00'),
  quarantineRow(2, 'PROPOSED'),
  quarantineRow(3, 'QUARANTINED'),
]
const SETTLED = [quarantineRow(4, 'RELEASED'), quarantineRow(5, 'REJECTED')]

const STATS = {
  detected: 0,
  proposed: 2,
  approved: 0,
  quarantined: 1,
  recheck_scheduled: 0,
  re_quarantined: 0,
  released: 1,
  rejected: 1,
  expired: 0,
  total_live: 3,
}

/**
 * Fresh state per test: a proposal made in one test never leaks into the
 * next. `proposals` records every POST /quarantine body.
 */
function flakyHandlers(proposals: unknown[] = []): ApiHandlers {
  const live = [...LIVE]
  return [
    ...LAYOUT,
    [`/api/v1/projects/${P}/flaky-coach`, () => COACH],
    ['/api/v1/quarantine/stats', () => ({ ...STATS, proposed: live.filter((r) => r.status === 'PROPOSED').length })],
    [
      '/api/v1/quarantine',
      ({ url }) => (url.searchParams.get('live_only') === 'true' ? live : [...live, ...SETTLED]),
    ],
    [
      '/api/v1/quarantine',
      ({ route }) => {
        const body = route.request().postDataJSON() as { test_fingerprint: string; test_name: string }
        proposals.push(body)
        const row = { ...quarantineRow(9, 'PROPOSED', body.test_fingerprint), test_name: body.test_name }
        live.push(row)
        return row
      },
      'POST',
    ],
  ]
}

const ready = (p: Page) => p.locator('[data-page-header] h1')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface FoldGeometry {
  scrollHeight: number
  primaryTop: number | null
  primaries: number
  blocks: Record<string, [number, number] | null>
}

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<FoldGeometry> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const span = (css: string): [number, number] | null => {
      const box = document.querySelector(css)?.getBoundingClientRect()
      return box ? [Math.round(box.top - top), Math.round(box.bottom - top)] : null
    }
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
      blocks: {
        header: span('[data-page-header]'),
        tabs: span('[data-page-header] [role="tablist"]'),
        firstTable: span('table'),
      },
    }
  }, MAIN)
}

const COACH_LINE = `GET /api/v1/projects/${P}/flaky-coach?days=30&limit=50`
const STATS_LINE = `GET /api/v1/quarantine/stats?project_id=${P}`
const LIVE_LINE = `GET /api/v1/quarantine?project_id=${P}&live_only=true&limit=200`
const ALL_LINE = `GET /api/v1/quarantine?project_id=${P}&live_only=false&limit=200`

test('the detected flaky tests table starts within the fold budget at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /flaky ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // It is the detected table, with its recommendation column, and its first row is on screen.
  const table = page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })
  await expect(table).toBeVisible()
  await expect(table.getByRole('row').nth(1)).toBeInViewport()
  await expect(table.getByRole('columnheader', { name: 'Recommendation' })).toBeVisible()
  // The tabs carry the counts (12 detected, 2 proposed, 1 quarantined, 2 settled); no stat tiles.
  await expect(page.getByRole('tab', { name: /^Detected/ })).toHaveText('Detected12')
  await expect(page.getByRole('tab', { name: /^Proposed/ })).toHaveText('Proposed2')
  await expect(page.getByRole('tab', { name: /^Quarantined/ })).toHaveText('Quarantined1')
  await expect(page.getByRole('tab', { name: /^History/ })).toHaveText('History2')
  await expect(page.getByText('Awaiting review')).toHaveCount(0)
  // One load: the analysis, the counts, and the live requests (a detected test's state).
  expectInventory(api, errors, [...SHELL_BASE, COACH_LINE, STATS_LINE, LIVE_LINE], '/flaky, Detected')
})

test('each tab renders its section; History asks for the settled requests', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky?tab=quarantined', { handlers: flakyHandlers(), ready })
  const primary = page.locator('[data-primary]')
  await expect(page.getByRole('tab', { name: /^Quarantined/ })).toHaveAttribute('aria-selected', 'true')
  await expect(primary.getByTestId('quarantine-row')).toHaveCount(1)
  await expect(primary.getByText('test_quarantine_3')).toBeVisible()

  await page.getByRole('tab', { name: /^Proposed/ }).click()
  await expect(page).toHaveURL(/\/flaky\?tab=proposed$/)
  await expect(primary.getByTestId('quarantine-row')).toHaveCount(2)

  expect(requestsTo(api, '/api/v1/quarantine').filter((line) => line === ALL_LINE)).toEqual([])
  await page.getByRole('tab', { name: /^History/ }).click()
  await expect(page.getByRole('tab', { name: /^History/ })).toHaveAttribute('aria-selected', 'true')
  // A settled row first (the Proposed tab also had two rows), then the count.
  await expect(primary.getByText('test_quarantine_5')).toBeVisible()
  await expect(primary.getByTestId('quarantine-row')).toHaveCount(2)
  expect(requestsTo(api, '/api/v1/quarantine')).toContain(ALL_LINE)

  await page.getByRole('tab', { name: /^Detected/ }).click()
  await expect(page).toHaveURL(/\/flaky$/)
  await expect(primary.getByRole('table', { name: 'Flaky tests' })).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Propose quarantine from a detected row; a test with a live request links to its tab', async ({ page }) => {
  const proposals: unknown[] = []
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(proposals), ready })
  const table = page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })

  // test_checkout_flow_0 (fp-00) already has a live proposal: its state, not the action.
  const first = table.locator('tr[data-flaky-row="fp-00"]')
  await expect(first.getByRole('button', { name: /Proposed/ })).toBeVisible()
  await expect(first.getByRole('button', { name: 'Propose quarantine' })).toHaveCount(0)

  const row = table.locator('tr[data-flaky-row="fp-04"]')
  await row.getByRole('button', { name: 'Propose quarantine' }).click()
  const dialog = page.getByRole('dialog', { name: 'Propose quarantine' })
  await dialog.getByRole('textbox').fill('Flips on CI only')
  await dialog.getByRole('button', { name: 'Propose quarantine' }).click()
  await expect(dialog).toHaveCount(0)
  expect(proposals).toEqual([
    expect.objectContaining({ project_id: P, test_fingerprint: 'fp-04', test_name: 'test_checkout_flow_4', detection_method: 'manual' }),
  ])
  // The row now shows the proposal, and the Proposed count follows.
  await expect(row.getByRole('button', { name: /Proposed/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /^Proposed/ })).toHaveText('Proposed3')
  await row.getByRole('button', { name: /Proposed/ }).click()
  await expect(page).toHaveURL(/\/flaky\?tab=proposed$/)
  await expect(page.locator('[data-primary]').getByText('test_checkout_flow_4')).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a detected row opens its details in the side panel', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await page.getByRole('button', { name: 'test_checkout_flow_0', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'test_checkout_flow_0' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Timing: a wait races the response')).toBeVisible()
  await panel.getByRole('button', { name: 'Close panel' }).click()
  await expect(panel).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('/flaky-coach and /quarantine land on their tabs', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await page.goto('/quarantine')
  await expect(page).toHaveURL(/\/flaky\?tab=quarantined$/)
  await expect(page.getByRole('tab', { name: /^Quarantined/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-primary]').getByTestId('quarantine-row')).toHaveCount(1)
  await page.goto('/flaky-coach')
  await expect(page).toHaveURL(/\/flaky$/)
  await expect(page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
