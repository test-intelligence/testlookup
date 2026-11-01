/**
 * The Flaky tests › Detected table (`FlakyCoachBody`) and its recommendation
 * pill. The routed page is `FlakyTestsPage` (`/flaky`; `/flaky-coach`
 * redirects there).
 *
 * W25c HYG-01: the unrouted Flaky Coach page shell is deleted, and with it the
 * cases on its header subtitle (the singular / plural regression: "1 flaky
 * tests · 1 quarantine candidates"). That rule lives in `flakySubtitle`, which
 * the routed page renders, and is pinned by `flaky/flakyModel.test.ts`.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { FlakyCoachEntry } from '@/services/testHealthService'

import { FlakyCoachBody, RecommendationPill } from './FlakyCoachPage'
import { formatDay } from '@/utils/formatters'

// ── W09 FLK-03: the four-signal flakiness score on the Detected rows ─────────

const SCORED = {
  status: 'scored', score: 0.62, confidence: 'medium', observations: 23, min_observations: 5, window_days: 30,
  computed_at: '2026-10-16T02:10:00+00:00',
  components: [
    { name: 'result_volatility', value: 0.7, weight: 0.45 },
    { name: 'retry_rate', value: 0.5, weight: 0.25 },
    { name: 'environment_instability', value: 0.4, weight: 0.2 },
    { name: 'duration_variance', value: 0.1, weight: 0.1 },
  ],
}
const INSUFFICIENT = {
  status: 'insufficient', score: null, confidence: 'none', observations: 3, min_observations: 5, window_days: 30,
  computed_at: null, components: [],
}
const PENDING = { ...INSUFFICIENT, status: 'pending', observations: 9 }

function scoreEntry(fp: string, flaky_score: unknown): FlakyCoachEntry {
  return {
    test_fingerprint: fp, test_name: `test_${fp}`, suite_name: 'Checkout', failure_rate: 0.4, total_runs: 20,
    failed_runs: 8, flaky_since: null, last_failure_at: null, quarantine_recommendation: 'INVESTIGATE',
    stabilization_actions: [], impact_score: 10, status_history: ['PASSED', 'FAILED'],
    flaky_score: flaky_score as FlakyCoachEntry['flaky_score'],
  }
}

const SCORE_ROWS = [
  scoreEntry('scored', SCORED),
  scoreEntry('thin', INSUFFICIENT),
  scoreEntry('pending', PENDING),
  scoreEntry('failed', null),
  scoreEntry('garbage', { status: 'scored', score: 'very', components: 'all of them' }),
]

// W27 PAN-04: the open test rides the URL (`?test=<fingerprint>`), so the body renders inside a router.
const renderBody = (entries: FlakyCoachEntry[] = SCORE_ROWS) =>
  render(
    <MemoryRouter>
      <FlakyCoachBody entries={entries} />
    </MemoryRouter>,
  )

/** The cell under "Flakiness score" in a row. */
function scoreCellOf(fp: string): HTMLElement {
  const headers = screen.getAllByRole('columnheader').map((th) => th.textContent ?? '')
  const index = headers.findIndex((text) => text.startsWith('Flakiness score'))
  const row = document.querySelector(`tr[data-flaky-row="${fp}"]`) as HTMLElement
  return row.querySelectorAll('td')[index] as HTMLElement
}

describe('FlakyCoachBody — the Flakiness score column (W09 FLK-03)', () => {
  it('heads ONE column after Fail %, "(4 signals)" under it, its title naming both detectors', () => {
    renderBody()
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent ?? '')
    const index = headers.findIndex((text) => text.startsWith('Flakiness score'))
    expect(headers[index - 1]).toBe('Fail %')
    expect(headers[index]).toBe('Flakiness score(4 signals)')
    expect(headers).toEqual(expect.arrayContaining(['Fail %', 'Recommendation', 'Impact']))
    expect(screen.getAllByRole('columnheader')[index]).toHaveAttribute(
      'title',
      'Result volatility, retries, environment instability and duration variance, from the nightly score. Separate from Fail %, which is the failure-rate detector.',
    )
  })

  // W29b UIX-06: the ONE table at dense density; Fail % and Impact right-aligned in header and cells.
  it('is the dense Table: Fail % and Impact are number columns, the score header keeps its two tight lines', () => {
    renderBody()
    const table = screen.getByRole('table', { name: 'Flaky tests' })
    expect(table).toHaveAttribute('data-density', 'dense')
    const headers = within(table).getAllByRole('columnheader')
    const fail = headers.findIndex((th) => th.textContent === 'Fail %')
    const impact = headers.findIndex((th) => th.textContent === 'Impact')
    expect(headers[fail]).toHaveClass('th', 'th-num')
    expect(headers[impact]).toHaveClass('th', 'th-num')
    expect(headers.find((th) => th.textContent?.startsWith('Flakiness score'))).toHaveClass('th', 'py-0.5', 'leading-tight')
    const row = document.querySelector('tr[data-flaky-row="scored"]') as HTMLElement
    expect(row).toHaveClass('table-row')
    expect(row.children[fail]).toHaveClass('td', 'td-num')
    expect(row.children[impact]).toHaveClass('td', 'td-num')
  })

  it('prints each state on one line, and never 0.00 for a test without a score', () => {
    renderBody()
    const scored = scoreCellOf('scored')
    expect(scored).toHaveTextContent('0.62medium')
    expect(within(scored).getByText('medium')).toHaveAttribute('data-score-band', 'medium')
    expect(scored.querySelector('[title="Flakiness score 0.62 · medium confidence · 23 observations"]')).not.toBeNull()
    expect(scoreCellOf('thin')).toHaveTextContent(/^insufficient$/)
    expect(scoreCellOf('thin').querySelector('[title="3 of 5 observations"]')).not.toBeNull()
    expect(scoreCellOf('pending')).toHaveTextContent(/^not scored yet$/)
    expect(scoreCellOf('pending').querySelector('[title="Scores are computed nightly"]')).not.toBeNull()
    expect(scoreCellOf('failed')).toHaveTextContent(/^—$/)
    expect(scoreCellOf('failed').querySelector('[title="Score not available"]')).not.toBeNull()
    // A malformed enrichment reads as unavailable, and the page still renders.
    expect(scoreCellOf('garbage')).toHaveTextContent(/^—$/)
    for (const fp of ['thin', 'pending', 'failed', 'garbage']) expect(scoreCellOf(fp)).not.toHaveTextContent('0.00')
    for (const fp of ['scored', 'thin', 'pending', 'failed']) {
      expect(scoreCellOf(fp).firstElementChild).toHaveClass('whitespace-nowrap')
    }
  })

  it('the details panel opens with the score section: the bar, the evidence, the four signals and their sum', () => {
    renderBody()
    fireEvent.click(screen.getByRole('button', { name: 'test_scored' }))
    const panel = screen.getByRole('complementary', { name: 'test_scored' })
    const section = within(panel).getByRole('region', { name: 'Flakiness score (4 signals)' })
    expect(within(section).getByText('0.62')).toBeInTheDocument()
    expect(within(section).getByText('medium · 23 observations')).toBeInTheDocument()
    expect(within(section).getByText('over the last 30 days, scored 16 Oct 02:10 UTC')).toBeInTheDocument()
    for (const line of [
      'Result volatility (pass/fail flips) 0.70 × 0.45',
      'Retries 0.50 × 0.25',
      'Environment instability 0.40 × 0.20',
      'Duration variance 0.10 × 0.10',
    ]) {
      expect(within(section).getByText(line)).toBeInTheDocument()
    }
    expect(within(section).getByText('Weights sum to 1.00')).toBeInTheDocument()
    // The bar's width is the score (the only inline value).
    expect((section.querySelector('[style]') as HTMLElement).style.width).toBe('62%')
    // It comes first; the existing details stay below it.
    const totalRuns = within(panel).getByText('Total Runs')
    expect(section.compareDocumentPosition(totalRuns) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('below the floor the section says insufficient, in words; pending says when; a failed one says nothing', () => {
    renderBody()
    fireEvent.click(screen.getByRole('button', { name: 'test_thin' }))
    let panel = screen.getByRole('complementary', { name: 'test_thin' })
    expect(within(panel).getByText('insufficient: 3 of 5 observations')).toBeInTheDocument()
    expect(within(panel).getByText(/A new test is not a stable test/)).toBeInTheDocument()
    expect(within(panel).queryByText(/Weights sum to/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'test_pending' }))
    panel = screen.getByRole('complementary', { name: 'test_pending' })
    expect(within(panel).getByText('Not scored yet: the score is computed nightly from the last 30 days.')).toBeInTheDocument()

    for (const fp of ['failed', 'garbage']) {
      fireEvent.click(screen.getByRole('button', { name: `test_${fp}` }))
      panel = screen.getByRole('complementary', { name: `test_${fp}` })
      expect(within(panel).queryByRole('region', { name: 'Flakiness score (4 signals)' })).toBeNull()
      expect(within(panel).getByText('Total Runs')).toBeInTheDocument()
    }
  })

  it('Sort › Flakiness score puts the scored rows first by score, unscored after in the server order; Impact restores it', () => {
    const entries = [
      scoreEntry('a', INSUFFICIENT),
      scoreEntry('b', { ...SCORED, score: 0.3 }),
      scoreEntry('c', { ...SCORED, score: 0.9 }),
      scoreEntry('d', null),
    ]
    renderBody(entries)
    const order = () => Array.from(document.querySelectorAll('tr[data-flaky-row]')).map((tr) => tr.getAttribute('data-flaky-row'))
    const sort = screen.getByLabelText('Sort')
    expect(sort).toHaveValue('impact')
    expect(order()).toEqual(['a', 'b', 'c', 'd'])
    fireEvent.change(sort, { target: { value: 'score' } })
    expect(order()).toEqual(['c', 'b', 'a', 'd'])
    // The recommendation filter applies to the sorted list.
    fireEvent.click(screen.getByRole('radio', { name: /^Investigate/ }))
    expect(order()).toEqual(['c', 'b', 'a', 'd'])
    fireEvent.change(sort, { target: { value: 'impact' } })
    expect(order()).toEqual(['a', 'b', 'c', 'd'])
  })
})

// W29a UIX-07: a quarantine holds a flaky test, it is not a failure: an outline
// in the flaky colour with the shield. Investigate / Healthy read the UI tokens.
describe('RecommendationPill — what each colour means (W29a UIX-07)', () => {
  it('draws Quarantine as an outline in the quarantined (flaky) colour, never the failed red', () => {
    const { container } = render(<RecommendationPill value="QUARANTINE" />)
    const pill = container.firstElementChild as HTMLElement
    expect(pill).toHaveTextContent('Quarantine')
    expect(pill.className).toContain('text-[var(--state-quarantined)]')
    expect(pill.className).toContain('border-[var(--state-quarantined-bd)]')
    expect(pill.className).toContain('bg-transparent')
    expect(pill.className).not.toContain('--status-failed')
    expect(pill.querySelector('svg')).not.toBeNull()
  })

  it('reads the warning and success UI tokens for Investigate and Healthy', () => {
    const investigate = render(<RecommendationPill value="INVESTIGATE" />).container.firstElementChild as HTMLElement
    expect(investigate.className).toContain('text-[var(--ui-warning-ink)]')
    expect(investigate.className).not.toContain('--status-')
    const healthy = render(<RecommendationPill value="HEALTHY" />).container.firstElementChild as HTMLElement
    expect(healthy.className).toContain('text-[var(--ui-success-ink)]')
    expect(healthy.className).not.toContain('--status-')
  })

  it('writes "Flaky Since" in the one date format (W29a UIX-10)', () => {
    renderBody([{ ...scoreEntry('since', SCORED), flaky_since: '2026-09-02T12:00:00Z' }])
    fireEvent.click(screen.getByRole('button', { name: 'test_since' }))
    const panel = screen.getByRole('complementary', { name: 'test_since' })
    const label = within(panel).getByText('Flaky Since')
    expect(label.nextElementSibling).toHaveTextContent(formatDay('2026-09-02T12:00:00Z'))
    expect(label.nextElementSibling).toHaveTextContent('Sep 2, 2026')
  })
})
