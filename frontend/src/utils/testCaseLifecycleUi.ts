import {
  TEST_CASE_LIFECYCLE_STATES,
} from '@/types/test-management'

export const LIFECYCLE_STATUS_OPTIONS = TEST_CASE_LIFECYCLE_STATES.map((value) => ({
  value,
  label: value.replace(/_/g, ' ').replace(/^./, (character) => character.toUpperCase()),
}))

/** One value, or several (W13 TCL-01: Status / Type / Priority are multi-select). */
type OneOrMany = string | readonly string[]

interface TestCaseListFilterValues {
  page: number
  size: number
  status: OneOrMany
  testType: OneOrMany
  priority: OneOrMany
  search: string
  /**
   * The Owner filter: `me` or a project member's user id, sent as `owner`
   * (W13 TCL-01). It used to be sent as `assignee_id`, a parameter the list
   * route never read.
   */
  ownerFilter: string
  suiteFilter: string
  includeAutomation: boolean
  /** W28 TCM-02: the "AI drafts" saved view (the list route's existing `ai_generated` filter). */
  aiGenerated?: boolean
  /** W13 TCL-01: `automated` | `not_automated`. */
  automation?: string | null
  /** W13 TCL-06: a requirement key (the list matches it after the server's normalisation). */
  requirement?: string | null
}

/** The non-empty values, de-duplicated in order. */
function values(value: OneOrMany): string[] {
  const list = typeof value === 'string' ? [value] : [...value]
  return Array.from(new Set(list.filter(Boolean)))
}

/**
 * One value is sent as a plain string (today's request, byte for byte); several
 * as an array, which the shared Axios base serialises as repeated keys
 * (`status=draft&status=review_requested`, `paramsSerializer: { indexes: null }`).
 */
function oneOrMany(list: string[]): string | string[] {
  return list.length === 1 ? list[0] : list
}

export function buildTestCaseListParams(filters: TestCaseListFilterValues): Record<string, unknown> {
  const params: Record<string, unknown> = { page: filters.page, size: filters.size }
  const statuses = values(filters.status)
  const types = values(filters.testType)
  const priorities = values(filters.priority)
  if (statuses.length) params.status = oneOrMany(statuses)
  if (types.length) params.test_type = oneOrMany(types)
  if (priorities.length) params.priority = oneOrMany(priorities)
  if (filters.search) params.search = filters.search
  if (filters.ownerFilter) params.owner = filters.ownerFilter
  if (filters.automation) params.automation = filters.automation
  if (filters.requirement) params.requirement = filters.requirement
  if (filters.suiteFilter) params.suite_name = filters.suiteFilter
  if (filters.includeAutomation) params.include_automation = true
  if (filters.aiGenerated) params.ai_generated = true
  if (statuses.includes('archived')) params.include_archived = true
  return params
}
