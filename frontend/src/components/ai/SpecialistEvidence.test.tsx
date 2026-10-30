/**
 * W23d AGT-14 / AGT-15 — the decision report's `Specialist evidence` row
 * (contract §4.4 G-D): nothing without findings; the contract stage's drift
 * sentence verbatim (field path mono) and "+ N more"; the log stage's metric
 * series as a `MetricSpark` with its sentence; a non-`ok` outcome as its
 * sentence and no spark.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import SpecialistEvidence from './SpecialistEvidence'
import { contractRows, formatMetric, logMetricRows, metricCaption, sentenceParts } from './specialistEvidenceModel'
import type { ContractFindings, LogFindings } from '@/types/specialistEvidence'

const DRIFT = 'field `price` missing from GET /api/products/{id}; drift since build 41'
const ROSE = 'p95 latency for checkout rose 3.1× in the failure window'
const OFFLINE = 'Prometheus lookup disabled: offline mode'

function violation(field: string, sentence?: string) {
  return {
    violation_type: 'schema_drift', severity: 'critical', field_path: field, expected: 'present', actual: 'absent',
    method: 'GET', endpoint: '/api/products/{id}', baseline_build: '41',
    sentence: sentence ?? `field \`${field}\` missing from GET /api/products/{id}; drift since build 41`,
  }
}

function contract(over: Record<string, unknown> = {}): ContractFindings {
  return {
    status: 'complete',
    violations: [violation('price', DRIFT)],
    violation_count: 1,
    critical_count: 1,
    drift_count: 1,
    endpoints_checked: ['/api/products/{id}', '/api/cart'],
    summary: 'SCHEMA DRIFT DETECTED: 1 field changed since the previous runs.',
    ...over,
  } as unknown as ContractFindings
}

/** 40 points: ~0.20 s before the window, ~0.62 s inside it (the fake Prometheus's shape). */
function risingPoints(): Array<[number, number | null]> {
  const start = Date.parse('2026-10-30T09:00:00Z') / 1000
  return Array.from({ length: 40 }, (_, i) => [start + i * 30, i < 26 ? 0.2 : 0.62] as [number, number])
}

function log(over: Record<string, unknown> = {}): LogFindings {
  return {
    status: 'complete',
    log_summary: 'checkout: 3 error spans in the failure window; log rate 4.2× its baseline.',
    app_metrics: {
      outcome: 'ok',
      sentence: ROSE,
      service: 'checkout',
      series: [{
        template_id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds', points: risingPoints(),
        baseline_mean: 0.2, window_mean: 0.62, window_max: 0.64, ratio: 3.1, sentence: ROSE,
      }],
    },
    ...over,
  } as unknown as LogFindings
}

const row = () => document.querySelector('[data-specialist-evidence]')

describe('SpecialistEvidence', () => {
  it('renders nothing when the report carries neither finding (every report before W23d)', () => {
    const { container } = render(<SpecialistEvidence contractFindings={null} logFindings={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('the API contract card: the summary, the drift sentence verbatim with the field path in mono, endpoints checked', () => {
    render(<SpecialistEvidence contractFindings={contract()} />)
    const card = row()?.querySelector('[data-contract-findings]') as HTMLElement
    expect(within(card).getByText('API contract · 1')).toBeInTheDocument()
    expect(within(card).getByText(/SCHEMA DRIFT DETECTED/)).toBeInTheDocument()
    const item = card.querySelector('[data-contract-violation]') as HTMLElement
    expect(item.textContent).toBe('field price missing from GET /api/products/{id}; drift since build 41')
    expect(within(item).getByText('price').tagName).toBe('CODE')
    expect(within(card).getByText('2 endpoints checked')).toBeInTheDocument()
    expect(row()?.querySelector('[data-log-findings]')).toBeNull()
  })

  it('the summary drops the violation sentences the stage appended to it (each is said once, in the list)', () => {
    const lead = 'CRITICAL CONTRACT VIOLATIONS: 1 required field missing across 1 endpoint.'
    render(<SpecialistEvidence contractFindings={contract({ summary: `${lead} ${DRIFT}` })} />)
    expect(document.querySelector('[data-contract-summary]')?.textContent).toBe(lead)
    expect(screen.getAllByText('price')).toHaveLength(1)
  })

  it('lists five violations, then "+ 2 more"', () => {
    const fields = ['price', 'name', 'sku', 'stock', 'currency', 'tax', 'weight']
    render(<SpecialistEvidence contractFindings={contract({ violations: fields.map(f => violation(f)), violation_count: 7 })} />)
    expect(document.querySelectorAll('[data-contract-violation]')).toHaveLength(5)
    expect(screen.getByText('+ 2 more')).toBeInTheDocument()
  })

  it('not_enough_evidence: the summary only', () => {
    const summary = 'No HTTP exchanges were captured for these failures; enable capture in the reporter to validate API contracts.'
    render(<SpecialistEvidence contractFindings={contract({ status: 'not_enough_evidence', violations: [], violation_count: 0, endpoints_checked: [], summary })} />)
    expect(screen.getByText(summary)).toBeInTheDocument()
    expect(document.querySelectorAll('[data-contract-violation]')).toHaveLength(0)
  })

  it('the Log intelligence card: a rose series is a MetricSpark (warn tone) with its sentence', () => {
    render(<SpecialistEvidence logFindings={log()} />)
    const card = row()?.querySelector('[data-log-findings]') as HTMLElement
    expect(within(card).getByText('Application metrics')).toBeInTheDocument()
    const spark = within(card).getByRole('img')
    expect(spark.getAttribute('aria-label')).toMatch(/^p95 latency for checkout: 40 points, latest 620 ms/)
    expect(within(card).getByText(ROSE)).toBeInTheDocument()
    expect(within(card).getByText('620 ms vs 200 ms before')).toBeInTheDocument()
    expect(within(card).getByText(/3 error spans/)).toHaveAttribute('title', log().log_summary)
  })

  it('an offline_mode outcome: its sentence, and no spark', () => {
    render(<SpecialistEvidence logFindings={log({ app_metrics: { outcome: 'offline_mode', sentence: OFFLINE, service: 'checkout', series: [] } })} />)
    expect(screen.getByText(OFFLINE)).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
    expect(document.querySelector('[data-metric-spark]')).toBeNull()
  })

  it('both cards side by side when both findings are present', () => {
    render(<SpecialistEvidence contractFindings={contract()} logFindings={log()} />)
    expect(screen.getByRole('region', { name: 'Specialist evidence' })).toBeInTheDocument()
    expect(row()?.querySelectorAll('[data-contract-findings], [data-log-findings]')).toHaveLength(2)
  })
})

describe('specialistEvidenceModel', () => {
  it('sentenceParts: backtick pairs are code; unbalanced backticks stay plain text', () => {
    expect(sentenceParts('field `price` missing')).toEqual([
      { text: 'field ', code: false }, { text: 'price', code: true }, { text: ' missing', code: false },
    ])
    expect(sentenceParts('a `b')).toEqual([{ text: 'a `b', code: false }])
  })

  it('contractRows / logMetricRows: absent findings → null', () => {
    expect(contractRows(null)).toBeNull()
    expect(logMetricRows(undefined)).toBeNull()
  })

  it('logMetricRows: a steady series is neutral, a rose one warn; ≤ 4 series', () => {
    const series = (ratio: number, id: string) => ({ template_id: id, label: id, unit: 'count', points: [], baseline_mean: 1, window_mean: ratio, window_max: ratio, ratio, sentence: id })
    const view = logMetricRows(log({ app_metrics: { outcome: 'ok', sentence: '', service: 'checkout', series: [series(1.1, 'a'), series(1.5, 'b'), series(2, 'c'), series(3, 'd'), series(4, 'e')] } }))
    expect(view?.metricRows.map(r => r.tone)).toEqual(['neutral', 'warn', 'warn', 'warn'])
  })

  it('formatMetric: each unit, and "—" for nothing', () => {
    expect(formatMetric(0.62, 'seconds')).toBe('620 ms')
    expect(formatMetric(1.25, 'seconds')).toBe('1.25 s')
    expect(formatMetric(512 * 1024 * 1024, 'bytes')).toBe('512.0 MB')
    expect(formatMetric(0.35, 'ratio')).toBe('35.0%')
    expect(formatMetric(0.4217, 'per_second')).toBe('0.42/s')
    expect(formatMetric(1284, 'count')).toBe('1,284')
    expect(formatMetric(null, 'seconds')).toBe('—')
    expect(metricCaption(0.62, null, 'seconds')).toBe('620 ms in the failure window')
    expect(metricCaption(null, 0.2, 'seconds')).toBeNull()
  })
})
