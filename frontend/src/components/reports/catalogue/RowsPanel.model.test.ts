import { describe, expect, it } from 'vitest'
import envelope from '../../../../../contracts/viz/fixtures/envelope/valid/filtered.json'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import {
  chartGrain,
  measureText,
  reconciliationNotice,
  ROWS_ACCESSORS,
  ROWS_PAGE_SIZE,
  rowsAnnouncement,
  rowsCountText,
  rowsHeading,
  rowsRequestParams,
  selectorsKey,
  validateRowsResponse,
  type RowsItem,
  type RowsResponse,
} from './RowsPanel.model'

const meta = envelope.payload as unknown as EnvelopeMeta
const item: RowsItem = {
  id: 'tc-1',
  test_name: 'test_checkout',
  test_fingerprint: 'fp-1',
  suite: 'payments',
  status: 'failed',
  duration_ms: 1234,
  run_id: 'run-1',
  release: { id: 'rel-1', name: '2026.09' },
  created_at: '2026-09-12T10:00:00Z',
  failure_category: 'product_bug',
  error_line: 'AssertionError: 1 != 2',
}
const body = (over: Partial<RowsResponse> = {}): RowsResponse => ({
  items: [item],
  total: 1,
  page: 1,
  size: 50,
  pages: 1,
  reconciliation: { mark_field: 'y', measure: 'rows', value: 1 },
  meta,
  ...over,
})

describe('rowsRequestParams', () => {
  const chart = { metric: 'failures', groupBy: ['suite', 'status'] as const, topN: 20 }
  const scope = { project_id: 'p1', days: 30, suite_name: ['a', 'b'] }

  it('is nothing while the panel is closed or the scope unresolved', () => {
    expect(rowsRequestParams({ selectors: [], chart, scope }, 1)).toBeNull()
    expect(rowsRequestParams({ selectors: [{ dimension: 'suite', value: 'x' }], chart, scope: null }, 1)).toBeNull()
  })

  it('sends the scope, the chart’s spec, one bucket per selector and the page', () => {
    expect(
      rowsRequestParams(
        { selectors: [{ dimension: 'suite', value: 'payments' }, { dimension: 'status', value: 'failed' }], chart, scope },
        2.7,
      ),
    ).toEqual({
      project_id: 'p1',
      days: 30,
      suite_name: ['a', 'b'],
      metric: 'failures',
      group_by: ['suite', 'status'],
      top_n: 20,
      bucket_suite: 'payments',
      bucket_status: 'failed',
      page: 2,
      size: ROWS_PAGE_SIZE,
    })
  })

  it('a scope cannot override the chart’s metric, and no top_n is sent without one; page is at least 1', () => {
    const params = rowsRequestParams(
      { selectors: [{ dimension: 'test', value: 'fp' }], chart: { metric: 'failed', groupBy: ['test'] }, scope: { metric: 'executions' } },
      -3,
    )
    expect(params?.metric).toBe('failed')
    expect(params).not.toHaveProperty('top_n')
    expect(params?.page).toBe(1)
  })

  it('selectorsKey is stable and tells values apart', () => {
    expect(selectorsKey([{ dimension: 'suite', value: 'a~b' }])).toBe(selectorsKey([{ dimension: 'suite', value: 'a~b' }]))
    expect(selectorsKey([{ dimension: 'suite', value: 'a' }])).not.toBe(selectorsKey([{ dimension: 'suite', value: 'b' }]))
  })
})

describe('validateRowsResponse', () => {
  it('accepts the wire body (meta beside the page) and keeps its meta', () => {
    const checked = validateRowsResponse({ ...body(), meta })
    expect(checked.ok).toBe(true)
    if (checked.ok) expect(checked.value.meta).toEqual(meta)
    const noMeta = validateRowsResponse({ ...body(), meta: undefined })
    expect(noMeta.ok && noMeta.value.meta).toBeNull()
  })

  it('accepts nulls where the server sends them', () => {
    const nulls = { ...item, suite: null, duration_ms: null, release: null, failure_category: null, error_line: null }
    expect(validateRowsResponse(body({ items: [nulls] })).ok).toBe(true)
  })

  it.each([
    ['not an object', [] as unknown],
    ['items not an array', { ...body(), items: 'x' }],
    ['an item not an object', { ...body(), items: [3] }],
    ['an item without a string id', { ...body(), items: [{ ...item, id: 7 }] }],
    ['a suite that is a number', { ...body(), items: [{ ...item, suite: 5 }] }],
    ['a negative duration', { ...body(), items: [{ ...item, duration_ms: -1 }] }],
    ['an infinite duration', { ...body(), items: [{ ...item, duration_ms: Infinity }] }],
    ['a release without an id', { ...body(), items: [{ ...item, release: { name: 'x' } }] }],
    ['a fractional total', { ...body(), total: 1.5 }],
    ['a negative page', { ...body(), page: -1 }],
    ['a reconciliation field other than y/n', { ...body(), reconciliation: { mark_field: 'x', measure: 'rows', value: 1 } }],
    ['a reconciliation without a value', { ...body(), reconciliation: { mark_field: 'y', measure: 'rows' } }],
    ['a bad meta', { ...body(), meta: { schema_version: 0 } }],
  ])('refuses %s', (_why, input) => {
    expect(validateRowsResponse(input).ok).toBe(false)
  })
})

describe('words', () => {
  it('counts by the reconciliation’s measure', () => {
    expect(measureText('rows', 1)).toBe('1 execution')
    expect(measureText('distinct_tests', 12)).toBe('12 tests')
    expect(measureText('distinct_runs', 1200)).toBe('1,200 runs')
    // An unknown measure, or a prototype word, is a plain number.
    expect(measureText('constructor', 3)).toBe('3')
    expect(rowsCountText(body({ total: 42 }))).toBe('42 executions')
    expect(rowsCountText(body({ total: 42, reconciliation: { mark_field: 'y', measure: 'distinct_tests', value: 9 } }))).toBe(
      '42 executions (9 tests)',
    )
  })

  it('heading and announcement carry the title as text', () => {
    expect(rowsHeading('<b>payments</b>, failed')).toBe('Executions in <b>payments</b>, failed')
    expect(rowsAnnouncement('payments, failed', 1)).toBe('1 execution in payments, failed')
  })

  it('reads chart_grain only from a definitions object', () => {
    expect(chartGrain(null)).toBeNull()
    expect(chartGrain(meta)).toBeNull()
    expect(chartGrain({ ...meta, definitions: { chart_grain: 'run_aggregate' } } as EnvelopeMeta)).toBe('run_aggregate')
    expect(chartGrain({ ...meta, definitions: 'x' } as unknown as EnvelopeMeta)).toBeNull()
  })

  it('ROWS_ACCESSORS: empty means no execution at all', () => {
    expect(ROWS_ACCESSORS.isEmpty(body({ total: 0, items: [] }))).toBe(true)
    expect(ROWS_ACCESSORS.isEmpty(body())).toBe(false)
    expect(ROWS_ACCESSORS.shown(body())).toBe(1)
    expect(ROWS_ACCESSORS.meta(body())).toBe(meta)
  })
})

describe('reconciliationNotice (never a bare mismatch)', () => {
  const page = body({ reconciliation: { mark_field: 'y', measure: 'rows', value: 40 } })

  it('says nothing when it adds up, or when the host no longer knows what it drew', () => {
    expect(reconciliationNotice(page, { y: 40, n: 50, asOf: null })).toBeNull()
    expect(reconciliationNotice(page, null)).toBeNull()
    expect(reconciliationNotice(page, undefined)).toBeNull()
    expect(reconciliationNotice(page, { y: null, n: 50, asOf: null })).toBeNull()
  })

  it('compares the field the server names: a rate compares n, not y', () => {
    const rate = body({ reconciliation: { mark_field: 'n', measure: 'rows', value: 50 } })
    expect(reconciliationNotice(rate, { y: 96.4, n: 50, asOf: null })).toBeNull()
    expect(reconciliationNotice(rate, { y: 50, n: 48, asOf: null })).toMatch(/^50 executions now; the chart counted 48\./)
  })

  it('data moved since the chart loaded: names when the chart counted, in UTC', () => {
    expect(reconciliationNotice(page, { y: 42, n: 50, asOf: '2026-09-19T10:42:07.123Z' })).toBe(
      '40 executions now; the chart counted 42 at 2026-09-19 10:42:07 UTC. Results have changed since the chart loaded.',
    )
  })

  it('a run-aggregate chart: says the chart counted run totals', () => {
    const grain = { ...page, meta: { ...meta, definitions: { chart_grain: 'run_aggregate' } } as EnvelopeMeta }
    expect(reconciliationNotice(grain, { y: 45, n: 50, asOf: null })).toBe(
      'The chart counted 45 executions from run totals, which include runs whose per-test results have not arrived; 40 executions can be listed here.',
    )
  })
})
