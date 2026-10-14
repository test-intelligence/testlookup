/**
 * EXEC-05: the run's "Did anything get slower?" card, in each state the API
 * can answer. The rule it holds: a run that could not be judged says why and
 * is never shown as "No test or suite is slower".
 */
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DurationRegressionsCard from './DurationRegressionsCard'
import type { DurationRegressions, DurationSpike } from '@/services/durationRegressionsService'
import { describeDurationReason as describeReason } from '@/utils/durationRegressions'

const hook = vi.hoisted(() => ({
  useDurationRegressions: vi.fn(),
}))
vi.mock('@/hooks/useDurationRegressions', () => hook)

function spike(n: number, extra: Partial<DurationSpike> = {}): DurationSpike {
  return {
    test_fingerprint: `fp-${n}`,
    test_name: `checkout::test_${n}`,
    suite_name: 'checkout',
    observed_ms: 400,
    baseline_mean_ms: 100,
    baseline_stddev_ms: 10,
    baseline_p95_ms: 116,
    sample_count: 20,
    sigma: 30,
    delta_ms: 300,
    ratio: 4,
    previous_ms: 110,
    was_slow_before: false,
    new: false,
    ...extra,
  }
}

function findings(extra: Partial<DurationRegressions> = {}): DurationRegressions {
  return {
    run_id: 'run-1',
    schema_version: 1,
    measured: true,
    reason: null,
    stored: true,
    evaluated_at: '2026-10-14T10:00:00Z',
    spikes: [],
    spike_count: 0,
    suites: [],
    suite_regression_count: 0,
    test_check: { measured: true, reason: null },
    suite_check: { measured: true, reason: null },
    notified: { tests: [], suites: [] },
    ...extra,
  }
}

function show(state: { data?: DurationRegressions; error?: unknown; isLoading?: boolean }) {
  hook.useDurationRegressions.mockReturnValue({ data: undefined, error: undefined, isLoading: false, ...state })
  return render(<DurationRegressionsCard runId="run-1" />)
}

const card = () => screen.getByRole('region', { name: 'Duration regressions' })

beforeEach(() => hook.useDurationRegressions.mockReset())

describe('DurationRegressionsCard', () => {
  it('asks for the run it is given', () => {
    show({ isLoading: true })
    expect(hook.useDurationRegressions).toHaveBeenCalledWith('run-1')
    expect(within(card()).getByRole('heading', { name: 'Did anything get slower?' })).toBeInTheDocument()
  })

  it('lists the tests slower than baseline with the delta and sigma, and marks the new ones', () => {
    show({ data: findings({
      spikes: [spike(1, { test_name: 'checkout::test_pay', sigma: 4.1, new: true }), spike(2)],
      spike_count: 2,
    }) })
    const list = within(card())
    expect(list.getByText('Slower than baseline (2)')).toBeInTheDocument()
    const pay = card().querySelector('[data-duration-spike="fp-1"]') as HTMLElement
    expect(pay).toHaveTextContent('checkout::test_pay')
    expect(pay).toHaveTextContent('+300ms (4.1σ)')
    expect(pay).toHaveTextContent('400ms vs a mean of 100ms over 20 runs')
    expect(within(pay).getByText('New')).toBeInTheDocument()
    const other = card().querySelector('[data-duration-spike="fp-2"]') as HTMLElement
    expect(within(other).queryByText('New')).toBeNull()
    expect(list.queryByText(/No test or suite is slower/)).toBeNull()
  })

  it('lists a suite slower than its history', () => {
    show({ data: findings({
      suites: [{
        suite_name: 'checkout', suite_key: 'checkout', metric: 'wall', ratio: 1.8,
        recent_median_ms: 1800, baseline_median_ms: 1000, recent_runs: 5, baseline_runs: 20,
        observed_ms: 1900, was_slow_before: false, new: true,
      }],
      suite_regression_count: 1,
    }) })
    const row = card().querySelector('[data-duration-suite="checkout"]') as HTMLElement
    expect(row).toHaveTextContent('Suite checkout: 1.8× slower than its previous 20 runs')
    expect(row).toHaveTextContent('median wall clock of the last 5: 1.8s vs 1.0s')
  })

  it('says how much history is missing instead of "nothing got slower"', () => {
    show({ data: findings({
      measured: false,
      reason: 'not enough history (4 of 10)',
      test_check: { measured: false, reason: 'not enough history (4 of 10)' },
      suite_check: { measured: false, reason: 'not enough history (4 of 15)' },
    }) })
    expect(within(card()).getByText('Not enough history yet (4 of 10 runs)')).toBeInTheDocument()
    expect(within(card()).queryByText(/No test or suite is slower/)).toBeNull()
  })

  it('says detection is off for the project when it is', () => {
    show({ data: findings({
      measured: false,
      reason: 'duration regression detection is off for this project (feature flag perf_regression_detection)',
    }) })
    expect(card()).toHaveTextContent('Duration regression detection is off for this project')
  })

  it('a measured run with nothing slower says so, and names the check that could not be made', () => {
    show({ data: findings({ suite_check: { measured: false, reason: 'not enough history (3 of 15)' }, stored: false }) })
    const body = within(card())
    expect(body.getByText('No test or suite is slower than its baseline.')).toBeInTheDocument()
    expect(body.getByText('Suites: not enough history yet (3 of 15 runs)')).toBeInTheDocument()
    expect(body.queryByText(/^Tests:/)).toBeNull()
    expect(body.getByText(/Computed now/)).toBeInTheDocument()
  })

  it('caps each list at 10 and counts the rest from the uncapped total', () => {
    const many = Array.from({ length: 12 }, (_, i) => spike(i))
    show({ data: findings({ spikes: many, spike_count: 57 }) })
    expect(card().querySelectorAll('[data-duration-spike]')).toHaveLength(10)
    expect(within(card()).getByText('…and 47 more')).toBeInTheDocument()
  })

  it('a failed request says so', () => {
    show({ error: new Error('500') })
    expect(within(card()).getByText('Could not load the duration regressions for this run.')).toBeInTheDocument()
  })

  it('renders a test name as text, never markup', () => {
    show({ data: findings({ spikes: [spike(1, { test_name: '<img src=x onerror=alert(1)>' })], spike_count: 1 }) })
    expect(card().querySelector('img')).toBeNull()
    expect(card()).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})

describe('describeReason', () => {
  it('turns the backend reason into a sentence', () => {
    expect(describeReason('not enough history (4 of 10)')).toBe('Not enough history yet (4 of 10 runs)')
    expect(describeReason('no suite timing was recorded for this run')).toBe('No suite timing was recorded for this run')
    expect(describeReason(null)).toBe('Not evaluated for this run yet.')
  })
})

describe('DurationRegressionsCard — fed by the Timing tab (EXEC-04)', () => {
  it('draws the findings it is handed and asks for nothing', () => {
    hook.useDurationRegressions.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
    render(<DurationRegressionsCard runId="run-1" findings={findings({ spikes: [spike(1)], spike_count: 1 })} />)
    expect(card()).toHaveTextContent('checkout::test_1')
    // The hook is called with no run (SWR's null key: no request).
    expect(hook.useDurationRegressions).toHaveBeenCalledWith(null)
    expect(hook.useDurationRegressions).not.toHaveBeenCalledWith('run-1')
  })

  it('null findings: the card reads the run itself, as on the Changes tab', () => {
    hook.useDurationRegressions.mockReturnValue({ data: findings(), error: undefined, isLoading: false })
    render(<DurationRegressionsCard runId="run-1" findings={null} />)
    expect(hook.useDurationRegressions).toHaveBeenCalledWith('run-1')
    expect(card()).toHaveTextContent('No test or suite is slower than its baseline.')
  })
})
