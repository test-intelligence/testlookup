/**
 * W12 SRCH-05 — the three search-usage tiles' values. Pure.
 *
 * Absence is not health: a day with no search reads "Not measured yet", never
 * "0 %" (`measured: false` from `GET /search/usage`).
 */
import { formatNumber } from '@/utils/formatters'
import type { SearchUsage } from '@/types/search'

export const USAGE_TITLES = { searches: 'Searches today', zeroRate: 'Zero-result rate', p95: 'p95 latency' } as const
export const NOT_MEASURED_CAPTION = 'Not measured yet: no search ran today.'
export const USAGE_LOAD_ERROR = 'Could not load search usage.'

export interface UsageTile {
  key: 'searches' | 'zeroRate' | 'p95'
  title: string
  value: string
  caption: string
}

const DASH = '—'

function ms(value: number | null | undefined): string | null {
  return typeof value === 'number' && Number.isFinite(value) ? `${formatNumber(Math.round(value))} ms` : null
}

/** The three tiles, in order: searches today, the zero-result rate, the p95 latency. */
export function tileValues(usage: SearchUsage | null | undefined, failed = false): UsageTile[] {
  const blank = (caption: string): UsageTile[] => [
    { key: 'searches', title: USAGE_TITLES.searches, value: DASH, caption },
    { key: 'zeroRate', title: USAGE_TITLES.zeroRate, value: DASH, caption },
    { key: 'p95', title: USAGE_TITLES.p95, value: DASH, caption },
  ]
  if (failed) return blank(USAGE_LOAD_ERROR)
  if (!usage || !usage.measured || !(usage.searches > 0)) return blank(NOT_MEASURED_CAPTION)
  const rate = usage.zero_result_rate
  const rateValue = typeof rate === 'number' && Number.isFinite(rate) ? `${Math.round(rate * 100)} %` : DASH
  const p50 = ms(usage.p50_latency_ms)
  return [
    {
      key: 'searches',
      title: USAGE_TITLES.searches,
      value: formatNumber(usage.searches),
      caption: `UTC day · kept ${usage.retention_days} days`,
    },
    {
      key: 'zeroRate',
      title: USAGE_TITLES.zeroRate,
      value: rateValue,
      caption: `${formatNumber(usage.zero_result_searches)} of ${formatNumber(usage.searches)} found nothing`,
    },
    {
      key: 'p95',
      title: USAGE_TITLES.p95,
      value: ms(usage.p95_latency_ms) ?? DASH,
      caption: p50 ? `p50 ${p50}` : 'p50 —',
    },
  ]
}
