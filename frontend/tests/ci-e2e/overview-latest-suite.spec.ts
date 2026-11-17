/**
 * D-0022 (GPT-6 exploratory, E2E-0024), hermetic: the Dashboard's "latest suite" is the suite of the run
 * TestLookup received last, the same run the Runs table lists first. `GET /runs` lists the highest build label
 * first by default (UI-003, right within one CI series); across suites only the time received compares, so the
 * Dashboard and Runs ask `order=newest`.
 *
 * World A is GPT-6's three runs: `GPT6-IT01-W4-CF06` (received 14 h before `NOW`) sorts above
 * `GPT6-IT01-W3-quarantine-after` / `-during` (30 min and 2 h before) by label. World B has more than 100 runs:
 * the newest (label `1`, suite `Fresh`) falls past the first 100 by label, so only the server's `newest` order
 * can return it. The `/runs` handler here models the server: the natural build-label order by default, the
 * received order with `order=newest`, `days` (omitted = 30), `suite_name`, `page`, `size`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `OVERVIEW_ON`, `RUNS_PAGE`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, queryOf } from '../lib/rollout'
import type { ApiHandlers, ApiRequest } from '../lib/production-pages'
import { NOW, OVERVIEW_ON, PROJECT_ID, RUNS_PAGE, runs } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Run = ReturnType<typeof runs>[number]

const at = (hoursAgo: number) => new Date(NOW.getTime() - hoursAgo * 3_600_000).toISOString()
const template = runs()[1]

function run(n: number, build: string, suite: string, hoursAgo: number): Run {
  const when = at(hoursAgo)
  return {
    ...template,
    id: `44444444-4444-4444-8444-${String(900000000000 + n)}`,
    project_id: PROJECT_ID,
    build_number: build as unknown as number,
    created_at: when,
    start_time: when,
    end_time: when,
    exec_started_at: when,
    exec_finished_at: when,
    primary_suite_name: suite,
    suite_names: [suite],
    run_seq: n,
  }
}

/** `natural_build_number_key`: the label's digit runs as a number array; a label with no digit sorts first. */
function naturalKey(label: string): number[] | null {
  const digits = label.match(/[0-9]+/g)
  return digits ? digits.map(Number) : null
}

function compareBuild(a: Run, b: Run): number {
  const ka = naturalKey(String(a.build_number))
  const kb = naturalKey(String(b.build_number))
  if (ka === null || kb === null) return ka === kb ? 0 : ka === null ? -1 : 1 // NULLS FIRST
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const x = ka[i] ?? -1
    const y = kb[i] ?? -1
    if (x !== y) return y - x // desc
  }
  return String(b.build_number).localeCompare(String(a.build_number))
    || Date.parse(b.created_at) - Date.parse(a.created_at)
    || (a.id < b.id ? 1 : -1)
}

/** `GET /runs` as the server answers it (see the file comment). */
function serverRuns(source: Run[]) {
  return ({ url }: ApiRequest) => {
    const q = url.searchParams
    const days = q.get('days') === null ? 30 : Number(q.get('days'))
    const suite = (q.get('suite_name') ?? '').trim().toLowerCase()
    const size = Number(q.get('size') ?? 20)
    const page = Math.max(1, Number(q.get('page') ?? 1))
    const all = source
      .filter((r) => days === 0 || Date.parse(r.created_at) >= NOW.getTime() - days * 86_400_000)
      .filter((r) => !suite || r.suite_names.some((s) => s.toLowerCase() === suite))
      .sort(q.get('order') === 'newest'
        ? (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || (a.id < b.id ? 1 : -1)
        : compareBuild)
    const items = all.slice((page - 1) * size, page * size).map((r) => ({ ...r, ai_recommendation: null }))
    return { items, total: all.length, page, size, pages: all.length ? Math.ceil(all.length / size) : 0 }
  }
}

const GPT6 = [
  run(1, 'GPT6-IT01-W4-CF06', 'GPT6 CF06 review', 14),
  run(2, 'GPT6-IT01-W3-quarantine-during', 'GPT6 W3 synthetic', 2),
  run(3, 'GPT6-IT01-W3-quarantine-after', 'GPT6 W3 synthetic', 0.5),
]

const MANY = [
  ...Array.from({ length: 120 }, (_, k) => run(100 + k, `W9-${k + 1}`, 'Bulk', 24 + k)),
  run(99, '1', 'Fresh', 0.25),
]

const overview = (source: Run[]): ApiHandlers => [['/api/v1/runs', serverRuns(source)], ...OVERVIEW_ON]
const runsPage = (source: Run[]): ApiHandlers => [['/api/v1/runs', serverRuns(source)], ...RUNS_PAGE]

const dashboard = (page: Page) => page.getByRole('heading', { level: 1, name: 'Dashboard' })
const subtitle = (page: Page) => page.getByText(/latest suite /)

test('GPT-6\'s three runs: the Dashboard names the suite of the run received last, and Runs lists that run first', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/overview', { handlers: overview(GPT6), ready: dashboard })
  await expect(subtitle(page)).toContainText('latest suite GPT6 W3 synthetic')
  await expect(subtitle(page)).not.toContainText('GPT6 CF06 review')
  await networkQuiet(page, api)
  const windowed = queryOf(api, '/api/v1/runs', (q) => q.get('size') === '100')
  expect(windowed.length).toBeGreaterThan(0)
  expect(windowed.every((q) => q.get('order') === 'newest')).toBe(true)

  // The suite control: the newest run of that suite.
  await page.getByTitle('Filter dashboard metrics by test suite').selectOption('GPT6 CF06 review')
  await expect(subtitle(page)).toContainText('latest suite GPT6 CF06 review')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Runs lists the same run first', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', {
    handlers: runsPage(GPT6),
    ready: (p) => p.locator(`#run-row-${GPT6[2].id}`),
  })
  const rows = page.locator('[id^="run-row-"]')
  await expect(rows.first()).toHaveAttribute('id', `run-row-${GPT6[2].id}`)
  await networkQuiet(page, api)
  const list = queryOf(api, '/api/v1/runs', (q) => q.get('size') === '500')
  expect(list.length).toBeGreaterThan(0)
  expect(list.every((q) => q.get('order') === 'newest')).toBe(true)
  assertHermetic(api, errors)
})

test('more than 100 runs: the newest run falls past the first 100 by label, and is still the latest suite', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/overview', { handlers: overview(MANY), ready: dashboard, days: 90 })
  await expect(subtitle(page)).toContainText('latest suite Fresh')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
