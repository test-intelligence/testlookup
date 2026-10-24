import { describe, expect, it } from 'vitest'
import { NOT_MEASURED_CAPTION, USAGE_LOAD_ERROR, tileValues } from './usageModel'
import type { SearchUsage } from '@/types/search'

const base: SearchUsage = {
  project_id: 'p1', days: 1, since: '2026-10-24T00:00:00Z', measured: true, searches: 3,
  zero_result_searches: 1, zero_result_rate: 1 / 3, p50_latency_ms: 40, p95_latency_ms: 120.4,
  fallback_searches: 0, retention_days: 30,
}

describe('tileValues (W12 SRCH-05)', () => {
  it('a measured day: searches, the zero-result rate, the p95 with its p50', () => {
    expect(tileValues(base).map((t) => [t.title, t.value, t.caption])).toEqual([
      ['Searches today', '3', 'UTC day · kept 30 days'],
      ['Zero-result rate', '33 %', '1 of 3 found nothing'],
      ['p95 latency', '120 ms', 'p50 40 ms'],
    ])
  })

  it('a day with no search is "not measured", never 0 %', () => {
    const tiles = tileValues({ ...base, measured: false, searches: 0, zero_result_searches: 0, zero_result_rate: null, p50_latency_ms: null, p95_latency_ms: null })
    expect(tiles.map((t) => t.value)).toEqual(['—', '—', '—'])
    expect(tiles.every((t) => t.caption === NOT_MEASURED_CAPTION)).toBe(true)
    expect(tiles.some((t) => t.value.includes('0 %'))).toBe(false)
    expect(tileValues(undefined).every((t) => t.caption === NOT_MEASURED_CAPTION)).toBe(true)
  })

  it('a refused read says it could not load', () => {
    expect(tileValues(undefined, true).every((t) => t.value === '—' && t.caption === USAGE_LOAD_ERROR)).toBe(true)
  })

  it('a measured zero is a real 0 %', () => {
    expect(tileValues({ ...base, zero_result_searches: 0, zero_result_rate: 0 })[1].value).toBe('0 %')
  })
})
