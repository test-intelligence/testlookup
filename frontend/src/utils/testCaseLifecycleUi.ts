import {
  TEST_CASE_LIFECYCLE_STATES,
} from '@/types/test-management'

export const LIFECYCLE_STATUS_OPTIONS = TEST_CASE_LIFECYCLE_STATES.map((value) => ({
  value,
  label: value.replace(/_/g, ' ').replace(/^./, (character) => character.toUpperCase()),
}))

interface TestCaseListFilterValues {
  page: number
  size: number
  status: string
  testType: string
  priority: string
  search: string
  ownerFilter: string
  suiteFilter: string
  includeAutomation: boolean
}

export function buildTestCaseListParams(filters: TestCaseListFilterValues): Record<string, unknown> {
  const params: Record<string, unknown> = { page: filters.page, size: filters.size }
  if (filters.status) params.status = filters.status
  if (filters.testType) params.test_type = filters.testType
  if (filters.priority) params.priority = filters.priority
  if (filters.search) params.search = filters.search
  if (filters.ownerFilter) params.assignee_id = filters.ownerFilter
  if (filters.suiteFilter) params.suite_name = filters.suiteFilter
  if (filters.includeAutomation) params.include_automation = true
  if (filters.status === 'archived') params.include_archived = true
  return params
}
