/**
 * REL-01: a release's detail shows its recorded verdict, the scorecard, every
 * blocking reason (with the count of those not shown), and the baseline
 * comparison — or the server's reason there is none. Nothing is computed in
 * the browser.
 */
import { fireEvent, render, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReleaseBaselineComparison, ReleaseGateDetail } from '@/types/releases'

const gateState = vi.hoisted(() => ({
  gate: { data: undefined as ReleaseGateDetail | null | undefined, error: undefined as unknown, isLoading: false },
  baseline: { data: undefined as ReleaseBaselineComparison | undefined, error: undefined as unknown },
}))
const retry = vi.fn()
vi.mock('@/hooks/useReleaseGate', () => ({
  useReleaseGate: () => ({ ...gateState.gate, mutate: retry }),
  useReleaseBaseline: () => gateState.baseline,
}))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: [{ id: 'rel-base', name: '2026.10' }], total: 1 } }),
  useRelease: vi.fn(),
}))

import GatePanel from './GatePanel'

const NO_GO: ReleaseGateDetail = {
  release_id: 'rel-1',
  verdict: 'NO_GO',
  decided_at: '2026-10-14T09:30:00+00:00',
  blocking_reasons: ['checkout-e2e::0f3a9b2c1d4e5f60 is FAILED', 'checkout-e2e::1a2b3c4d5e6f7081 is BROKEN'],
  blocking_reasons_display: ['test_pay (checkout-e2e) is FAILED', 'test_refund (checkout-e2e) is BROKEN'],
  blocking_total: 25,
  scorecard: {
    denominator: 120, evidence_count: 118, pass_rate: 96.61, run_count: 4, measured: true,
    insufficient_reason: null, evidence_floor: 5,
    status_counts: { PASSED: 114, FAILED: 3, BROKEN: 1, SKIPPED: 0, UNKNOWN: 0 },
  },
  from_snapshot: true,
}

beforeEach(() => {
  retry.mockReset()
  gateState.gate = { data: NO_GO, error: undefined, isLoading: false }
  gateState.baseline = {
    data: { comparable: true, baseline_release_id: 'rel-base', pass_rate_delta: -1.25, denominator_delta: 3, evidence_delta: -2 },
    error: undefined,
  }
})

const panel = (container: HTMLElement) => container.querySelector('section#gate') as HTMLElement

describe('GatePanel', () => {
  it('is the #gate section: verdict, when it was evaluated, and the scorecard', () => {
    const { container } = render(<GatePanel releaseId="rel-1" />)
    const section = panel(container)
    expect(within(section).getByRole('heading', { name: 'Release gate' })).toBeInTheDocument()
    expect(within(section).getByText('No-Go')).toBeInTheDocument()
    const evaluated = within(section).getByText(/^Evaluated /)
    expect(evaluated).toHaveAttribute('title', '2026-10-14T09:30:00+00:00')
    const scorecard = section.querySelector('[data-gate-scorecard]') as HTMLElement
    for (const [label, value] of [['Pass rate', '96.6%'], ['Evidence', '118'], ['In scope', '120'], ['Runs', '4']]) {
      expect(within(scorecard).getByText(label).nextElementSibling, label).toHaveTextContent(value)
    }
    expect(section.querySelector('[data-gate-status-counts]')).toHaveTextContent('Passed 114 · Failed 3 · Broken 1 · Skipped 0 · Unknown 0')
  })

  it('lists the readable blocking reasons and how many are not shown', () => {
    const { container } = render(<GatePanel releaseId="rel-1" />)
    const list = within(panel(container)).getByRole('list', { name: 'Blocking' })
    expect(within(list).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      'test_pay (checkout-e2e) is FAILED',
      'test_refund (checkout-e2e) is BROKEN',
    ])
    expect(panel(container).querySelector('[data-gate-truncated]')).toHaveTextContent('+23 more')
  })

  it('falls back to the stored reasons when the server sent no display strings', () => {
    gateState.gate.data = { ...NO_GO, blocking_reasons_display: undefined, blocking_total: 2 }
    const { container } = render(<GatePanel releaseId="rel-1" />)
    const list = within(panel(container)).getByRole('list', { name: 'Blocking' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(panel(container).querySelector('[data-gate-truncated]')).toBeNull()
  })

  it('states the deltas against the named baseline when they are comparable', () => {
    const { container } = render(<GatePanel releaseId="rel-1" />)
    const deltas = panel(container).querySelector('[data-baseline-deltas]') as HTMLElement
    expect(within(deltas).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      'Pass rate -1.3 pp vs 2026.10',
      'Tests in scope +3',
      'Evidence -2',
    ])
  })

  it("prints the server's reason when the baseline is not comparable, and no numbers", () => {
    gateState.baseline.data = { comparable: false, reason: 'the baseline is below the evidence floor' }
    const { container } = render(<GatePanel releaseId="rel-1" />)
    expect(panel(container).querySelector('[data-baseline-reason]')).toHaveTextContent('the baseline is below the evidence floor')
    expect(panel(container).querySelector('[data-baseline-deltas]')).toBeNull()
  })

  it('a never-evaluated release (null) says so, with "Not evaluated yet"', () => {
    gateState.gate.data = null
    const { container } = render(<GatePanel releaseId="rel-1" onEvaluate={vi.fn()} />)
    const section = panel(container)
    expect(within(section).getByText('This release has not been evaluated yet.')).toBeInTheDocument()
    expect(within(section).getByText('Not evaluated yet')).toBeInTheDocument()
    expect(section.querySelector('[data-gate-scorecard]')).toBeNull()
    expect(within(section).getByRole('button', { name: 'Evaluate gate' })).toBeEnabled()
  })

  it('an unmeasured verdict states why', () => {
    gateState.gate.data = {
      ...NO_GO, verdict: 'NOT_EVALUATED', blocking_reasons: [], blocking_reasons_display: undefined, blocking_total: 0,
      scorecard: { ...NO_GO.scorecard, evidence_count: 3, pass_rate: null, measured: false, insufficient_reason: '3 of 120 tests carry evidence; 5 required' },
    }
    const { container } = render(<GatePanel releaseId="rel-1" />)
    const section = panel(container)
    expect(within(section).getByText('Not evaluated')).toBeInTheDocument()
    expect(section.querySelector('[data-gate-insufficient]')).toHaveTextContent('3 of 120 tests carry evidence; 5 required')
    expect(within(section.querySelector('[data-gate-scorecard]') as HTMLElement).getByText('Pass rate').nextElementSibling).toHaveTextContent(/^—$/)
  })

  it('offers Re-evaluate only to a QA lead (onEvaluate), busy while evaluating', () => {
    const onEvaluate = vi.fn()
    const { container, rerender } = render(<GatePanel releaseId="rel-1" />)
    expect(within(panel(container)).queryByRole('button', { name: /evaluat/i })).toBeNull()
    rerender(<GatePanel releaseId="rel-1" onEvaluate={onEvaluate} />)
    fireEvent.click(within(panel(container)).getByRole('button', { name: 'Re-evaluate' }))
    expect(onEvaluate).toHaveBeenCalledTimes(1)
    rerender(<GatePanel releaseId="rel-1" onEvaluate={onEvaluate} evaluating />)
    expect(within(panel(container)).getByRole('button', { name: 'Evaluating…' })).toBeDisabled()
  })

  it('an outage is not "never evaluated": it says the gate could not be loaded, with Retry', () => {
    gateState.gate = { data: undefined, error: new Error('500'), isLoading: false }
    const { container } = render(<GatePanel releaseId="rel-1" />)
    expect(within(panel(container)).queryByText('This release has not been evaluated yet.')).toBeNull()
    fireEvent.click(within(panel(container)).getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
