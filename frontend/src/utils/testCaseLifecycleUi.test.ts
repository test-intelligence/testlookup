import { describe, expect, it } from 'vitest'

import { TEST_CASE_LIFECYCLE_STATES } from '@/types/test-management'
import { buildTestCaseListParams, LIFECYCLE_STATUS_OPTIONS } from './testCaseLifecycleUi'

const baseFilters = {
  page: 1,
  size: 25,
  status: '' as const,
  testType: '',
  priority: '',
  search: '',
  ownerFilter: '',
  suiteFilter: '',
  includeAutomation: false,
}

describe('test-case lifecycle filters', () => {
  it('exposes every lifecycle state as an exact filter option', () => {
    expect(LIFECYCLE_STATUS_OPTIONS.map(({ value }) => value)).toEqual(TEST_CASE_LIFECYCLE_STATES)
    expect(LIFECYCLE_STATUS_OPTIONS.map(({ label }) => label)).toEqual([
      'Draft',
      'Review requested',
      'Under review',
      'Approved',
      'Active',
      'Rejected',
      'Needs update',
      'Deprecated',
      'Archived',
    ])
  })

  it('requests archived rows only when the archived filter is selected', () => {
    expect(buildTestCaseListParams({ ...baseFilters, status: 'archived' })).toEqual({
      page: 1,
      size: 25,
      status: 'archived',
      include_archived: true,
    })
    expect(buildTestCaseListParams({ ...baseFilters, status: 'deprecated' })).not.toHaveProperty('include_archived')
    expect(buildTestCaseListParams(baseFilters)).not.toHaveProperty('include_archived')
  })
})
