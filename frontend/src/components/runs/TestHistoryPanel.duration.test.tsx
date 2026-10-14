/**
 * EXEC-04: the History tab's duration chart (EXEC-03 §4 `duration_stats`,
 * `baseline` and the per-point timing):
 *
 *  - one point per run that reported a duration, a run without one is a gap
 *    (no point, the line broken there), never 0 ms;
 *  - the baseline band (mean ± 1σ) and the window's p50 / p95 lines;
 *  - a 4x run is ringed with its ratio, and the panel says how many;
 *  - an older server that sends neither block: the chart alone, no sentence
 *    about figures it never sent; no duration anywhere: said in words.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const historyState: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }

vi.mock('@/hooks/useRuns', () => ({
  useTestCaseHistory: () => historyState,
}))

import TestHistoryPanel from './TestHistoryPanel'

const point = (n: number, duration_ms: number | null, status = 'PASSED') => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  build_number: String(n),
  run_seq: n,
  status,
  duration_ms,
  created_at: `2026-10-${String(n).padStart(2, '0')}T10:00:00Z`,
  started_at: null,
  finished_at: null,
  setup_ms: null,
  call_ms: null,
  teardown_ms: null,
  worker_id: null,
  timing_source: null,
})

const BASE = {
  run_id: 'run-6',
  test_id: 't1',
  test_fingerprint: 'fp',
  test_name: 'test_pay',
  // Newest first, as the API sends it.
  history: [point(6, 900), point(5, 3600, 'FAILED'), point(4, null), point(3, 880), point(2, 920), point(1, 910)],
  flakiness: { is_flaky: false, failure_rate: 0.17, failure_rate_pct: 16.7, impact_score: 1, classification: 'HEALTHY', window_days: 30, total_runs: 6, passed: 5, failed: 1 },
  metadata: { suite: 'checkout', first_seen_at: null, last_seen_at: null },
}

const STATS = { window_days: 30, n: 14, p50_ms: 905, p95_ms: 1800, max_ms: 3600, measured: true, reason: null }
const BASELINE = { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 900, last_observed_at: null }

const section = () => screen.getByRole('region', { name: 'Duration across runs' })

describe('TestHistoryPanel — duration across runs', () => {
  beforeEach(() => {
    historyState.isLoading = false
    historyState.data = { ...BASE, duration_stats: STATS, baseline: BASELINE }
  })

  it('starts collapsed with the stats in its summary, so the page keeps its fold budget', () => {
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    const details = container.querySelector('[data-duration-history-disclosure]') as HTMLDetailsElement
    expect(details.tagName).toBe('DETAILS')
    expect(details.open).toBe(false)
    expect(container.querySelector('[data-duration-history-summary]')?.textContent).toMatch(/^p50 .+ · p95 .+ · 1 slow run$/)
  })

  it('draws one point per run with a duration, a gap for the run without, the band and both reference lines', () => {
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    const chart = container.querySelector('[data-chart="test-duration-history"]') as HTMLElement
    expect(chart).not.toBeNull()
    expect(chart.querySelectorAll('[data-duration-point]')).toHaveLength(5)
    // Run #4 reported no duration: no point, and the line is broken around it (two segments).
    expect(chart.querySelectorAll('path')).toHaveLength(2)
    expect(chart.querySelector('[data-duration-baseline-band]')).not.toBeNull()
    expect(chart.querySelector('[data-duration-ref="p50"]')).not.toBeNull()
    expect(chart.querySelector('[data-duration-ref="p95"]')).not.toBeNull()
    expect(within(section()).getByText('Last 30 days: p50 905ms · p95 1.8s · max 3.6s over 14 executions.')).toBeInTheDocument()
    expect(within(section()).getByText('Baseline: mean 900ms ± 40ms over 14 runs, p95 980ms.')).toBeInTheDocument()
  })

  it('rings the 4x run with its ratio and says so', () => {
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    const outliers = container.querySelectorAll('[data-duration-point="outlier"]')
    expect(outliers).toHaveLength(1)
    expect(outliers[0].getAttribute('data-status')).toBe('failed')
    expect(container.querySelector('[data-duration-outlier-label]')?.textContent).toBe('4× baseline')
    expect(within(section()).getByText('1 run took at least 2× the baseline mean (ringed).')).toBeInTheDocument()
  })

  it('the gap run\'s tooltip says "not reported", never 0 ms', () => {
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    const hits = container.querySelectorAll('[data-duration-hit]')
    fireEvent.mouseEnter(hits[3])
    const tip = container.querySelector('[data-duration-history-tip]') as HTMLElement
    expect(tip.textContent).toContain('Run #4')
    expect(tip.textContent).toContain('not reported')
    expect(tip.textContent).not.toMatch(/0ms/)
  })

  it('an unmeasured window and no baseline: the reason and "no baseline yet", no reference lines', () => {
    historyState.data = {
      ...BASE,
      duration_stats: { ...STATS, n: 0, p50_ms: null, p95_ms: null, max_ms: null, measured: false, reason: 'no execution in the window carried a duration' },
      baseline: null,
    }
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    expect(within(section()).getByText('Last 30 days: no execution in the window carried a duration.')).toBeInTheDocument()
    expect(within(section()).getByText('No duration baseline for this test yet.')).toBeInTheDocument()
    expect(container.querySelector('[data-duration-ref]')).toBeNull()
    expect(container.querySelector('[data-duration-baseline-band]')).toBeNull()
  })

  it('an older server (no duration_stats, no baseline key): the chart alone, nothing claimed about them', () => {
    historyState.data = { ...BASE }
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    expect(container.querySelector('[data-chart="test-duration-history"]')).not.toBeNull()
    expect(container.querySelector('[data-duration-stats]')).toBeNull()
    expect(container.querySelector('[data-duration-baseline-text]')).toBeNull()
    // No reference at all: no ratio, no outlier claimed.
    expect(container.querySelector('[data-duration-point="outlier"]')).toBeNull()
  })

  it('no run reported a duration: said in words, no empty chart', () => {
    historyState.data = { ...BASE, history: [point(2, null), point(1, null)], duration_stats: { ...STATS, measured: false, reason: 'no execution in the window carried a duration' } }
    const { container } = render(<TestHistoryPanel runId="run-6" testId="t1" />)
    expect(container.querySelector('[data-chart="test-duration-history"]')).toBeNull()
    expect(within(section()).getByText('No run of this test reported a duration.')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\b0ms\b/)
  })
})
