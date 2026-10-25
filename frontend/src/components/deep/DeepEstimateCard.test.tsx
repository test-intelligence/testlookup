/**
 * W14 AGT-07 (`W14-contract.md` §2.5 test 5): "Cost & time" is the server's MEASURED estimate. Absence is not a
 * zero — never a `~0.5 min` from nothing, never a quiet `$0.00` for a model with no price — and a local model's
 * `$0.00` is a measured answer.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ESTIMATE_FIXTURE } from '@/test/agentOpsMocks'
import type { DeepEstimate } from '@/types/agentOps'
import DeepEstimateCard from './DeepEstimateCard'
import { ESTIMATE_LOAD_ERROR, ESTIMATE_NOT_MEASURED } from './deepRunModel'

const UNPRICED = 'No price is known for openai / gpt-9: the cost cannot be estimated.'

function renderCard(estimate: DeepEstimate | undefined, over: Partial<React.ComponentProps<typeof DeepEstimateCard>> = {}) {
  return render(
    <DeepEstimateCard
      estimate={estimate}
      error={undefined}
      onRetry={vi.fn()}
      lastRunCostDollars={null}
      spendMtdDollars={3.21}
      spendBudgetDollars={null}
      spendSoftWarnPct={null}
      {...over}
    />,
  )
}

const card = () => document.querySelector('[data-deep-estimate]') as HTMLElement

describe('DeepEstimateCard', () => {
  it('measured on a local model: $0.00 "local model — no charge", the p50 minutes, the sample, the slowest stages', () => {
    renderCard(ESTIMATE_FIXTURE)
    expect(card()).toHaveTextContent('$0.00')
    expect(card()).toHaveTextContent('local model — no charge')
    expect(card().querySelector('[data-estimate-minutes]')).toHaveTextContent('~4 min')
    expect(card()).toHaveTextContent('estimate from 12 past runs')
    const stages = card().querySelector('[data-estimate-stages]') as HTMLElement
    expect(stages.children.length).toBeLessThanOrEqual(4)
    expect(stages).toHaveTextContent('root cause analysis')
    expect(stages).toHaveTextContent('2 min · 12k tok · scaled')
    expect(stages.children[0]).toHaveAttribute('title', 'p50 per failure, scaled by the failures this run would analyse')
  })

  it('not measured: no minutes at all (never ~0.5 min), "Not measured yet", no stage rows', () => {
    renderCard({ ...ESTIMATE_FIXTURE, measured: false, reason: 'none yet', sample_pipelines: 0, minutes_p50: null, tokens_p50: null, stages: [] })
    expect(card().querySelector('[data-estimate-minutes]')).toBeNull()
    expect(card()).not.toHaveTextContent(/~\d/)
    expect(card()).not.toHaveTextContent('0 min')
    expect(card()).toHaveTextContent(ESTIMATE_NOT_MEASURED)
    expect(card().querySelector('[data-estimate-stages]')).toBeNull()
  })

  it('an unpriced model is "—" with the server\'s sentence, never $0.00', () => {
    renderCard({ ...ESTIMATE_FIXTURE, cost: { priced: false, local_model: false, usd: null, provider: 'openai', model: 'gpt-9', note: UNPRICED, price_table_updated: '2026-08-09' } })
    expect(card()).not.toHaveTextContent('$0.00')
    expect(card()).toHaveTextContent('—')
    expect(card()).toHaveTextContent('cost unknown')
    expect(card().querySelector('[data-cost-note]')).toHaveTextContent(UNPRICED)
  })

  it('a priced cloud model shows its dollars', () => {
    renderCard({ ...ESTIMATE_FIXTURE, cost: { priced: true, local_model: false, usd: 0.4249, provider: 'anthropic', model: 'haiku', note: null, price_table_updated: '2026-08-09' } })
    expect(card()).toHaveTextContent('$0.42')
    expect(card().querySelector('[data-cost-note]')).toBeNull()
  })

  it('while it loads, and when the read failed (with Retry)', () => {
    const onRetry = vi.fn()
    const { unmount } = renderCard(undefined)
    expect(card()).toHaveTextContent('estimating…')
    expect(card()).not.toHaveTextContent('min')
    unmount()
    renderCard(undefined, { error: new Error('503'), onRetry })
    expect(card()).toHaveTextContent(ESTIMATE_LOAD_ERROR)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('the last run\'s actual cost and the real budget follow the estimate', () => {
    const { unmount } = renderCard(ESTIMATE_FIXTURE, { lastRunCostDollars: 0.42 })
    expect(card()).toHaveTextContent('last run$0.42')
    expect(card()).toHaveTextContent('$3.21 spent this month · no budget set')
    unmount()
    renderCard(ESTIMATE_FIXTURE, { spendMtdDollars: 18.42, spendBudgetDollars: 50, spendSoftWarnPct: 80 })
    expect(card()).toHaveTextContent('$18.42 of $50 monthly')
    expect(card()).toHaveTextContent('warn at 80%')
  })
})
