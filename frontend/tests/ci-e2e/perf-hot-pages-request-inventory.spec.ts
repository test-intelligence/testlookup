/**
 * GAP-C06, hermetic: the five hot pages make exactly their pinned set of requests on first load.
 *
 * `/runs`, `/runs/:runId`, `/defects`, `/search?q=…` and `/releases` are the most-opened pages, and none had a
 * request inventory: the harness fails closed only on UNMOCKED requests, so a duplicated read, a per-row fetch
 * or a second copy of a hook with another key passed every spec. Each load here is compared, as a multiset
 * (`expectInventory`: the same lines, the same number of times), with the list a person read and pinned.
 *
 * The run detail polls its run and tests every 5 s and the runs list every 15 s, and the harness's pinned clock
 * pins only `Date` (timers run in real time). A repeat of a line arriving 4 s or more after its first copy is
 * therefore a poll, not part of the load, and is left out before the comparison: no poll can arrive sooner, and
 * a duplicate read issued by the same render arrives within milliseconds of the first. Nothing is asserted about
 * time.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock); fixtures of the visual and in-page specs.
 */
import { test, type Page } from '@playwright/test'
import type { ApiHandlers, MockedApi } from '../lib/production-pages'
import { expectInventory, networkQuiet, openRollout, SHELL_BASE } from '../lib/rollout'
import { DEFECTS, LAYOUT, PROJECT_ID, RUN_ID, RUNS_PAGE } from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import { releasesInPageWorld } from '../visual/production/fixtures-in-page'
import { SEARCH_LOAD_READS } from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
/** A poll repeats an existing line no sooner than its interval (5 s at the fastest); a duplicate read is ms apart. */
const POLL_FLOOR_MS = 4_000

const SEARCH_TERM = 'checkout'
const SEARCH_HIT_TITLE = 'test_checkout_total_rounds_once'
const SEARCH_COUNTS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }
const SEARCH: ApiHandlers = [
  ...SEARCH_LOAD_READS,
  ...LAYOUT,
  ['/api/v1/search/index-status', () => ({ status: 'not_configured', document_count: 0, last_indexed_at: null })],
  ['/api/v1/search/entity-counts', () => SEARCH_COUNTS],
  [
    '/api/v1/search/global',
    ({ url }) => ({
      items: [
        {
          entity_type: 'test_case',
          entity_id: 'perf-search-hit',
          title: SEARCH_HIT_TITLE,
          subtitle: 'checkout-api · FAILED',
          project_id: PROJECT_ID,
          project_name: null,
          navigation_url: `/runs/${RUN_ID}`,
          relevance_score: 1,
          match_reasons: ['Matched test name'],
          metadata: {},
        },
      ],
      total: 1,
      query: url.searchParams.get('q') ?? '',
      search_type: 'keyword',
      entity_counts: { test_case: 1, test_run: 0, suite: 0, defect: 0, flaky_test: 0, release: 0 },
      page: 1,
      size: 25,
      pages: 1,
    }),
  ],
]

interface HotPage {
  name: string
  path: string
  handlers: () => ApiHandlers
  ready: (page: Page) => ReturnType<Page['locator']>
  expected: readonly string[]
}

/** The fixture's twelve older builds (`runs()`), newest first after `RUN_ID`. */
const OLDER = (n: number) => `44444444-4444-4444-8444-1000000000${String(n).padStart(2, '0')}`
const runIds = (ids: readonly string[]) => ids.map((id) => `run_id=${id}`).join('&')
const ON_SCREEN = [RUN_ID, ...Array.from({ length: 12 }, (_, n) => OLDER(n + 1))]
/** The red builds of the fixture (the failure-signature read asks only for those). */
const RED = [RUN_ID, OLDER(1), OLDER(3), OLDER(5), OLDER(8), OLDER(11)]

const PAGES: HotPage[] = [
  {
    name: 'runs list',
    path: '/runs',
    handlers: () => RUNS_PAGE,
    ready: (p) => p.locator('[data-status-banner]'),
    expected: [
      ...SHELL_BASE,
      `GET /api/v1/feature-flags/manual_upload/status?project_id=${P}`,
      // The table's page and the status banner's newest-first window: two reads, each once.
      `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
      `GET /api/v1/runs?project_id=${P}&page=1&size=500&days=30&order=newest`,
      `GET /api/v1/runs/branches?project_id=${P}&days=30`,
      // ONE batch for every run on screen, never one per row (W07 FAIL-04).
      `GET /api/v1/runs/ai-verdicts?${runIds(ON_SCREEN)}`,
      `GET /api/v1/runs/failure-signatures?${runIds(RED)}`,
    ],
  },
  {
    name: 'run detail',
    path: `/runs/${RUN_ID}`,
    handlers: () => RUN_PAGE,
    ready: (p) => p.locator('[data-primary] table'),
    expected: [
      ...SHELL_BASE,
      // The page's own Ask AI gate and AI mode (`RunDetailPage`), beside the shell's copies.
      `GET /api/v1/feature-flags/ask_ai_chat/status?project_id=${P}`,
      'GET /api/v1/settings/ai/mode',
      `GET /api/v1/runs/${RUN_ID}`,
      `GET /api/v1/runs/${RUN_ID}/tests?page=1&size=25`,
      `GET /api/v1/runs/${RUN_ID}/attribution`,
      `GET /api/v1/runs/${RUN_ID}/previous`,
    ],
  },
  {
    name: 'defects',
    path: '/defects',
    handlers: () => DEFECTS,
    ready: (p) => p.locator('section[aria-label="Defect KPIs"]'),
    expected: [
      ...SHELL_BASE,
      `GET /api/v1/analytics/defects?project_id=${P}&size=100&page=1`,
      `GET /api/v1/analytics/defects/trend?project_id=${P}&days=30`,
      `GET /api/v1/analytics/defects/escape-rate?project_id=${P}&days=30`,
      `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=0`,
      `GET /api/v1/saved-views?project_id=${P}&page=defects`,
    ],
  },
  {
    name: 'search',
    path: `/search?mode=keyword&q=${SEARCH_TERM}`,
    handlers: () => SEARCH,
    ready: (p) => p.getByText(SEARCH_HIT_TITLE).first(),
    expected: [
      ...SHELL_BASE,
      `GET /api/v1/search/global?q=${SEARCH_TERM}&project_id=${P}&page=1&size=25&record=true`,
      // Twice: `SearchPage` reads it in a plain effect, which the dev build CI serves (React.StrictMode) runs twice.
      `GET /api/v1/search/entity-counts?project_id=${P}`,
      `GET /api/v1/search/entity-counts?project_id=${P}`,
      `GET /api/v1/search/index-status?project_id=${P}`,
      'GET /api/v1/search/recent',
      `GET /api/v1/search/usage?project_id=${P}&days=1`,
      `GET /api/v1/saved-views?project_id=${P}&page=search`,
    ],
  },
  {
    name: 'releases',
    path: '/releases',
    handlers: () => releasesInPageWorld().handlers,
    ready: (p) => p.getByRole('heading', { name: 'Releases', level: 1 }),
    expected: [
      ...SHELL_BASE,
      // The page's own release list, beside the shell picker's copy.
      `GET /api/v1/releases?project_id=${P}`,
      `GET /api/v1/compliance-packs?project_id=${P}&limit=10`,
      `GET /api/v1/projects/${P}/activity?category=release&limit=8`,
    ],
  },
]

/** Every `/api` request with the Node-side time it was issued (the page's clock cannot touch it). */
function timeRequests(page: Page): { at: number; line: string }[] {
  const log: { at: number; line: string }[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) log.push({ at: Date.now(), line: `${request.method()} ${url.pathname}${url.search}` })
  })
  return log
}

/** `api` with every poll (a repeat 4 s or more after the line's first copy) taken out of `seen`. */
function firstLoad(api: MockedApi, log: { at: number; line: string }[]): MockedApi {
  const firstAt = new Map<string, number>()
  const polls = new Map<string, number>()
  for (const { at, line } of log) {
    const first = firstAt.get(line)
    if (first === undefined) firstAt.set(line, at)
    else if (at - first >= POLL_FLOOR_MS) polls.set(line, (polls.get(line) ?? 0) + 1)
  }
  const seen = [...api.seen]
  for (const [line, count] of polls) {
    for (let n = 0; n < count; n += 1) {
      const index = seen.lastIndexOf(line)
      if (index >= 0) seen.splice(index, 1)
    }
  }
  return { ...api, seen }
}

for (const hot of PAGES) {
  test(`${hot.name}: the first load makes exactly its pinned requests`, async ({ page }) => {
    const log = timeRequests(page)
    const { api, errors } = await openRollout(page, hot.path, { handlers: hot.handlers(), ready: hot.ready })
    await networkQuiet(page, api)
    expectInventory(firstLoad(api, log), errors, hot.expected, `${hot.name} (${hot.path})`)
  })
}
