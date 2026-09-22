/**
 * Contract C1 on the WIRE (VIZ-303): none → no parameter, one → the legacy
 * scalar `release_id=R1`, several → a repeated `release_id`. Same for
 * `suite_name`.
 *
 * The params are rendered to a real query string through the shared axios
 * instance (`api.getUri`), because the failure this guards is invisible at the
 * params-object level: axios's DEFAULT array encoding is `release_id[]=R1`,
 * which FastAPI silently drops — the filter would do nothing, with no error.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getData = vi.hoisted(() =>
  vi.fn(async (_url: string, _config?: { params?: Record<string, unknown> }) => ({})),
)
vi.mock('./http', () => ({ getData, postData: vi.fn(), putData: vi.fn(), deleteData: vi.fn() }))

import { api } from './api'
import { analyticsService } from './analyticsService'
import { metricsService } from './metricsService'
import { summaryReportService } from './summaryReportService'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

/** The query string the last call would put on the wire. */
function lastQuery(): URLSearchParams {
  const call = getData.mock.calls[getData.mock.calls.length - 1]
  const uri = api.getUri({ url: call[0], params: call[1]?.params })
  return new URL(uri, 'http://x').searchParams
}

describe('multi-value scope on the wire', () => {
  beforeEach(() => getData.mockClear())

  it('none: the parameter is absent (not empty, not null)', async () => {
    await analyticsService.getCoverage(PROJECT, 30, [], [])
    const q = lastQuery()
    expect(q.has('release_id')).toBe(false)
    expect(q.has('suite_name')).toBe(false)
  })

  it('one: a single scalar, exactly the legacy request', async () => {
    await analyticsService.getCoverage(PROJECT, 30, ['payments'], [R1])
    const q = lastQuery()
    expect(q.getAll('release_id')).toEqual([R1])
    expect(q.getAll('suite_name')).toEqual(['payments'])
    // And the params object itself carries a scalar, not a one-element array.
    const params = getData.mock.calls[0][1]?.params as Record<string, unknown>
    expect(params.release_id).toBe(R1)
    expect(params.suite_name).toBe('payments')
  })

  it('many: a repeated bare key — never `release_id[]`', async () => {
    await analyticsService.getCoverage(PROJECT, 30, ['payments', 'cart'], [R2, R1])
    const uri = api.getUri({ url: '/x', params: getData.mock.calls[0][1]?.params })
    expect(uri).not.toContain('%5B%5D')
    expect(uri).not.toContain('[]')
    const q = lastQuery()
    expect(q.getAll('release_id')).toEqual([R1, R2])
    expect(q.getAll('suite_name')).toEqual(['cart', 'payments'])
  })

  it('the unattributed sentinel goes out as itself', async () => {
    await metricsService.getSummary(PROJECT, 30, null, ['unattributed'])
    expect(lastQuery().getAll('release_id')).toEqual(['unattributed'])
    await metricsService.getTrends(PROJECT, 30, null, ['unattributed', R1])
    expect(lastQuery().getAll('release_id')).toEqual([R1, 'unattributed'].sort())
  })

  it('every release-capable analytics/metrics call follows the rule', async () => {
    const calls: Array<[string, () => unknown]> = [
      ['getFlakyTests', () => analyticsService.getFlakyTests(PROJECT, 30, null, [R1, R2])],
      ['getFailureCategories', () => analyticsService.getFailureCategories(PROJECT, 30, null, [R1, R2])],
      ['getTopFailing', () => analyticsService.getTopFailing(PROJECT, 30, null, [R1, R2])],
      ['getCoverage', () => analyticsService.getCoverage(PROJECT, 30, null, [R1, R2])],
      ['getSuiteDetail', () => analyticsService.getSuiteDetail(PROJECT, 'Checkout', 30, [R1, R2])],
      ['getSummary', () => metricsService.getSummary(PROJECT, 30, null, [R1, R2])],
      ['getTrends', () => metricsService.getTrends(PROJECT, 30, null, [R1, R2])],
      ['summaryReport.get', () => summaryReportService.get({ project_id: PROJECT, days: 30, mode: 'window', release_id: [R1, R2] })],
    ]
    for (const [name, call] of calls) {
      getData.mockClear()
      await call()
      expect(lastQuery().getAll('release_id'), name).toEqual([R1, R2])
    }
  })
})
