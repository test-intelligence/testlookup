import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import type { ReportSummary } from '@/hooks/useReportMetrics'
import { GALLERY_CASES } from '@/pages/dev/reportContextFixtures'
import MetricsStrip from './MetricsStrip'
import { BLOCK_UNAVAILABLE_REASON, buildStripMetrics, metricsFromSummary, type ReportMetricsInput } from './metricsModel'

const caseOf = (id: string) => {
  const found = GALLERY_CASES.find((c) => c.id === id)
  if (!found) throw new Error(id)
  return found
}

const tile = (id: string) => document.querySelector(`[data-metric="${id}"]`) as HTMLElement
const value = (id: string) => within(tile(id)).getByText((_, el) => el?.tagName === 'P' && /text-3xl/.test(el.className)).textContent

describe('MetricsStrip', () => {
  it('shows the metric set in visual order; Unknown only when non-zero', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} />)
    expect(Array.from(document.querySelectorAll('[data-metric]')).map((el) => el.getAttribute('data-metric'))).toEqual([
      'runs',
      'total_tests',
      'passed',
      'failed',
      'broken',
      'skipped',
      'flaky',
      'pass_rate',
      'total_duration',
      'avg_duration',
    ])
  })

  it('formats counts, pass rate and durations; states the basis and the Flaky source', () => {
    render(<MetricsStrip input={caseOf('filtered').metrics} />)
    expect(value('total_tests')).toBe('412')
    expect(value('passed')).toBe('3,612')
    expect(value('pass_rate')).toBe('91.2%')
    expect(within(tile('pass_rate')).getByText('of executions')).toBeInTheDocument()
    expect(within(tile('flaky')).getByText(/not filtered by release/)).toBeInTheDocument()
    expect(value('total_duration')).toBe('2h 7m')
    expect(within(tile('total_duration')).getByText('Exact value: 7,620,000 ms')).toBeVisible()
    expect(value('unknown')).toBe('3')
  })

  // Fix round B (a11y M2): the reason for "—" and the exact value behind an
  // abbreviation are VISIBLE text under the value (not a hover-only `title`,
  // not a screen-reader-only description), so a keyboard or magnifier user
  // reads them without a tooltip (WCAG 1.4.13, 2.1.1).
  it('an unmeasured metric is "—" with its reason as visible text — never 0 or 0%, never title-only', () => {
    render(<MetricsStrip input={caseOf('unmeasured').metrics} />)
    for (const id of ['runs', 'total_tests', 'pass_rate', 'avg_duration']) {
      expect(value(id)).toBe('—')
      expect(tile(id)).toHaveAttribute('data-measured', 'false')
      const hint = within(tile(id)).getByText('Not measured: No runs match this scope in the window.')
      expect(hint).toBeVisible()
      expect(hint.closest('.sr-only')).toBeNull()
      expect(tile(id)).not.toHaveAttribute('title')
    }
    expect(document.body.textContent).not.toMatch(/0\.0%/)
  })

  it('a huge count is compact, with the exact value visible under it', () => {
    render(<MetricsStrip input={caseOf('huge-counts').metrics} />)
    expect(value('runs')).toBe('1.23M')
    const exact = within(tile('runs')).getByText('Exact value: 1,234,567')
    expect(exact.closest('.sr-only')).toBeNull()
    expect(tile('runs')).not.toHaveAttribute('title')
  })

  it('each hint is exposed ONCE (group content, no aria-describedby echo); no tile is a bare tab stop', () => {
    render(<MetricsStrip input={caseOf('huge-counts').metrics} />)
    expect(document.querySelectorAll('[data-metric][aria-describedby]')).toHaveLength(0)
    expect(document.querySelectorAll('[data-metric][tabindex]')).toHaveLength(0)
    expect(within(tile('runs')).getAllByText(/Exact value: 1,234,567/)).toHaveLength(1)
  })

  it('no empty element under a tile without a note or hint', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} />)
    const empty = Array.from(document.querySelectorAll('[data-metrics-strip] p, [data-metric] > div')).filter(
      (el) => (el.textContent ?? '').trim() === '' && !el.closest('[data-metric-loading]'),
    )
    expect(empty.map((el) => el.outerHTML.slice(0, 80))).toEqual([])
  })

  it('columns come from the strip width (auto-fill, min 13rem), not viewport breakpoints (a11y M7)', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} />)
    const grid = document.querySelector('[data-metrics-grid]') as HTMLElement
    expect(grid.className).toContain('grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))]')
    expect(grid.className).not.toMatch(/\b(sm|md|lg|xl):grid-cols-/)
    // The value never wraps inside its tile.
    expect(within(tile('runs')).getByText('143').className).toContain('whitespace-nowrap')
  })

  it('a metric not applicable to a page is omitted, not dashed', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} include={['runs', 'pass_rate']} />)
    expect(document.querySelectorAll('[data-metric]')).toHaveLength(2)
  })

  it('draws a delta only when the previous period is comparable', () => {
    const input = caseOf('unfiltered').metrics
    const { rerender } = render(<MetricsStrip input={input} />)
    expect(within(tile('pass_rate')).getByText('1.8 pp vs previous period')).toBeInTheDocument()
    rerender(<MetricsStrip input={{ ...input, comparable: false }} />)
    expect(within(tile('pass_rate')).queryByText(/vs prev/)).toBeNull()
  })

  // Fix round B (m2): a pass rate moves in percentage POINTS; counts keep the relative change.
  it('pass rate delta in pp, counts in relative %, direction and judgement in words (a11y M1)', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} />)
    const passRate = tile('pass_rate').querySelector('[data-metric-trend]') as HTMLElement
    expect(passRate.textContent).toBe('Up1.8 pp vs previous period(better)')
    const failed = tile('failed').querySelector('[data-metric-trend]') as HTMLElement
    expect(failed.textContent).toBe('Down12.5% vs previous period(better)')
    // The icon is decoration and never shrinks out of its row.
    expect(failed.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(failed.querySelector('svg')?.getAttribute('class')).toContain('shrink-0')
  })

  it('a previous period of 0 says "new" instead of drawing nothing (m2)', () => {
    const input: ReportMetricsInput = {
      values: { failed: 4, pass_rate: 50 },
      trends: { failed: null, pass_rate: 50 },
      trendNotes: { failed: 'New: 0 in the previous period' },
      comparable: true,
    }
    render(<MetricsStrip input={input} include={['failed', 'pass_rate']} />)
    expect(within(tile('failed')).getByText('New: 0 in the previous period')).toBeVisible()
    expect(tile('failed').querySelector('[data-trend-judgement]')).toBeNull()
    expect(within(tile('pass_rate')).getByText('50.0 pp vs previous period')).toBeInTheDocument()
  })

  it('status metrics carry a status icon', () => {
    render(<MetricsStrip input={caseOf('unfiltered').metrics} />)
    for (const id of ['passed', 'failed', 'broken', 'skipped', 'flaky']) {
      expect(tile(id).querySelector('svg.lucide')).not.toBeNull()
    }
  })

  it('loading shows placeholders, not "—", and no basis note about a response not yet arrived (m7)', () => {
    render(<MetricsStrip input={{ values: {} }} loading />)
    expect(screen.getByRole('region', { name: 'Report metrics' })).toHaveAttribute('aria-busy', 'true')
    expect(document.body.textContent).not.toContain('—')
    expect(document.body.textContent).not.toContain('basis not stated')
    expect(document.querySelector('[data-metric-note]')).toBeNull()
    expect(screen.getByText('Loading report metrics')).toHaveClass('sr-only')
    expect(document.querySelectorAll('[data-metric-loading]')).toHaveLength(10)
    // axe aria-prohibited-attr: no aria-label on a role-less placeholder
    // (MetricCard's own loading div has exactly that defect).
    for (const el of document.querySelectorAll('[data-metrics-strip] [aria-label]')) {
      expect(el.getAttribute('role'), el.outerHTML.slice(0, 80)).not.toBeNull()
    }
  })
})

describe('metricsFromSummary', () => {
  const meta = GALLERY_CASES.find((c) => c.id === 'filtered')?.meta as EnvelopeMeta
  const summary: ReportSummary = {
    total_executions_7d: { value: 412 },
    avg_pass_rate_7d: { value: 91.2, basis: 'executions' },
    flaky_test_count: { value: 5 },
    avg_duration_ms: { value: 1200 },
  }

  it('maps one response: runs from meta.totals, the rest from the KPI fields', () => {
    const input = metricsFromSummary(summary, meta)
    expect(input.values).toEqual({ runs: 18, total_tests: 412, flaky: 5, pass_rate: 91.2, avg_duration: 1200 })
    expect(input.passRateBasis).toBe('executions')
    expect(input.comparable).toBe(false)
  })

  it('without a report_metrics block, the fields only it carries are "—" with that reason, never 0', () => {
    const strip = buildStripMetrics(metricsFromSummary(summary, meta))
    for (const id of ['passed', 'failed', 'broken', 'skipped', 'total_duration']) {
      const m = strip.find((s) => s.id === id)
      expect(m?.formatted).toEqual({ text: '—', measured: false, reason: BLOCK_UNAVAILABLE_REASON })
    }
  })

  it('measured:false turns the service zeros into "—" with meta.reason (absence is not health)', () => {
    const empty: ReportSummary = { ...summary, total_executions_7d: { value: 0 }, avg_pass_rate_7d: { value: 0 } }
    const unmeasured = { ...meta, measured: false, reason: 'No runs match this scope in the window.' }
    const strip = buildStripMetrics(metricsFromSummary(empty, unmeasured))
    for (const id of ['runs', 'total_tests', 'pass_rate']) {
      expect(strip.find((s) => s.id === id)?.formatted).toEqual({
        text: '—',
        measured: false,
        reason: 'No runs match this scope in the window.',
      })
    }
  })

  it('the basis falls back to the KPI field when meta is absent, and is never guessed', () => {
    expect(metricsFromSummary(summary, null).passRateBasis).toBe('executions')
    const noBasis: ReportSummary = { ...summary, avg_pass_rate_7d: { value: 91.2 } }
    const strip = buildStripMetrics(metricsFromSummary(noBasis, null) as ReportMetricsInput)
    expect(strip.find((s) => s.id === 'pass_rate')?.note).toBe('basis not stated')
  })
})
