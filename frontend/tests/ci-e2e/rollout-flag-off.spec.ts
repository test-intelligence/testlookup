/**
 * The flag-off request inventory of the five VIZ-408 report pages (Wave 2.6
 * C0, committed BEFORE any rollout code).
 *
 * The catalogue rollout is gated behind `viz_chart_data_api` (the heatmap
 * also `viz_advanced_charts`), default OFF. With every flag off a page must
 * make exactly the requests it made before the rollout: the same endpoints,
 * the same query strings, the same number of times. That is the "no double
 * fetch" proof (ARCH F18): a section that fetched while hidden, a hook moved
 * above its flag check, or a chart that re-asked an endpoint the page already
 * holds all show up here as one extra line, long before they show up as load.
 *
 * `INVENTORY` below was recorded on main @ 71c022e0 (Wave 2.5) with this
 * spec. After the rollout the ONLY permitted addition is the seam's own flag
 * lookup, `GET /feature-flags/viz_chart_data_api/status`, at most once per
 * page (`ALLOWED_ADDITIONS`). Anything else, added or missing, fails.
 *
 * Fail-closed, like the visual baselines (`tests/lib/production-pages.ts`):
 * a request with no fixture is aborted and fails the test by name, a request
 * that leaves the dev server fails it, and nothing is skipped. The fixtures
 * are the visual baselines' own (`tests/visual/production/fixtures.ts`), so
 * the page drawn here is the page in those PNGs.
 *
 * To re-record after a DELIBERATE change to a page's requests:
 *   ROLLOUT_INVENTORY_PRINT=1 npx playwright test --config playwright.ci.config.ts rollout-flag-off
 * prints each page's observed list; paste it into `INVENTORY` and say why in
 * the commit.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import {
  assertHermetic,
  frameByHeading,
  landmark,
  openProductionPage,
  settle,
  waitForCharts,
  type ApiHandlers,
  type MockedApi,
} from '../lib/production-pages'
import {
  NOW,
  OVERVIEW,
  PROJECT_ID,
  RELEASE_GATE,
  RUN_ID,
  SUITE,
  SUITE_DETAIL,
  SUMMARY_REPORT,
  TRENDS,
  USER,
} from '../visual/production/fixtures'

/**
 * The visual baselines' viewport (1280 x 2400): tall enough that every part
 * of every page is on screen, so a below-the-fold section that fetched with
 * its flag off would fetch here too. The clock and zone are theirs as well,
 * which keeps every `days=` and date in a query string fixed.
 */
test.use({
  viewport: { width: 1280, height: 2400 },
  timezoneId: 'UTC',
  locale: 'en-US',
  reducedMotion: 'reduce',
})

/**
 * Telemetry is not part of a page's load: the web-vitals / error beacon is
 * flushed on a timer and on page hide, so whether it lands inside the window
 * is a race. It is still answered (whitelist) and still fails closed.
 */
const NOT_INVENTORIED = /^POST \/api\/v1\/observability\//

/**
 * The seam's flag lookup, the one request the rollout may add to a
 * flag-off page (plan 2.3: "1 flag lookup" per page).
 */
const ALLOWED_ADDITIONS = [`GET /api/v1/feature-flags/viz_chart_data_api/status?project_id=${PROJECT_ID}`]

/** How long no new request may arrive before the page's load counts as over. */
const NETWORK_QUIET_MS = 1_500
const NETWORK_QUIET_TIMEOUT_MS = 15_000

interface ReportPage {
  name: string
  path: string
  handlers: ApiHandlers
  ready: (page: Page) => Locator
  /** The Wave 2.5 kit frames, by title, each with one drawn chart. */
  frames: (string | RegExp)[]
  /** Headings of what the page draws without a kit frame. */
  headings: (string | RegExp)[]
}

const PAGES: ReportPage[] = [
  {
    name: 'Overview',
    path: '/overview',
    handlers: OVERVIEW,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    frames: ['Execution trend'],
    headings: [],
  },
  {
    name: 'Trends',
    path: '/trends',
    handlers: TRENDS,
    ready: (p) => landmark(p, 'Trend metrics'),
    frames: ['Daily breakdown', 'Pass rate trend'],
    headings: ['Run cadence — last 14 days', 'Suite pass rates · today'],
  },
  {
    name: 'Summary',
    path: '/reports/summary',
    handlers: SUMMARY_REPORT,
    ready: (p) => p.getByText('Total tests', { exact: true }),
    frames: [],
    headings: [/^Per-suite breakdown/, /^Top failing tests/],
  },
  {
    name: 'Suite detail',
    path: `/coverage/suite?name=${SUITE}&days=30`,
    handlers: SUITE_DETAIL,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    frames: [/^Run history/, /^Pass rate trend/],
    headings: [],
  },
  {
    name: 'Release gate',
    path: `/release-gate/${RUN_ID}`,
    handlers: RELEASE_GATE,
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    frames: [],
    headings: [],
  },
]

const P = PROJECT_ID

/**
 * What the app shell asks for on every one of these routes, whatever the
 * page: the session and the project list (each asked twice today), the
 * three flags the shell reads, the top bar's
 * badges and notifications, the release picker and the AI settings.
 * `viz_report_context` is not among them: the report chrome that reads it
 * ships in the multi-filters runtime, which loads only with
 * `viz_multi_filters` on.
 */
const SHELL = [
  'GET /api/v1/auth/me',
  'GET /api/v1/auth/me',
  `GET /api/v1/feature-flags/ask_ai_chat/status?project_id=${P}`,
  `GET /api/v1/feature-flags/manual_upload/status?project_id=${P}`,
  `GET /api/v1/feature-flags/viz_multi_filters/status?project_id=${P}`,
  'GET /api/v1/me/assigned-failures/count',
  'GET /api/v1/notifications/history/unread-count',
  'GET /api/v1/notifications/history?unread_only=false&limit=50',
  'GET /api/v1/projects',
  'GET /api/v1/projects',
  `GET /api/v1/releases?project_id=${P}`,
  'GET /api/v1/settings/ai',
]

/**
 * Every request each page makes on load with every flag off, as
 * `METHOD path?query`; a request made twice is listed twice (order does not
 * matter, both sides are sorted). Recorded on main @ 71c022e0.
 */
const INVENTORY: Record<string, string[]> = {
  Overview: [
    ...SHELL,
    `GET /api/v1/saved-views?project_id=${P}&page=dashboard`,
    `GET /api/v1/metrics/summary?project_id=${P}&days=30`,
    `GET /api/v1/metrics/trends?project_id=${P}&days=30`,
    `GET /api/v1/analytics/failure-categories?project_id=${P}&days=30`,
    `GET /api/v1/value-metrics?project_id=${P}&days=30&months=6`,
    `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
    `GET /api/v1/projects/${P}/activity?limit=8&since=2026-08-19T12:00:00.000Z`,
  ],
  // Trends opens on its own 14 days. Until Wave 2.6 the reset to 14 ran after
  // the first render had already asked for the stored 30, so every page-owned
  // read was made twice at a cold load (before-notes 13). Wave 2.6 fixed it
  // (the page reads 14 until the store holds 14) and removed the six `days=30`
  // lines that pinned the double fetch: each read is now made once.
  Trends: [
    ...SHELL,
    `GET /api/v1/saved-views?project_id=${P}&page=trends`,
    `GET /api/v1/metrics/trends?project_id=${P}&days=14`,
    `GET /api/v1/metrics/summary?project_id=${P}&days=14`,
    `GET /api/v1/analytics/coverage?project_id=${P}&days=14`,
    `GET /api/v1/analytics/flaky-tests?project_id=${P}&days=14`,
    `GET /api/v1/runs?project_id=${P}&page=1&size=1&days=14`,
    `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=14`,
  ],
  Summary: [...SHELL, `GET /api/v1/reports/summary?project_id=${P}&days=30&mode=latest`],
  'Suite detail': [
    ...SHELL,
    `GET /api/v1/analytics/suite-detail?project_id=${P}&suite_name=${SUITE}&days=30`,
    `GET /api/v1/test-management/suites/${SUITE}/trend?project_id=${P}&days=30`,
    `GET /api/v1/suites?project_id=${P}`,
  ],
  // A second read of the AI settings, the page's own (`useAIConfig`, which
  // only this page of the five calls).
  'Release gate': [
    ...SHELL,
    `GET /api/v1/release-readiness/${RUN_ID}`,
    'GET /api/v1/scoring-model',
    `GET /api/v1/runs?project_id=${P}&page=1&size=1`,
    'GET /api/v1/settings/ai',
  ],
}

/** Wait until no request has been answered for `NETWORK_QUIET_MS`. */
async function networkQuiet(page: Page, api: MockedApi) {
  const deadline = Date.now() + NETWORK_QUIET_TIMEOUT_MS
  let count = api.seen.length + api.unhandled.length
  let since = Date.now()
  while (Date.now() - since < NETWORK_QUIET_MS) {
    expect(Date.now(), `the page kept requesting for ${NETWORK_QUIET_TIMEOUT_MS} ms`).toBeLessThan(deadline)
    await page.waitForTimeout(100)
    const now = api.seen.length + api.unhandled.length
    if (now !== count) {
      count = now
      since = Date.now()
    }
  }
}

/**
 * The observed requests minus the permitted additions (each at most once),
 * sorted. An addition seen twice leaves one copy in, which then fails the
 * comparison: a flag lookup is deduplicated per key and project, so two
 * means something re-mounted.
 */
function withoutAllowedAdditions(observed: string[]): string[] {
  const rest = [...observed]
  for (const allowed of ALLOWED_ADDITIONS) {
    const i = rest.indexOf(allowed)
    if (i >= 0) rest.splice(i, 1)
  }
  return rest.sort()
}

test.describe('Report pages with every flag off: the Wave 2.5 page, the Wave 2.5 requests', () => {
  for (const report of PAGES) {
    test(`${report.name}: exactly its recorded requests, no catalogue section`, async ({ page }) => {
      const { api, errors } = await openProductionPage(page, report.path, {
        theme: 'signal',
        now: NOW,
        me: USER,
        user: USER,
        projectId: PROJECT_ID,
        handlers: report.handlers,
        ready: report.ready,
      })

      // The page as Wave 2.5 left it: its kit frames drawn, its own cards
      // present, and nothing from the catalogue.
      for (const title of report.frames) await waitForCharts(frameByHeading(page, title))
      await expect(page.locator('[data-chart-frame]')).toHaveCount(report.frames.length)
      for (const heading of report.headings) {
        await expect(page.getByRole('heading', { name: heading })).toBeVisible()
      }
      await settle(page)
      await networkQuiet(page, api)
      await expect(page.locator('[data-catalogue-section]')).toHaveCount(0)

      const observed = api.seen.filter((line) => !NOT_INVENTORIED.test(line))
      if (process.env.ROLLOUT_INVENTORY_PRINT) {
        console.log(`INVENTORY ${JSON.stringify(report.name)}: ${JSON.stringify([...observed].sort(), null, 2)},`)
      }
      assertHermetic(api, errors)
      expect(INVENTORY[report.name], `no recorded inventory for ${report.name}`).toBeDefined()
      expect(withoutAllowedAdditions(observed), `${report.name}: requests with every flag off`).toEqual(
        [...INVENTORY[report.name]].sort(),
      )
    })
  }
})
