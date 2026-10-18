import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getData, patchData, postData } from './http'
import { conflictOf, errorSentence, testCaseEditingService } from './testCaseEditingService'

vi.mock('./http', () => ({
  getData: vi.fn(),
  patchData: vi.fn(),
  postData: vi.fn(),
}))

const QUIET = { suppressToast: true }

function httpError(status: number, detail: unknown) {
  return { response: { status, data: { detail } } }
}

describe('testCaseEditingService request contracts (W28)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the edit as one PATCH of expected_version + the changed fields, without the global toast', () => {
    testCaseEditingService.updateCase('case-1', { expected_version: 4, title: 'Checkout v2', change_summary: 'clarify' })
    expect(patchData).toHaveBeenCalledTimes(1)
    expect(patchData).toHaveBeenCalledWith(
      '/api/v1/test-management/cases/case-1',
      { expected_version: 4, title: 'Checkout v2', change_summary: 'clarify' },
      QUIET,
    )
  })

  it('re-reads the case and its history quietly (the 409 notice)', () => {
    testCaseEditingService.getCaseFresh('case-1')
    testCaseEditingService.getCaseHistoryFresh('case-1')
    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases/case-1', QUIET)
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/test-management/cases/case-1/history', QUIET)
  })

  it('posts the bulk edit and the restore to their routes, quietly', () => {
    const bulk = {
      project_id: 'p-1',
      case_ids: ['a', 'b'],
      expected_versions: { a: 1, b: 3 },
      action: 'transition' as const,
      transition: 'activate' as const,
    }
    testCaseEditingService.bulkUpdate(bulk)
    testCaseEditingService.restoreVersion('case-1', { version: 2, expected_version: 4 })
    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/test-management/cases/bulk', bulk, QUIET)
    expect(postData).toHaveBeenNthCalledWith(
      2,
      '/api/v1/test-management/cases/case-1/restore',
      { version: 2, expected_version: 4 },
      QUIET,
    )
  })
})

describe('conflictOf', () => {
  it.each([
    ['the PATCH stale-version 409', httpError(409, { current_version: 3, expected_version: 1, message: 'stale' }), { currentVersion: 3 }],
    ['a 409 without current_version (a lifecycle refusal)', httpError(409, { current_state: 'draft', attempted_action: 'activate' }), null],
    ['a 409 with a string detail', httpError(409, 'Cannot edit a deprecated test case'), null],
    ['a 409 whose current_version is not a number', httpError(409, { current_version: '3' }), null],
    ['a 422 with current_version', httpError(422, { current_version: 3 }), null],
    ['a network error', new Error('Network Error'), null],
    ['nothing', undefined, null],
  ])('%s', (_name, error, expected) => {
    expect(conflictOf(error)).toEqual(expected)
  })
})

describe('errorSentence', () => {
  it.each([
    ['a string detail', httpError(422, 'The assignee must be an active member of this project.'), 'The assignee must be an active member of this project.'],
    ['a detail.message', httpError(409, { message: 'This test case was changed.', current_version: 2 }), 'This test case was changed.'],
    ['a blank string detail', httpError(500, '   '), 'Fallback.'],
    ['a validation list', httpError(422, [{ loc: ['body', 'title'], msg: 'too short' }]), 'Fallback.'],
    ['no response', new Error('boom'), 'Fallback.'],
  ])('%s', (_name, error, expected) => {
    expect(errorSentence(error, 'Fallback.')).toBe(expected)
  })
})
