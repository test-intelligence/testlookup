/**
 * EXEC-04: the Run page's Timing tab, from `GET /runs/{id}/timing`
 * (EXEC-03 contract §1):
 *
 *  - a timed run: "Wall clock 9.0s · Test time 17.4s · Parallelism 1.9×",
 *    the waterfall on two worker rows, the slowest tests with their baseline,
 *    suites, workers, phases and the duration regressions handed to the card;
 *  - an old JUnit run: every null is "not reported" (never "0ms"), each
 *    unmeasured block states its reason, no empty chart frame, and the
 *    slowest list still comes from the durations;
 *  - a bar or a slowest test opens the test beside the tab.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunTiming, RunTimelineItem, RunTimingTest } from '@/types/executionMetrics'

const hooks = vi.hoisted(() => ({
  useRunTiming: vi.fn(),
  useDurationRegressions: vi.fn(),
}))
vi.mock('@/hooks/useExecutionMetrics', () => ({ useRunTiming: hooks.useRunTiming }))
vi.mock('@/hooks/useDurationRegressions', () => ({ useDurationRegressions: hooks.useDurationRegressions }))
const service = vi.hoisted(() => ({ downloadRunTimingCsv: vi.fn(() => Promise.resolve(true)) }))
vi.mock('@/services/executionMetricsService', () => ({ executionMetricsService: service }))
vi.mock('@/pages/testCase/TestCaseBody', () => ({
  default: ({ testId }: { testId: string }) => <div data-testid="test-case-body">{`body of ${testId}`}</div>,
}))

import RunTimingTab from './RunTimingTab'
import { NOT_REPORTED, msText, ratioText, reasonSentence, runSourceText } from './runTimingText'

const T0 = Date.parse('2026-10-14T10:00:00.000Z')
const iso = (ms: number) => new Date(T0 + ms).toISOString()

/** Two workers, six tests each, back to back: 9.0 s on the clock, 17.4 s summed. */
const LANES: [string, number[]][] = [
  ['0', [1500, 1200, 1800, 1400, 1300, 1500]],
  ['1', [2000, 1100, 1600, 1700, 1300, 1000]],
]
function timelineItems(): RunTimelineItem[] {
  const items: RunTimelineItem[] = []
  for (const [worker, durations] of LANES) {
    let at = worker === '0' ? 0 : 100
    const gap = worker === '0' ? 60 : 40
    durations.forEach((d, i) => {
      const id = `t-${worker}-${i}`
      items.push({
        test_id: id,
        test_name: worker === '0' && i === 2 ? 'test_pay' : `test_${worker}_${i}`,
        suite_name: 'checkout',
        status: worker === '0' && i === 2 ? 'FAILED' : 'PASSED',
        worker_id: worker,
        started_at: iso(at),
        finished_at: iso(at + d),
        offset_ms: at,
        duration_ms: d,
      })
      at += d + gap
    })
  }
  return items.sort((a, b) => a.offset_ms - b.offset_ms)
}

function slowTest(id: string, name: string, duration: number, extra: Partial<RunTimingTest> = {}): RunTimingTest {
  return {
    test_id: id, test_name: name, test_fingerprint: `fp-${id}`, suite_name: 'checkout', status: 'PASSED',
    duration_ms: duration, started_at: null, finished_at: null, setup_ms: null, call_ms: null, teardown_ms: null,
    worker_id: null, timing_source: null, baseline: null, baseline_ratio: null, ...extra,
  }
}

const TIMED: RunTiming = {
  run_id: 'run-1', project_id: 'proj-1', status: 'FAILED', build_number: '240', branch: 'main', created_at: iso(0),
  run: {
    wall_clock_ms: 9000, summed_ms: 17400, parallelism: 1.93, timing_source: 'derived',
    exec_started_at: iso(0), exec_finished_at: iso(9000), test_count: 12, timed_count: 12, duration_count: 12,
  },
  phases: {
    measured: true, reason: null, tests_with_phases: 12, setup_ms: 1440, call_ms: 14000, teardown_ms: 960,
    setup_share: 0.0883, teardown_share: 0.0589,
  },
  suites: {
    measured: true, reason: null, total: 1, truncated: false,
    items: [{
      suite_name: 'checkout', suite_key: 'checkout', source: 'derived', started_at: iso(0), finished_at: iso(9000),
      wall_ms: 9000, summed_ms: 17400, setup_ms: null, teardown_ms: null, test_count: 12, timed_count: 12,
      passed: 11, failed: 1, broken: 0, skipped: 0, unknown: 0, parallelism: 1.93,
    }],
  },
  slowest: {
    limit: 20, total: 12, measured: true, reason: null,
    items: [
      slowTest('t-1-0', 'test_1_0', 2000, { worker_id: '1' }),
      slowTest('t-0-2', 'test_pay', 1800, {
        status: 'FAILED', worker_id: '0', setup_ms: 120, call_ms: 1600, teardown_ms: 80,
        baseline: { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 1800, last_observed_at: iso(9000) },
        baseline_ratio: 2,
      }),
    ],
  },
  workers: {
    measured: true, reason: null, total: 2, truncated: false,
    lanes: [
      { worker_id: '0', test_count: 6, busy_ms: 8700, started_at: iso(0), finished_at: iso(9000), span_ms: 9000 },
      { worker_id: '1', test_count: 6, busy_ms: 8700, started_at: iso(100), finished_at: iso(9000), span_ms: 8900 },
    ],
  },
  timeline: { measured: true, reason: null, limit: 2000, total: 12, truncated: false, origin: iso(0), end: iso(9000), items: timelineItems() },
  duration_findings: {
    run_id: 'run-1', schema_version: 1, measured: true, reason: null, stored: true, evaluated_at: iso(9500),
    spikes: [{
      test_fingerprint: 'fp-t-0-2', test_name: 'test_pay', suite_name: 'checkout', observed_ms: 1800, baseline_mean_ms: 900,
      baseline_stddev_ms: 40, baseline_p95_ms: 980, sample_count: 14, sigma: 22.5, delta_ms: 900, ratio: 2,
      previous_ms: 910, was_slow_before: false, new: true,
    }],
    spike_count: 1, suites: [], suite_regression_count: 0,
    test_check: { measured: true, reason: null }, suite_check: { measured: true, reason: null }, notified: {},
  },
}

/** An old JUnit run: durations only. */
const JUNIT: RunTiming = {
  ...TIMED,
  run: {
    wall_clock_ms: null, summed_ms: 17400, parallelism: null, timing_source: 'derived_sum',
    exec_started_at: null, exec_finished_at: null, test_count: 12, timed_count: 0, duration_count: 12,
  },
  phases: { measured: false, reason: 'report carried no setup/teardown split', tests_with_phases: 0, setup_ms: null, call_ms: null, teardown_ms: null, setup_share: null, teardown_share: null },
  suites: { measured: false, reason: 'suite timing was not recorded for this run yet', total: 0, truncated: false, items: [] },
  slowest: { ...TIMED.slowest, items: [slowTest('t-1-0', 'test_1_0', 2000), slowTest('t-0-2', 'test_pay', 1800, { status: 'FAILED' })] },
  workers: { measured: false, reason: 'report carried no timestamps', total: 0, truncated: false, lanes: [] },
  timeline: { measured: false, reason: 'report carried no timestamps', limit: 2000, total: 0, truncated: false, origin: null, end: null, items: [] },
  duration_findings: null,
}

function show(state: { data?: RunTiming; error?: unknown; isLoading?: boolean }) {
  hooks.useRunTiming.mockReturnValue({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn(), ...state })
  return render(
    <MemoryRouter>
      <RunTimingTab runId="run-1" />
    </MemoryRouter>,
  )
}

const kpi = (container: HTMLElement, id: string) =>
  (container.querySelector(`[data-timing-kpi="${id}"] [data-timing-kpi-value]`) as HTMLElement).textContent

beforeEach(() => {
  hooks.useRunTiming.mockReset()
  hooks.useDurationRegressions.mockReset()
  hooks.useDurationRegressions.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
})

describe('RunTimingTab — a timed run', () => {
  it('reads the run\'s timing and states wall clock, summed time and parallelism', () => {
    const { container } = show({ data: TIMED })
    expect(hooks.useRunTiming).toHaveBeenCalledWith('run-1')
    expect(kpi(container, 'wall')).toBe('9.0s')
    expect(kpi(container, 'summed')).toBe('17.4s')
    expect(kpi(container, 'parallelism')).toBe('1.9×')
    expect(kpi(container, 'phases')).toBe('8.8% / 5.9%')
    expect(container.querySelector('[data-timing-source="derived"]')?.textContent).toMatch(/derived from the tests' start and finish times/)
    expect(container.querySelector('[data-primary]')).toHaveAttribute('aria-label', 'Execution time')
  })

  it('draws the waterfall on two worker rows with overlapping bars, the failed test in the failed pattern', () => {
    const { container } = show({ data: TIMED })
    const chart = container.querySelector('[data-chart="execution-timeline"]') as HTMLElement
    expect(chart).toHaveAttribute('data-lane-mode', 'worker')
    expect(Array.from(chart.querySelectorAll('[data-timeline-lane]')).map((l) => l.getAttribute('data-worker-id'))).toEqual(['0', '1'])
    expect(chart.querySelectorAll('[data-timeline-bar]')).toHaveLength(12)
    const failed = chart.querySelector('[data-timeline-bar][data-status="failed"]') as Element
    expect(failed.getAttribute('data-test-id')).toBe('t-0-2')
    expect(failed.getAttribute('fill')).toMatch(/chart-pattern-failed/)
    // The two rows run at the same time: worker 1's first bar starts before worker 0's first ends.
    const x = (id: string) => Number(chart.querySelector(`[data-test-id="${id}"]`)?.getAttribute('x'))
    const w = (id: string) => Number(chart.querySelector(`[data-test-id="${id}"]`)?.getAttribute('width'))
    expect(x('t-1-0')).toBeLessThan(x('t-0-0') + w('t-0-0'))
  })

  it('lists the slowest tests with the baseline ratio and the phases', () => {
    show({ data: TIMED })
    const slow = screen.getByRole('region', { name: 'Slowest tests' })
    expect(within(slow).getByRole('heading', { name: '20 slowest tests' })).toBeInTheDocument()
    const pay = slow.querySelector('[data-timing-slow-test="t-0-2"]') as HTMLElement
    expect(pay).toHaveTextContent('test_pay')
    expect(pay).toHaveTextContent('1.8s')
    expect(pay).toHaveTextContent('2× baseline')
    expect(pay).toHaveTextContent('mean 900ms over 14 runs')
    expect(pay).toHaveTextContent('120ms · 1.6s · 80ms')
    expect(pay.querySelector('[data-timing-baseline-tick]')).not.toBeNull()
    const other = slow.querySelector('[data-timing-slow-test="t-1-0"]') as HTMLElement
    expect(other).toHaveTextContent('no baseline yet')
    expect(other).toHaveTextContent(NOT_REPORTED)
  })

  it('shows suites, workers and phases, and hands the findings to the regressions card (no second read)', () => {
    const { container } = show({ data: TIMED })
    const suite = container.querySelector('[data-timing-suite="checkout"]') as HTMLElement
    expect(suite).toHaveTextContent('9.0s')
    expect(suite).toHaveTextContent('17.4s')
    expect(suite).toHaveTextContent('1.9×')
    expect(suite.querySelector('[data-timing-suite-source="derived"]')).not.toBeNull()
    expect(container.querySelector('[data-timing-worker="1"]')).toHaveTextContent('8.7s')
    expect(container.querySelector('[data-timing-phase-total="setup_ms"]')).toHaveTextContent('Setup: 1.4s')
    const card = screen.getByRole('region', { name: 'Duration regressions' })
    expect(card).toHaveTextContent('test_pay')
    expect(hooks.useDurationRegressions).not.toHaveBeenCalledWith('run-1')
  })

  it('a bar opens its test beside the tab, and so does a slowest test', () => {
    const { container } = show({ data: TIMED })
    fireEvent.click(container.querySelector('[data-timeline-bar][data-test-id="t-0-2"]') as Element)
    expect(screen.getByTestId('test-case-body')).toHaveTextContent('body of t-0-2')
    expect(screen.getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', '/runs/run-1/tests/t-0-2')
    expect(container.querySelector('[data-timeline-mark="selected"]')).not.toBeNull()
    fireEvent.click(within(screen.getByRole('region', { name: 'Slowest tests' })).getByRole('button', { name: 'test_1_0' }))
    expect(screen.getByTestId('test-case-body')).toHaveTextContent('body of t-1-0')
  })
})

describe('RunTimingTab — Export CSV (EXEC-06)', () => {
  it("downloads this run's timing through the service, never a bare link", () => {
    const { container } = show({ data: TIMED })
    const button = container.querySelector('[data-timing-export]') as HTMLButtonElement
    expect(button.tagName).toBe('BUTTON')
    expect(container.querySelector('a[href*="timing.csv"]')).toBeNull()
    fireEvent.click(button)
    expect(service.downloadRunTimingCsv).toHaveBeenCalledWith(TIMED.run_id)
  })
})

describe('RunTimingTab — an old JUnit run (no timestamps)', () => {
  it('says "not reported" for every null, never "0ms"', () => {
    const { container } = show({ data: JUNIT })
    expect(kpi(container, 'wall')).toBe(NOT_REPORTED)
    expect(kpi(container, 'parallelism')).toBe(NOT_REPORTED)
    expect(kpi(container, 'phases')).toBe(NOT_REPORTED)
    expect(kpi(container, 'summed')).toBe('17.4s')
    expect(container.querySelector('[data-timing-kpi="wall"]')).toHaveAttribute('data-measured', 'false')
    expect(container.querySelector('[data-timing-source="derived_sum"]')?.textContent).toMatch(/no run clock/)
    expect(container.textContent).not.toMatch(/\b0ms\b/)
    expect(container.textContent).not.toMatch(/—/)
  })

  it('shows each unmeasured block\'s reason as text, and no empty chart', () => {
    const { container } = show({ data: JUNIT })
    expect(container.querySelector('[data-chart="execution-timeline"]')).toBeNull()
    const timeline = container.querySelector('[data-timing-unmeasured="timeline"]') as HTMLElement
    expect(timeline.textContent).toMatch(/^Report carried no timestamps\./)
    expect(timeline.textContent).toMatch(/only durations are shown below/)
    expect(container.querySelector('[data-timing-unmeasured="workers"]')).toHaveTextContent('Report carried no timestamps.')
    expect(container.querySelector('[data-timing-unmeasured="suites"]')).toHaveTextContent('Suite timing was not recorded for this run yet.')
    expect(container.querySelector('[data-timing-unmeasured="phases"]')).toHaveTextContent('Report carried no setup/teardown split.')
    // The durations still rank the slowest tests.
    expect(container.querySelectorAll('[data-timing-slow-test]')).toHaveLength(2)
  })

  it('null findings: the regressions card reads the run itself', () => {
    show({ data: JUNIT })
    expect(hooks.useDurationRegressions).toHaveBeenCalledWith('run-1')
  })

  it('a hostile test name is text', () => {
    const hostile = '<img src=x onerror=alert(1)>'
    const data = { ...JUNIT, slowest: { ...JUNIT.slowest, items: [slowTest('t-x', hostile, 900)] } }
    const { container } = show({ data })
    expect(container.querySelector('[data-timing-slow-test="t-x"]')).toHaveTextContent(hostile)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('RunTimingTab — loading and failure', () => {
  it('a loading read shows a spinner, not zeros', () => {
    const { container } = show({ isLoading: true })
    expect(container.querySelector('[data-timing-loading]')).not.toBeNull()
    expect(container.querySelector('[data-timing-kpi]')).toBeNull()
  })

  it('a failed read says so', () => {
    const { container } = show({ error: new Error('404') })
    expect(container.querySelector('[data-timing-error]')).toHaveTextContent('Could not load the timing of this run.')
  })
})

describe('helpers', () => {
  it('format null as "not reported" and a real zero as 0ms', () => {
    expect(msText(null)).toBe(NOT_REPORTED)
    expect(msText(undefined)).toBe(NOT_REPORTED)
    expect(msText(0)).toBe('0ms')
    expect(msText(899.6)).toBe('900ms')
    expect(ratioText(null)).toBe(NOT_REPORTED)
    expect(ratioText(1.93)).toBe('1.9×')
    expect(reasonSentence('report carried no timestamps')).toBe('Report carried no timestamps.')
    expect(reasonSentence(null)).toBe('Not measured.')
    expect(runSourceText(null)).toMatch(/^Not timed yet/)
    expect(runSourceText('report')).toMatch(/own run clock/)
  })
})
