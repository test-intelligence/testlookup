/**
 * The strip reads `/metrics/summary`'s `report_metrics` block (contract C6):
 * real per-status counts and durations instead of "not reported" placeholders,
 * a delta only when the previous period is `comparable`, the block's own
 * reason on anything it reports as `null` — never 0.
 */
import { describe, expect, it } from 'vitest'
import { render, within } from '@testing-library/react'
import type { ReportSummary } from '@/hooks/useReportMetrics'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import MetricsStrip from './MetricsStrip'
import {
  BLOCK_UNAVAILABLE_REASON,
  buildStripMetrics,
  metricsFromSummary,
  readReportMetrics,
  TREND_NOTES,
} from './metricsModel'

const period = (over: Record<string, unknown> = {}) => ({
  runs: 5,
  total_tests: 64,
  passed: 50,
  failed: 5,
  broken: 2,
  skipped: 3,
  unknown: 4,
  pass_rate: 87.7,
  total_duration_ms: 600_000,
  avg_duration_ms: 120_000,
  duration_runs: 5,
  reasons: {},
  window: { from: '2026-09-14', to: '2026-09-21', days: 7 },
  ...over,
})

const block = (previous: Record<string, unknown> = {}, current: Record<string, unknown> = {}) => ({
  schema_version: 1,
  pass_rate_basis: 'executions',
  current: period(current),
  previous: {
    ...period({ runs: 4, total_tests: 40, passed: 40, failed: 10, broken: 0, pass_rate: 80, total_duration_ms: 480_000, ...previous }),
    comparable: true,
    reason: null,
    reason_code: null,
    ...previous,
  },
})

const summary = (reportMetrics: unknown): ReportSummary => ({
  total_executions_7d: { value: 64 },
  avg_pass_rate_7d: { value: 87.7, basis: 'executions' },
  flaky_test_count: { value: 2 },
  avg_duration_ms: { value: 120_000 },
  report_metrics: reportMetrics,
})

const META = { measured: true, reason: null, pass_rate_basis: 'executions', totals: { matched_runs: 5 } } as unknown as EnvelopeMeta

const tile = (id: string) => document.querySelector(`[data-metric="${id}"]`) as HTMLElement
const value = (id: string) =>
  within(tile(id)).getByText((_, el) => el?.tagName === 'P' && /text-3xl/.test(el.className)).textContent

describe('metricsFromSummary with a report_metrics block', () => {
  it('every tile comes from the block; nothing is a placeholder', () => {
    const input = metricsFromSummary(summary(block()), META)
    expect(input.values).toEqual({
      runs: 5,
      total_tests: 64,
      passed: 50,
      failed: 5,
      broken: 2,
      skipped: 3,
      unknown: 4,
      pass_rate: 87.7,
      total_duration: 600_000,
      avg_duration: 120_000,
      flaky: 2,
    })
    expect(input.reasons).toEqual({})
    expect(JSON.stringify(input)).not.toContain(BLOCK_UNAVAILABLE_REASON)
  })

  it('renders the counts, the durations and Unknown (non-zero)', () => {
    render(<MetricsStrip input={metricsFromSummary(summary(block()), META)} />)
    expect(value('passed')).toBe('50')
    expect(value('failed')).toBe('5')
    expect(value('broken')).toBe('2')
    expect(value('skipped')).toBe('3')
    expect(value('unknown')).toBe('4')
    expect(value('total_duration')).toBe('10m 0s')
    expect(within(tile('total_duration')).getByText('Exact value: 600,000 ms')).toBeVisible()
    expect(document.querySelector('[data-comparison-reason]')).toBeNull()
  })

  it('draws deltas only when the previous period is comparable', () => {
    const comparable = metricsFromSummary(summary(block()), META)
    expect(comparable.comparable).toBe(true)
    // failed 10 -> 5 is -50 %; passed 40 -> 50 is +25 %.
    expect(comparable.trends?.failed).toBe(-50)
    expect(comparable.trends?.passed).toBe(25)
    const strip = buildStripMetrics(comparable)
    expect(strip.find((m) => m.id === 'failed')?.delta).toEqual({ direction: 'down', text: '50.0% vs previous period' })
    // Pass rate 80 -> 87.7 is +7.7 percentage POINTS, not the relative +9.6 %.
    expect(comparable.trends?.pass_rate).toBeCloseTo(7.7, 5)
    expect(strip.find((m) => m.id === 'pass_rate')?.delta).toEqual({ direction: 'up', text: '7.7 pp vs previous period' })
    expect(strip.find((m) => m.id === 'pass_rate')?.trend).toBe(7.7)

    const partial = metricsFromSummary(
      summary(block({ comparable: false, reason_code: 'partial_window', reason: 'The history starts on 2026-09-10.' })),
      META,
    )
    expect(partial.comparable).toBe(false)
    expect(buildStripMetrics(partial).every((m) => m.delta === null && m.trend === null)).toBe(true)
    render(<MetricsStrip input={partial} />)
    expect(document.body.textContent).not.toMatch(/vs prev/)
    expect(document.querySelector('[data-comparison-reason]')?.textContent).toContain(
      'The history starts on 2026-09-10.',
    )
  })

  it('no NUMBER from a previous value of 0 or null — but the tile says why, never silently blank (m2)', () => {
    const input = metricsFromSummary(
      summary(block({ broken: 0, total_duration_ms: null, avg_duration_ms: null, duration_runs: 0,
        reasons: { total_duration_ms: 'none', avg_duration_ms: 'none' } })),
      META,
    )
    expect(input.trends?.broken).toBeNull()
    expect(input.trends?.total_duration).toBeNull()
    const strip = buildStripMetrics(input)
    // broken 0 -> 2: new, not "+Infinity %".
    expect(strip.find((m) => m.id === 'broken')?.delta).toEqual({ direction: 'none', text: TREND_NOTES.newFromZero })
    expect(strip.find((m) => m.id === 'total_duration')?.delta).toEqual({
      direction: 'none',
      text: TREND_NOTES.previousUnmeasured,
    })
    render(<MetricsStrip input={input} />)
    expect(within(tile('broken')).getByText('New: 0 in the previous period')).toBeVisible()
  })

  it('0 in both periods is "No change", not "new" (m2)', () => {
    const input = metricsFromSummary(summary(block({ broken: 0 }, { broken: 0 })), META)
    expect(input.trends?.broken).toBe(0)
    expect(buildStripMetrics(input).find((m) => m.id === 'broken')?.delta).toEqual({
      direction: 'flat',
      text: 'vs previous period',
    })
  })

  it('a null in the block is "—" with the block reason — never 0', () => {
    const why = 'No run in this scope recorded a duration.'
    const input = metricsFromSummary(
      summary(block({}, { total_duration_ms: null, avg_duration_ms: null, duration_runs: 0,
        reasons: { total_duration_ms: why, avg_duration_ms: why } })),
      META,
    )
    render(<MetricsStrip input={input} />)
    for (const id of ['total_duration', 'avg_duration']) {
      expect(value(id)).toBe('—')
      expect(within(tile(id)).getByText(`Not measured: ${why}`)).toBeVisible()
    }
  })

  it('an invalid block is not trusted: the tiles it would fill say so, and nothing is compared', () => {
    const invalid = block({}, { pass_rate: 120 })
    expect(readReportMetrics(summary(invalid))).toBeNull()
    const input = metricsFromSummary(summary(invalid), META)
    expect(input.comparable).toBe(false)
    expect(input.reasons?.passed).toBe(BLOCK_UNAVAILABLE_REASON)
    expect(input.values.passed).toBeUndefined()
  })

  it('a denied scope ({meta} alone, measured false) is "—" with meta.reason everywhere it is windowed', () => {
    const reason = 'No project in this scope is readable by the caller.'
    const meta = { ...META, measured: false, reason, totals: { matched_runs: 0 } } as unknown as EnvelopeMeta
    const strip = buildStripMetrics(metricsFromSummary({ meta } as ReportSummary, meta))
    for (const id of ['runs', 'total_tests', 'passed', 'failed', 'pass_rate', 'total_duration', 'avg_duration']) {
      expect(strip.find((m) => m.id === id)?.formatted).toEqual({ text: '—', measured: false, reason })
    }
  })
})
