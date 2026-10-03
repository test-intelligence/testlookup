/**
 * Wave 3 (FK0): the state model reads the `points` kind, and the four-kind
 * validator still refuses it, so a pre-Wave-3 reader is never handed one.
 */
import { describe, expect, it } from 'vitest'
import type { PointsChart } from '@/lib/viz/contracts'
import pointsFixture from '../../../../contracts/viz/fixtures/chart_series/valid/points.json'
import allExcluded from '../../../../contracts/viz/fixtures/chart_series/valid/points_all_excluded.json'
import truncatedMeta from '../../../../contracts/viz/fixtures/envelope/valid/truncated_on_both_axes.json'
import {
  CHART_RESPONSE_ACCESSORS,
  isChartSeriesEmpty,
  resolveChartState,
  seriesHasPoints,
  shownCount,
  validateAnyChartResponse,
  validateChartResponse,
  type AnyChartResponse,
} from './chartState'

const points = pointsFixture.payload as PointsChart
const none = allExcluded.payload as PointsChart

describe('points in the state model', () => {
  it('validateChartResponse keeps refusing points (kind_enum), exactly as before Wave 3', () => {
    const checked = validateChartResponse({ meta: null, series: points })
    expect(checked.ok).toBe(false)
    if (!checked.ok) expect(checked.errors.join(' ')).toMatch(/^series: kind_enum/)
  })

  it('validateAnyChartResponse accepts points, and still checks meta and series', () => {
    const checked = validateAnyChartResponse({ meta: null, series: points })
    expect(checked.ok).toBe(true)
    if (checked.ok) expect(checked.value.series).toBe(points)
    const bad = validateAnyChartResponse({ meta: { schema_version: 0 }, series: { ...points, points: [{ ...points.points[0], x: 0 }] } })
    expect(bad.ok).toBe(false)
    if (!bad.ok) {
      expect(bad.errors.some((e) => e.startsWith('meta: '))).toBe(true)
      expect(bad.errors.some((e) => e.startsWith('series: '))).toBe(true)
    }
    expect(validateAnyChartResponse([]).ok).toBe(false)
  })

  it('a points chart with no points is empty; any point is data', () => {
    expect(isChartSeriesEmpty(none)).toBe(true)
    expect(seriesHasPoints(none)).toBe(false)
    expect(isChartSeriesEmpty(points)).toBe(false)
    expect(seriesHasPoints(points)).toBe(true)
  })

  it('a truncated points chart shows its points count of the server total', () => {
    expect(shownCount(points)).toBe(4)
    const data: AnyChartResponse = { meta: { ...truncatedMeta.payload, truncated: true, truncated_total: 2400 } as never, series: points }
    const state = resolveChartState({ data, error: undefined, isValidating: false, everHadData: true, accessors: CHART_RESPONSE_ACCESSORS })
    expect(state).toMatchObject({ status: 'truncated', shown: 4, total: 2400 })
  })

  it('every test excluded resolves to filtered-empty, never a drawn empty plot', () => {
    const data: AnyChartResponse = { meta: null, series: none }
    const state = resolveChartState({ data, error: undefined, isValidating: false, everHadData: true, accessors: CHART_RESPONSE_ACCESSORS })
    expect(state.status).toBe('filtered-empty')
  })
})
