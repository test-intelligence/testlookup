import { beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteData, getData, postData } from './http'
import { suitesService } from './suitesService'
import { testManagementService } from './testManagementService'

vi.mock('./http', () => ({
  deleteData: vi.fn(),
  getData: vi.fn(),
  patchData: vi.fn(),
  postData: vi.fn(),
  putData: vi.fn(),
}))

describe('lifecycle API contracts', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the single transition endpoint and exact request body', () => {
    testManagementService.transitionCase('case-1', {
      action: 'request_changes',
      notes: 'Missing boundary coverage',
    })

    expect(postData).toHaveBeenCalledWith('/api/v1/test-management/cases/case-1/transition', {
      action: 'request_changes',
      notes: 'Missing boundary coverage',
    })
  })

  it('queries measured evidence by project and kind', () => {
    testManagementService.getEvidenceGaps('project-1', 'automation_vanished')

    expect(getData).toHaveBeenCalledWith('/api/v1/test-management/cases/evidence-gaps', {
      params: { project_id: 'project-1', kind: 'automation_vanished' },
    })
  })

  it('keeps DELETE compatibility while carrying a supplied reason in the body', () => {
    testManagementService.deleteCase('case-1', 'Superseded')

    expect(deleteData).toHaveBeenCalledWith(
      '/api/v1/test-management/cases/case-1',
      undefined,
      { reason: 'Superseded' },
    )
  })

  it('promotes, unlinks, and confirms retirement on canonical identity', () => {
    suitesService.promoteCanonical('canonical-1')
    suitesService.unlinkManagedCase('canonical-1', 'Wrong identity match')
    suitesService.confirmRetirement('canonical-1', 'Removed intentionally')

    expect(postData).toHaveBeenCalledWith('/api/v1/canonical-test-cases/canonical-1/promote', {})
    expect(deleteData).toHaveBeenCalledWith(
      '/api/v1/canonical-test-cases/canonical-1/managed-link',
      undefined,
      { reason: 'Wrong identity match' },
    )
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/canonical-test-cases/canonical-1/confirm-retirement',
      { reason: 'Removed intentionally' },
    )
  })
})
