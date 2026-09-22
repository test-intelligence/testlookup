// @vitest-environment node
// (node, as contracts.test.ts does: under jsdom Vite refuses a `?raw` import
// from outside `frontend/` with "Denied ID".)
import { describe, expect, it } from 'vitest'
import {
  COMPACT_COUNT_FROM,
  DEFAULT_UNMEASURED_REASON,
  FLAKY_SOURCE,
  formatDelta,
  formatMetric,
  PASS_RATE_BASIS_LABELS,
  passRateBasisLabel,
  shouldShowUnknown,
  type FormattedMetric,
  type MetricKind,
} from './formatMetric'

describe('formatMetric — table', () => {
  const rows: Array<[string, number | null | undefined, MetricKind, FormattedMetric]> = [
    ['zero is a measured zero', 0, 'count', { text: '0', measured: true }],
    ['thousands separator', 3960, 'count', { text: '3,960', measured: true }],
    ['just under compact', 999_999, 'count', { text: '999,999', measured: true }],
    ['compact from one million', 1_000_000, 'count', { text: '1M', measured: true, exact: '1,000,000' }],
    ['huge count, exact kept', 1_234_567, 'count', { text: '1.23M', measured: true, exact: '1,234,567' }],
    ['billions', 2_500_000_000, 'count', { text: '2.5B', measured: true, exact: '2,500,000,000' }],
    ['pass rate one decimal', 87.25, 'percent', { text: '87.3%', measured: true }],
    ['pass rate measured zero', 0, 'percent', { text: '0.0%', measured: true }],
    ['pass rate 100', 100, 'percent', { text: '100.0%', measured: true }],
    ['sub-second duration is exact', 850, 'duration', { text: '850ms', measured: true }],
    ['seconds', 12_500, 'duration', { text: '12.5s', measured: true, exact: '12,500 ms' }],
    ['minutes', 125_000, 'duration', { text: '2m 5s', measured: true, exact: '125,000 ms' }],
    ['hours', 7_620_000, 'duration', { text: '2h 7m', measured: true, exact: '7,620,000 ms' }],
    ['null count', null, 'count', { text: '—', measured: false, reason: DEFAULT_UNMEASURED_REASON }],
    ['undefined percent', undefined, 'percent', { text: '—', measured: false, reason: DEFAULT_UNMEASURED_REASON }],
    ['NaN duration', Number.NaN, 'duration', { text: '—', measured: false, reason: DEFAULT_UNMEASURED_REASON }],
    ['Infinity', Number.POSITIVE_INFINITY, 'count', { text: '—', measured: false, reason: DEFAULT_UNMEASURED_REASON }],
    ['negative is not a value', -3, 'count', { text: '—', measured: false, reason: DEFAULT_UNMEASURED_REASON }],
  ]

  it.each(rows)('%s', (_name, value, kind, expected) => {
    expect(formatMetric(value, kind)).toEqual(expected)
  })

  it('carries the caller reason for an unmeasured value, and never renders 0 or 0%', () => {
    for (const kind of ['count', 'percent', 'duration'] as const) {
      const out = formatMetric(null, kind, { reason: 'No runs in this window' })
      expect(out).toEqual({ text: '—', measured: false, reason: 'No runs in this window' })
      expect(out.text).not.toMatch(/^0/)
    }
  })

  it('a blank reason falls back to the default rather than an empty hover', () => {
    expect(formatMetric(null, 'count', { reason: '  ' }).reason).toBe(DEFAULT_UNMEASURED_REASON)
  })

  it('the compact threshold is one million', () => {
    expect(COMPACT_COUNT_FROM).toBe(1_000_000)
  })
})

describe('pass-rate basis label', () => {
  it('labels both published bases', () => {
    expect(passRateBasisLabel('executions')).toBe('of executions')
    expect(passRateBasisLabel('unique_tests')).toBe('of unique tests')
  })

  it('claims no basis it was not told', () => {
    expect(passRateBasisLabel(null)).toBeNull()
    expect(passRateBasisLabel(undefined)).toBeNull()
    expect(passRateBasisLabel('')).toBeNull()
    expect(passRateBasisLabel('toString')).toBeNull()
    expect(passRateBasisLabel('per_run')).toBeNull()
  })
})

/**
 * The frontend mirror of `backend/tests/regression/test_pass_rate_basis_is_published.py`.
 *
 * That guard makes sure every basis the SERVICE computes reaches the API. This
 * one closes the other end: every basis the API may publish must have a label
 * here, or the strip would show a pass rate without its population — the
 * exact F-067 ambiguity. The published set is read from the shared contract
 * (`contracts/viz/README.md`, C2 `pass_rate_basis`), which the backend's
 * Pydantic model is held to by `test_viz_contracts.py`. Read through
 * `import.meta.glob(?raw)` (no node types in this tsconfig; Vite refuses a
 * `.py` source as "Denied ID"), and fail-closed when nothing is found.
 */
const CONTRACT_README = Object.values(
  import.meta.glob('../../../contracts/viz/README.md', { query: '?raw', import: 'default', eager: true }) as Record<
    string,
    string
  >,
)[0]

function publishedBases(): string[] {
  const row = (CONTRACT_README ?? '').split('\n').find((line) => line.startsWith('| `pass_rate_basis`'))
  if (!row) return []
  return [...row.matchAll(/`"([a-z_]+)"`/g)].map((m) => m[1])
}

describe('basis label regression (mirrors test_pass_rate_basis_is_published.py)', () => {
  it('found the contract row (fail closed)', () => {
    expect(CONTRACT_README, 'contracts/viz/README.md was not found three levels up').toBeTypeOf('string')
    expect(publishedBases().length, 'no pass_rate_basis values parsed from the C2 table').toBeGreaterThanOrEqual(2)
  })

  it('labels every basis the contract publishes', () => {
    const unlabelled = publishedBases().filter((basis) => passRateBasisLabel(basis) === null)
    expect(unlabelled, 'a basis the API may publish has no frontend label').toEqual([])
  })

  it('labels nothing the contract does not publish', () => {
    const published = new Set(publishedBases())
    expect(Object.keys(PASS_RATE_BASIS_LABELS).filter((basis) => !published.has(basis))).toEqual([])
  })
})

describe('Flaky and Unknown', () => {
  it('states the Flaky source, including that release does not scope it', () => {
    expect(FLAKY_SOURCE).toMatch(/last 10 runs/)
    expect(FLAKY_SOURCE).toMatch(/not filtered by release/)
  })

  it('shows Unknown only when it is a measured non-zero', () => {
    expect(shouldShowUnknown(3)).toBe(true)
    expect(shouldShowUnknown(0)).toBe(false)
    expect(shouldShowUnknown(null)).toBe(false)
    expect(shouldShowUnknown(undefined)).toBe(false)
  })
})

describe('formatDelta', () => {
  it('is null unless the previous period is comparable', () => {
    expect(formatDelta(4.2, undefined)).toBeNull()
    expect(formatDelta(4.2, false)).toBeNull()
    expect(formatDelta(null, true)).toBeNull()
    expect(formatDelta(Number.NaN, true)).toBeNull()
  })

  it('formats a comparable delta with direction', () => {
    expect(formatDelta(4.25, true)).toEqual({ direction: 'up', text: '4.3% vs previous period' })
    expect(formatDelta(-2, true)).toEqual({ direction: 'down', text: '2.0% vs previous period' })
    expect(formatDelta(0, true)).toEqual({ direction: 'flat', text: '0.0% vs previous period' })
  })
})
