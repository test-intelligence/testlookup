/**
 * Suite › Charts › "Suite execution time" (EXEC-04b): the suite's wall clock
 * and summed test time per run, from its execution history, with the
 * window's p50 / p95. Only the network, the project store and the release
 * scope are mocked.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { SuiteExecutionHistory, SuiteHistoryPoint } from '@/types/executionMetrics'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const scope = vi.hoisted(() => ({ project: 'p1' as string, release: null as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: scope.project }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => scope.release }))

import SuiteExecutionSection, { SUITE_EXECUTION_LIMIT, SUITE_EXECUTION_TITLE, SUITE_NO_PROJECT_REASON } from './SuiteExecutionSection'
import { percentilePair } from './ExecutionTimeSection.model'

const URL = '/api/v1/analytics/execution/suite-history'

const point = (id: string, wall: number | null, summed: number | null): SuiteHistoryPoint => ({
  run_id: id, build_number: id, run_created_at: '2026-10-14T10:00:00+00:00', branch: 'main', run_status: 'PASSED',
  source: wall === null ? 'derived_sum' : 'derived', wall_ms: wall, summed_ms: summed, parallelism: wall && summed ? Math.round((summed / wall) * 100) / 100 : null,
  started_at: null, finished_at: null, test_count: 6, timed_count: 6, passed: 6, failed: 0, broken: 0, skipped: 0, unknown: 0,
})

function history(overrides: Partial<SuiteExecutionHistory> = {}): SuiteExecutionHistory {
  return {
    project_id: 'p1', suite_name: 'Checkout', suite_key: 'checkout', branch: null, days: 30, limit: 30,
    total: 37, truncated: true, measured: true, reason: null,
    points: [point('101', 5_000, 9_100), point('102', null, 9_000), point('103', 6_100, 9_900)],
    stats: {
      wall: { n: 30, p50_ms: 5_000, p95_ms: 6_100, measured: true, reason: null },
      summed: { n: 37, p50_ms: 9_000, p95_ms: 9_900, measured: true, reason: null },
      parallelism_p50: 1.8,
    },
    ...overrides,
  }
}

let answer: () => Promise<unknown>
const calls = () => get.mock.calls.filter(([url]) => url === URL).map(([, config]) => (config as { params: Record<string, unknown> }).params)
const frame = () => screen.getByRole('heading', { name: SUITE_EXECUTION_TITLE }).closest('[data-chart-frame]') as HTMLElement

function renderSection(props: { days?: number; suiteName?: string; projectId?: string | null } = {}) {
  return render(
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>
          <SuiteExecutionSection days={props.days ?? 30} suiteName={props.suiteName ?? 'Checkout'} projectId={props.projectId} />
        </ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  scope.project = 'p1'
  scope.release = null
  answer = () => Promise.resolve({ data: history() })
  get.mockReset()
  get.mockImplementation(() => answer())
})

describe('SuiteExecutionSection', () => {
  it(`asks for the suite's last ${SUITE_EXECUTION_LIMIT} executions in the page window, for its own project`, async () => {
    scope.release = 'rel-1'
    renderSection({ projectId: 'p1' })
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0]).toEqual({ project_id: 'p1', days: 30, release_id: ['rel-1'], suite_name: 'Checkout', limit: SUITE_EXECUTION_LIMIT })
  })

  it("a suite of another project than the active one: no release (a release belongs to one project)", async () => {
    scope.release = 'rel-1'
    renderSection({ projectId: 'p2' })
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0]).toEqual({ project_id: 'p2', days: 30, suite_name: 'Checkout', limit: SUITE_EXECUTION_LIMIT })
  })

  it('draws wall and summed per run; a run without timestamps is a wall gap, said under the plot', async () => {
    renderSection()
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'ready'))
    expect(frame().querySelector('[data-chart="execution-time-trend"]')).toHaveAttribute('data-grain', 'run')
    expect(frame().querySelector('[data-chart-wall-gaps]')).toHaveTextContent('1 run has summed test time but no wall clock')
    expect(frame()).toHaveTextContent('Median wall clock 5.0s · median test time 9.0s · parallelism 1.8×')
  })

  it("the window's p50 / p95 of both figures, each over its own count, and the cut stated", async () => {
    renderSection()
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'ready'))
    expect(frame().querySelector('[data-stat="wall"]')).toHaveTextContent('5.0s / 6.1s over 30 runs')
    expect(frame().querySelector('[data-stat="summed"]')).toHaveTextContent('9.0s / 9.9s over 37 runs')
    expect(frame().querySelector('[data-suite-execution-cut]')).toHaveTextContent('The newest 3 of 37 runs in the window')
  })

  it('no wall clock in the whole window: "not reported" with the server’s reason, never 0', () => {
    expect(percentilePair({ n: 0, p50_ms: null, p95_ms: null, measured: false, reason: 'no run reported one' })).toBe(
      'not reported (no run reported one)',
    )
  })

  it('the empty shape: not measured, with the server’s reason', async () => {
    answer = () => Promise.resolve({ data: history({ measured: false, reason: 'no execution of this suite in the window', points: [] }) })
    renderSection()
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'not-measured'))
    expect(frame()).toHaveTextContent('no execution of this suite in the window')
  })

  it('no project to ask for (All Projects, none given): asks nothing and says why', async () => {
    scope.project = '__ALL__'
    renderSection()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(calls()).toEqual([])
    expect(frame()).toHaveTextContent(SUITE_NO_PROJECT_REASON)
  })
})
