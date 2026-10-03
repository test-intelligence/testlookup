/**
 * Wave 3 sources (FK0): the four new endpoints reach their charts as a
 * validated `ChartResponse` of ONE kind, and the wire's envelope (the series'
 * keys at the top level with `meta` beside them: `with_meta(payload, meta)`)
 * is reshaped into `{meta, series}` before the validator sees it.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import envelopeFixture from '../../../../contracts/viz/fixtures/envelope/valid/filtered.json'
import matrixFixture from '../../../../contracts/viz/fixtures/chart_series/valid/matrix_rate_keys_counts.json'
import pointsFixture from '../../../../contracts/viz/fixtures/chart_series/valid/points.json'
import treeFixture from '../../../../contracts/viz/fixtures/chart_series/valid/tree_stats.json'
import graphFixture from '../../../../contracts/viz/fixtures/chart_series/valid/graph_group_hostile.json'
import seriesFixture from '../../../../contracts/viz/fixtures/chart_series/valid/series_multi.json'
import {
  CATALOG_KINDS,
  CATALOG_URLS,
  catalogFetcher,
  catalogValidator,
  chartResponseFromEnvelope,
  useCatalogChartData,
  type CatalogSource,
} from './chartCatalogSources'

const chartGet = vi.hoisted(() => vi.fn())

vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet,
}))

const signal = new AbortController().signal
const meta = envelopeFixture.payload

/** The body exactly as the analytics routes send it: the series' keys at the top, `meta` beside them. */
const wire = (series: object) => ({ ...series, meta })

beforeEach(() => chartGet.mockReset())

describe('chartResponseFromEnvelope', () => {
  it('reshapes the wire body into {meta, series}', () => {
    const body = wire(matrixFixture.payload)
    expect(chartResponseFromEnvelope(body)).toEqual({ meta, series: matrixFixture.payload })
  })

  it('reshapes a chart-data body too, whose own `series` key is the ARRAY of lines', () => {
    const body = wire(seriesFixture.payload)
    expect(chartResponseFromEnvelope(body)).toEqual({ meta, series: seriesFixture.payload })
    expect(catalogValidator('chart-data')(chartResponseFromEnvelope(body)).ok).toBe(true)
  })

  it('chart-data’s own fetcher reshapes its wire body (the Wave 2.6 sources were handed it unshaped)', async () => {
    chartGet.mockResolvedValue({ data: wire(seriesFixture.payload), requestId: null })
    const { data } = await catalogFetcher('chart-data', {})(['chart-data', {}], { signal })
    expect(data).toEqual({ meta, series: seriesFixture.payload })
  })

  it('a body with no meta gets meta null, never undefined', () => {
    expect(chartResponseFromEnvelope({ ...pointsFixture.payload })).toEqual({ meta: null, series: pointsFixture.payload })
  })

  it('passes an already-wrapped body through as the SAME object (the hermetic fixtures, the gallery)', () => {
    const wrapped = { meta: null, series: pointsFixture.payload }
    expect(chartResponseFromEnvelope(wrapped)).toBe(wrapped)
  })

  it('leaves anything else alone for the validator to reject', () => {
    for (const odd of [null, 'text', 7, [1, 2], { items: [] }, { kind: 3 }]) expect(chartResponseFromEnvelope(odd)).toBe(odd)
  })
})

describe('Wave 3 catalog sources', () => {
  const cases: [CatalogSource, string, object][] = [
    ['heatmap', '/api/v1/analytics/heatmap', matrixFixture.payload],
    ['coverage-map', '/api/v1/analytics/coverage-map', treeFixture.payload],
    ['failure-groups', '/api/v1/analytics/failure-groups', graphFixture.payload],
    ['test-scatter', '/api/v1/analytics/test-scatter', pointsFixture.payload],
  ]

  it.each(cases)('%s asks %s and validates its wire body', async (source, url, series) => {
    expect(CATALOG_URLS[source]).toBe(url)
    chartGet.mockResolvedValue({ data: wire(series), requestId: 'req-w3' })
    const result = await catalogFetcher(source, { project_id: 'p1' })([source, {}], { signal })
    expect(chartGet).toHaveBeenCalledWith(url, { params: { project_id: 'p1' }, signal })
    expect(result.requestId).toBe('req-w3')
    const checked = catalogValidator(source)(result.data)
    expect(checked.ok ? [] : checked.errors).toEqual([])
  })

  it('holds each Wave 3 source to its one kind', () => {
    expect(CATALOG_KINDS).toEqual({ heatmap: 'matrix', 'coverage-map': 'tree', 'failure-groups': 'graph', 'test-scatter': 'points' })
    const checked = catalogValidator('heatmap')({ meta: null, series: treeFixture.payload })
    expect(checked.ok).toBe(false)
    if (!checked.ok) expect(checked.errors).toEqual(['series: kind_enum: heatmap answers a matrix series, got tree'])
  })

  it('still rejects an invalid body of the right kind, with the field named', () => {
    const checked = catalogValidator('test-scatter')({ meta: null, series: { ...pointsFixture.payload, points: [{ id: 'a' }] } })
    expect(checked.ok).toBe(false)
    if (!checked.ok) expect(checked.errors.every((e) => e.startsWith('series: '))).toBe(true)
  })

  it('the older sources keep the four-kind check: a points body is refused there', () => {
    for (const source of ['chart-data', 'top-failing', 'failure-categories'] as const) {
      expect(CATALOG_KINDS[source]).toBeUndefined()
      expect(catalogValidator(source)({ meta: null, series: pointsFixture.payload }).ok).toBe(false)
      expect(catalogValidator(source)({ meta: null, series: matrixFixture.payload }).ok).toBe(true)
    }
  })

  it('keeps the endpoint’s additive keys on the series object (failure-groups `groups`, `singletons`)', async () => {
    const extras = { groups: [{ id: 'g' }], singletons: { id: '__SINGLETONS__', group_count: 0, failure_count: 0, share_of_failures: 0 } }
    chartGet.mockResolvedValue({ data: wire({ ...graphFixture.payload, ...extras }), requestId: null })
    const { data } = await catalogFetcher('failure-groups', {})(['failure-groups', {}], { signal })
    const checked = catalogValidator('failure-groups')(data)
    expect(checked.ok).toBe(true)
    if (checked.ok) expect((checked.value.series as unknown as typeof extras).groups).toEqual(extras.groups)
  })
})

describe('useCatalogChartData on a Wave 3 source', () => {
  it('resolves a points body to a drawn state typed as a points response', async () => {
    chartGet.mockResolvedValue({ data: wire(pointsFixture.payload), requestId: 'req-pts' })
    const { result } = renderHook(() =>
      useCatalogChartData('test-scatter', { params: { project_id: 'p-scatter', min_executions: 5 }, everHadData: true }),
    )
    await waitFor(() => expect(result.current.status).toBe('ready'))
    const state = result.current
    if (state.status !== 'ready') throw new Error(state.status)
    // Typed `PointsChart`: no narrowing needed to read `points`.
    expect(state.data.series.points).toHaveLength(4)
  })

  it('a body of the wrong kind is an invalid-payload error naming the request, never a half-drawn chart', async () => {
    chartGet.mockResolvedValue({ data: wire(treeFixture.payload), requestId: 'req-wrong' })
    const { result } = renderHook(() => useCatalogChartData('heatmap', { params: { project_id: 'p-wrong' }, everHadData: true }))
    await waitFor(() => expect(result.current.status).toBe('error'))
    const state = result.current
    if (state.status !== 'error') throw new Error(state.status)
    expect(state.error.kind).toBe('invalid-payload')
    expect(state.error.requestId).toBe('req-wrong')
  })
})
