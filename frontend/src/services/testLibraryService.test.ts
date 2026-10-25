/**
 * W13: the test-library service sends each route's path (ids path-encoded),
 * its QUERY names (`project_id`, `kinds` repeated, `cursor`, `limit`, `kind`,
 * `page`, `size`, `dry_run`) and nothing that was not given; writes pass
 * `suppressToast` (the forms render the server's sentence); the import is
 * multipart (`file`) with the JSON content type unset.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { deleteData, getData, postData } from './http'
import { testLibraryService } from './testLibraryService'

vi.mock('./http', () => ({
  deleteData: vi.fn(),
  getData: vi.fn(),
  postData: vi.fn(),
}))

const P = '11111111-1111-4111-8111-111111111111'
const CASE = 'c0000000-0000-4000-8000-000000000001'
const TM = '/api/v1/test-management'

/** The mock's last call (fails the test when there was none). */
function lastCall(fn: unknown): unknown[] {
  const call = vi.mocked(fn as (...args: unknown[]) => unknown).mock.lastCall
  if (!call) throw new Error('not called')
  return call
}

/** What the shared client would put on the wire for this GET (its `paramsSerializer`). */
function wire(call: unknown[]): string {
  const [url, config] = call as [string, { params?: Record<string, unknown> } | undefined]
  return api.getUri({ url, params: config?.params })
}

beforeEach(() => vi.clearAllMocks())

describe('testLibraryService reads', () => {
  it('asks the health of ONE project', () => {
    testLibraryService.getHealth(P)
    expect(getData).toHaveBeenLastCalledWith(`${TM}/health`, { params: { project_id: P } })
  })

  it('asks a timeline page: every kind by default, limit 50, no cursor on the first page', () => {
    testLibraryService.getTimeline(CASE, {})
    expect(getData).toHaveBeenLastCalledWith(`${TM}/cases/${CASE}/timeline`, { params: { limit: 50 } })
    testLibraryService.getTimeline(CASE, { kinds: [], cursor: null })
    expect(getData).toHaveBeenLastCalledWith(`${TM}/cases/${CASE}/timeline`, { params: { limit: 50 } })
  })

  it('repeats `kinds` on the wire and carries the cursor and the limit', () => {
    testLibraryService.getTimeline(CASE, { kinds: ['plan_execution', 'automated_execution'], cursor: 'eyJ0IjoxfQ==', limit: 20 })
    const call = lastCall(getData)
    expect(call).toEqual([`${TM}/cases/${CASE}/timeline`, {
      params: { kinds: ['plan_execution', 'automated_execution'], cursor: 'eyJ0IjoxfQ==', limit: 20 },
    }])
    const url = wire(call)
    expect(url).toContain('kinds=plan_execution&kinds=automated_execution')
    expect(url).not.toContain('kinds[]')
    expect(url).toContain('cursor=eyJ0IjoxfQ%3D%3D')
  })

  it('path-encodes the case id', () => {
    testLibraryService.getTimeline('a/../b', {})
    expect(lastCall(getData)[0]).toBe(`${TM}/cases/a%2F..%2Fb/timeline`)
    testLibraryService.listRequirements('a/../b')
    expect(getData).toHaveBeenLastCalledWith(`${TM}/cases/a%2F..%2Fb/requirements`)
  })

  it('pages the evidence gaps and the traceability gaps (25 by default)', () => {
    testLibraryService.getEvidenceGapPage(P, 'flaky_active')
    expect(getData).toHaveBeenLastCalledWith(`${TM}/cases/evidence-gaps`, {
      params: { project_id: P, kind: 'flaky_active', page: 1, size: 25 },
    })
    testLibraryService.getEvidenceGapPage(P, 'no_owner', 3, 10)
    expect(getData).toHaveBeenLastCalledWith(`${TM}/cases/evidence-gaps`, {
      params: { project_id: P, kind: 'no_owner', page: 3, size: 10 },
    })
    testLibraryService.getTraceabilityGaps(P)
    expect(getData).toHaveBeenLastCalledWith(`${TM}/traceability/gaps`, { params: { project_id: P, page: 1, size: 25 } })
    testLibraryService.getTraceabilityGaps(P, 2)
    expect(getData).toHaveBeenLastCalledWith(`${TM}/traceability/gaps`, { params: { project_id: P, page: 2, size: 25 } })
  })
})

describe('testLibraryService writes', () => {
  it('adds and removes a requirement link with no global toast', () => {
    const body = { requirement_key: 'REQ-PAY-07', requirement_title: 'Refund within 24h', link_type: 'verifies' }
    testLibraryService.addRequirement(CASE, body)
    expect(postData).toHaveBeenLastCalledWith(`${TM}/cases/${CASE}/requirements`, body, { suppressToast: true })
    testLibraryService.removeRequirement(CASE, 'l/1')
    expect(deleteData).toHaveBeenLastCalledWith(`${TM}/cases/${CASE}/requirements/l%2F1`, { suppressToast: true })
  })

  it.each([true, false])('imports a file as multipart, dry_run=%s, the project in the QUERY', (dryRun) => {
    const file = new File(['Title\nLogin works\n'], 'cases.csv', { type: 'text/csv' })
    testLibraryService.importCases(P, file, dryRun)
    const [url, form, config] = lastCall(postData) as [string, FormData, Record<string, unknown>]
    expect(url).toBe(`${TM}/cases/import`)
    expect(form).toBeInstanceOf(FormData)
    expect([...form.keys()]).toEqual(['file'])
    expect((form.get('file') as File).name).toBe('cases.csv')
    expect(config).toEqual({
      params: { project_id: P, dry_run: dryRun },
      headers: { 'Content-Type': undefined },
      suppressToast: true,
    })
    expect(wire([url, config])).toContain(`dry_run=${dryRun}`)
  })
})
