/**
 * Trends › Execution time (EXEC-04b, TRND-09), with only the network, the
 * project store and the release scope mocked: the hook, the service, the
 * models and the frames are the real ones, so the scope on the wire, "null
 * is not reported, never 0", the clamp and the histogram are read off the
 * rendered page.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { ExecutionMetrics } from '@/types/executionMetrics'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const scope = vi.hoisted(() => ({ project: 'p1' as string, release: null as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: scope.project }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => scope.release }))

// EXEC-06: the CSV download is the service's (authed client, its own toast); the read stays real.
const downloadCsv = vi.hoisted(() => vi.fn(async () => true))
vi.mock('@/services/executionMetricsService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/executionMetricsService')>()
  return { executionMetricsService: { ...actual.executionMetricsService, downloadExecutionMetricsCsv: downloadCsv } }
})

import ExecutionTimeSection, {
  ALL_PROJECTS_REASON,
  HISTOGRAM_TITLE,
  ONE_DAY_EXECUTION_REASON,
  PER_DAY_TITLE,
  REGRESSIONS_TITLE,
  SUITES_TITLE,
} from './ExecutionTimeSection'
import { SERVER_TOO_OLD_REASON } from './ExecutionTimeSection.model'
import TrendsCatalogue from './TrendsCatalogue'

const URL = '/api/v1/analytics/execution-metrics'
const HOSTILE = '<img src=x onerror="window.__xss=1">'

const pct = (n: number, p50: number | null, p95: number | null) => ({ n, p50_ms: p50, p95_ms: p95, measured: p50 !== null, reason: p50 === null ? 'no run reported one' : null })

function metrics(overrides: Partial<ExecutionMetrics> = {}): ExecutionMetrics {
  return {
    project_id: 'p1', days: 14, branch: null, measured: true, reason: null,
    window_start: '2026-10-01T00:00:00+00:00', window_end: '2026-10-14T12:00:00+00:00', previous_window_start: '2026-09-17T00:00:00+00:00',
    totals: { runs: 40, wall_clock_ms: 360_000, wall_runs: 25, summed_ms: 700_000, summed_runs: 40, parallelism: 1.9 },
    per_day: {
      grain: 'run',
      points: [
        { day: '2026-10-12', runs: 2, wall_clock_ms: 18_000, wall_runs: 2, summed_ms: 34_800, summed_runs: 2 },
        { day: '2026-10-13', runs: 0, wall_clock_ms: null, wall_runs: 0, summed_ms: null, summed_runs: 0 },
        { day: '2026-10-14', runs: 1, wall_clock_ms: null, wall_runs: 0, summed_ms: 12_500, summed_runs: 1 },
      ],
    },
    suites: {
      limit: 50, total: 2, truncated: false,
      items: [
        {
          suite_key: 'checkout', suite_name: 'Checkout',
          current: { runs: 20, wall: pct(20, 5_000, 6_100), summed: pct(20, 9_000, 9_900) },
          previous: { runs: 18, wall: pct(18, 4_444, 5_900), summed: pct(18, 9_000, 9_900) },
          wall_p50_change_pct: 12.5, wall_p95_change_pct: -3, summed_p50_change_pct: 0, summed_p95_change_pct: null,
        },
        {
          suite_key: HOSTILE.toLowerCase(), suite_name: HOSTILE,
          current: { runs: 4, wall: pct(0, null, null), summed: pct(4, 800, 900) },
          previous: { runs: 0, wall: pct(0, null, null), summed: pct(0, null, null) },
          wall_p50_change_pct: null, wall_p95_change_pct: null, summed_p50_change_pct: null, summed_p95_change_pct: null,
        },
      ],
    },
    regressions: {
      rule: { recent_runs: 5, baseline_runs: 20, min_baseline_runs: 10, ratio: 1.5, same_branch: true, test_min_samples: 10, test_ratio: 1.5 },
      suites: {
        measured: true, reason: null, evaluated: 9, considered: 12, total: 1,
        items: [{
          suite_key: 'checkout', suite_name: 'Checkout', branch: 'main', metric: 'wall', ratio: 2, recent_median_ms: 10_000,
          baseline_median_ms: 5_000, recent_runs: 5, baseline_runs: 20, observed_ms: 10_200, was_slow_before: false,
          latest_run_id: 'run-9', latest_run_at: '2026-10-14T10:00:00+00:00',
        }],
      },
      tests: { measured: false, reason: 'no test in this project has a duration baseline of at least 10 samples', considered: 0, total: 0, items: [] },
    },
    slowest_tests: {
      limit: 20, measured: true, reason: null,
      items: [
        { test_fingerprint: 'f-out', test_name: 'nightly export', suite_name: 'reports', n: 3, p50_ms: 39_000, p95_ms: 40_000, max_ms: 40_100 },
        ...Array.from({ length: 19 }, (_, i) => ({
          test_fingerprint: `f-${i}`, test_name: `step ${i}`, suite_name: 'checkout', n: 12, p50_ms: 900, p95_ms: 1_900 - i * 50, max_ms: 2_000,
        })),
      ],
    },
    duration_histogram: {
      measured: true, reason: null, n: 48, p50_ms: 180, p95_ms: 2_400,
      buckets: [0, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000, 30_000, 60_000, 300_000].map((lower, i, all) => ({
        lower_ms: lower, upper_ms: i + 1 < all.length ? all[i + 1] : null, count: lower === 100 ? 40 : lower === 300_000 ? 8 : 0,
      })),
    },
    ...overrides,
  }
}

let answer: () => Promise<unknown>

function renderSection(node: ReactNode) {
  return render(
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>,
  )
}

const calls = () => get.mock.calls.filter(([url]) => url === URL).map(([, config]) => (config as { params: Record<string, unknown> }).params)
const frame = (title: string) => screen.getByRole('heading', { name: title }).closest('[data-chart-frame], section') as HTMLElement
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })

beforeEach(() => {
  scope.project = 'p1'
  scope.release = null
  answer = () => Promise.resolve({ data: metrics() })
  get.mockReset()
  get.mockImplementation(() => answer())
  downloadCsv.mockClear()
  delete (window as { __xss?: unknown }).__xss
})

describe('ExecutionTimeSection: the scope on the wire', () => {
  it('asks once for the active project and the page window; nothing else unless the page scopes it', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await screen.findByRole('heading', { name: SUITES_TITLE })
    expect(calls()).toEqual([{ project_id: 'p1', days: 14 }])
  })

  it('the release, the suites (sorted) and the window clamped to 90 days reach the request', async () => {
    scope.release = 'rel-1'
    renderSection(<ExecutionTimeSection days={365} suiteFilter={['payments', 'checkout']} />)
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0]).toEqual({ project_id: 'p1', days: 90, release_id: ['rel-1'], suite_name: ['checkout', 'payments'] })
  })

  it('Export CSV downloads the per-suite table for the SAME scope the section reads', async () => {
    scope.release = 'rel-1'
    renderSection(<ExecutionTimeSection days={365} suiteFilter={['payments', 'checkout']} />)
    const button = await screen.findByRole('button', { name: 'Export CSV' })
    fireEvent.click(button)
    expect(downloadCsv).toHaveBeenCalledTimes(1)
    expect(downloadCsv).toHaveBeenCalledWith({ projectId: 'p1', days: 90, releaseIds: ['rel-1'], suiteNames: ['checkout', 'payments'] })
    // The read went out with the same scope.
    expect(calls()[0]).toEqual({ project_id: 'p1', days: 90, release_id: ['rel-1'], suite_name: ['checkout', 'payments'] })
  })

  it('All Projects asks nothing and says execution time is per project', async () => {
    scope.project = '__ALL__'
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await settle()
    expect(calls()).toEqual([])
    expect(frame(PER_DAY_TITLE)).toHaveAttribute('data-chart-state', 'not-measured')
    expect(frame(PER_DAY_TITLE)).toHaveTextContent(ALL_PROJECTS_REASON)
    // Nothing to export without a project: no button.
    expect(screen.queryByRole('button', { name: 'Export CSV' })).toBeNull()
  })

  it('the catalogue renders it only when a host names it (Trends’ Execution time tab)', async () => {
    renderSection(<TrendsCatalogue days={14} suiteFilter={null} sections={['trends-execution']} />)
    await screen.findByRole('heading', { name: SUITES_TITLE })
    expect(document.querySelectorAll('[data-catalogue-section]')).toHaveLength(1)
    expect(document.querySelector('[data-catalogue-section="trends-execution"]')).not.toBeNull()
  })
})

describe('ExecutionTimeSection: what it draws', () => {
  it('the totals: wall clock against summed, the parallelism and the runs, each figure named', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    const totals = (await screen.findByRole('heading', { name: 'Execution time' })).closest('section') as HTMLElement
    const stat = (id: string) => totals.querySelector(`[data-execution-stat="${id}"]`) as HTMLElement
    expect(stat('wall')).toHaveTextContent('Wall clock6m 0sover the 25 runs that reported one, of 40')
    expect(stat('summed')).toHaveTextContent(/^Test time \(summed\)11m 40s/)
    expect(stat('parallelism')).toHaveTextContent(/^Parallelism1\.9×/)
    expect(stat('runs')).toHaveTextContent(/^Runs40last 14 days$/)
  })

  it('a wall clock nobody reported is "not reported", never 0 and never the summed time', async () => {
    answer = () => Promise.resolve({ data: metrics({ totals: { runs: 3, wall_clock_ms: null, wall_runs: 0, summed_ms: 52_000, summed_runs: 3, parallelism: null } }) })
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    const totals = (await screen.findByRole('heading', { name: 'Execution time' })).closest('section') as HTMLElement
    expect(totals.querySelector('[data-execution-stat="wall"]')).toHaveTextContent('Wall clocknot reportedno run in the window reported one')
    expect(totals.querySelector('[data-execution-stat="parallelism"]')).toHaveTextContent(/^Parallelismnot reported/)
    expect(totals).not.toHaveTextContent(/0ms/)
  })

  it('per day: wall vs summed drawn; the table reads the gaps as "—"', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await waitFor(() => expect(frame(PER_DAY_TITLE)).toHaveAttribute('data-chart-state', 'ready'))
    expect(frame(PER_DAY_TITLE).querySelector('[data-chart="execution-time-trend"]')).not.toBeNull()
    // One day had summed time and no wall clock: said under the plot.
    expect(frame(PER_DAY_TITLE).querySelector('[data-chart-wall-gaps]')).toHaveTextContent(
      '1 day has summed test time but no wall clock: the report carried no timestamps.',
    )
  })

  it('a one-day window: the per-day chart says it is not a trend, the rest still draws', async () => {
    renderSection(<ExecutionTimeSection days={1} suiteFilter={null} />)
    await screen.findByRole('heading', { name: SUITES_TITLE })
    expect(frame(PER_DAY_TITLE)).toHaveAttribute('data-chart-state', 'not-measured')
    expect(frame(PER_DAY_TITLE)).toHaveTextContent(ONE_DAY_EXECUTION_REASON.slice(0, 40))
  })

  it('the per-suite table: p50 / p95 with the change against the previous window; unmeasured is "not reported"; names are text', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await screen.findByRole('heading', { name: SUITES_TITLE })
    const checkout = document.querySelector('[data-execution-suite="checkout"]') as HTMLElement
    expect([...checkout.querySelectorAll('td')].map((td) => td.textContent)).toEqual([
      'Checkout', '20', '5.0s+12.5%', '6.1s−3.0%', '9.0sno change', '9.9s—',
    ])
    expect(checkout.querySelector('[data-change-tone="worse"]')).toHaveAttribute('title', '12.5% slower than the previous 14 days')
    const hostile = document.querySelector(`[data-execution-suite="${CSS.escape(HOSTILE.toLowerCase())}"]`) as HTMLElement
    expect(within(hostile).getByText(HOSTILE)).toBeInTheDocument()
    expect(hostile.querySelectorAll('td')[2]).toHaveTextContent('not reported—')
    expect(document.querySelector('[data-catalogue-section] img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('W29b: the per-suite table is the one dense Table; its five number columns are right-aligned in header and cells', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await screen.findByRole('heading', { name: SUITES_TITLE })
    const table = (document.querySelector('[data-execution-suites]') as HTMLElement).querySelector('table') as HTMLTableElement
    expect(table).toHaveAttribute('data-density', 'dense')
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((h) => h.className.split(/\s+/).filter((c) => c === 'th' || c === 'th-num'))).toEqual([
      ['th'], ['th', 'th-num'], ['th', 'th-num'], ['th', 'th-num'], ['th', 'th-num'], ['th', 'th-num'],
    ])
    const checkout = document.querySelector('[data-execution-suite="checkout"]') as HTMLElement
    expect(checkout).toHaveClass('table-row', 'cursor-default')
    expect([...checkout.querySelectorAll('td')].map((td) => td.classList.contains('td-num'))).toEqual([false, true, true, true, true, true])
    // The page-made header recipe is gone: no header carries its own size or tracking.
    for (const h of headers) expect(h.className).not.toMatch(/text-xs|tracking-wider|font-medium/)
  })

  it('regressions: the slowed suite with its figures and rule; the tests block says why it is not measured', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    const block = (await screen.findByRole('heading', { name: REGRESSIONS_TITLE })).closest('section') as HTMLElement
    const suite = block.querySelector('[data-regression-suite="checkout"]') as HTMLElement
    expect(suite).toHaveTextContent(
      'Checkout on main: 2.0× slower wall clock, a median of 10.0s over its last 5 runs against 5.0s over the 20 before. New on the latest run. Latest run',
    )
    expect(within(suite).getByRole('link', { name: 'Latest run' })).toHaveAttribute('href', '/runs/run-9')
    expect(block.querySelector('[data-regression-not-measured]')).toHaveTextContent(
      'Not measured: no test in this project has a duration baseline of at least 10 samples.',
    )
    expect(block).toHaveTextContent(/at least 1\.5× the median of the 20 runs before them, on the same branch\./)
  })

  it('20 slowest tests (p95): the 40 s outlier no longer flattens the rest (clamped, marked off scale)', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    const slowest = await waitFor(() => {
      const el = frame('20 slowest tests (p95)')
      expect(el).toHaveAttribute('data-chart-state', 'ready')
      return el
    })
    const bars = within(slowest).getAllByTestId('ranked-bar')
    expect(bars).toHaveLength(20)
    expect(bars[0]).toHaveAttribute('data-off-scale', '')
    expect(within(slowest).getByText('nightly export (reports)')).toBeInTheDocument()
    expect(within(slowest).getByText('40.0s')).toBeInTheDocument()
    // The fastest of the twenty is no sliver any more.
    expect(Number.parseFloat(bars[19].style.width)).toBeGreaterThan(50)
  })

  it('the duration distribution: the server’s buckets, their own caption, the window p50 / p95 as the takeaway', async () => {
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    const histogram = await waitFor(() => {
      const el = frame(HISTOGRAM_TITLE)
      expect(el).toHaveAttribute('data-chart-state', 'ready')
      return el
    })
    expect(histogram).toHaveTextContent('p50 180ms · p95 2.4s over 48 test executions')
    expect(histogram).toHaveTextContent(/the last bucket counts everything from 5m 0s up/)
    expect(histogram).toHaveTextContent('48 of 48 executions placed.')
    expect(histogram).not.toHaveTextContent(/1-2-5 per decade/)
  })

  it('a server without the histogram: that frame says so; the others draw', async () => {
    const { duration_histogram: _gone, ...older } = metrics()
    void _gone
    answer = () => Promise.resolve({ data: older })
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await waitFor(() => expect(frame(HISTOGRAM_TITLE)).toHaveAttribute('data-chart-state', 'not-measured'))
    expect(frame(HISTOGRAM_TITLE)).toHaveTextContent(SERVER_TOO_OLD_REASON)
    expect(frame('20 slowest tests (p95)')).toHaveAttribute('data-chart-state', 'ready')
  })

  it('the empty / denied shape: one frame with the server’s reason, no totals and no tables', async () => {
    answer = () => Promise.resolve({ data: metrics({ measured: false, reason: 'no run in this scope in the window' }) })
    renderSection(<ExecutionTimeSection days={14} suiteFilter={null} />)
    await waitFor(() => expect(frame(PER_DAY_TITLE)).toHaveAttribute('data-chart-state', 'not-measured'))
    expect(frame(PER_DAY_TITLE)).toHaveTextContent('no run in this scope in the window')
    expect(screen.queryByRole('heading', { name: 'Execution time' })).toBeNull()
    expect(screen.queryByRole('heading', { name: SUITES_TITLE })).toBeNull()
    expect(document.querySelectorAll('[data-chart-frame]')).toHaveLength(1)
  })
})
