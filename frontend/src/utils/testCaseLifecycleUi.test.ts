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

  it('asks for AI-generated cases only for the AI drafts view (W28)', () => {
    expect(buildTestCaseListParams({ ...baseFilters, status: 'draft', aiGenerated: true })).toEqual({
      page: 1,
      size: 25,
      status: 'draft',
      ai_generated: true,
    })
    expect(buildTestCaseListParams({ ...baseFilters, aiGenerated: false })).not.toHaveProperty('ai_generated')
    expect(buildTestCaseListParams(baseFilters)).not.toHaveProperty('ai_generated')
  })

  // ── W13 TCL-01: multi-value filters, the Owner and Automation filters ──
  it('sends several values of a filter as an array (repeated keys), one value as today', () => {
    expect(buildTestCaseListParams({
      ...baseFilters,
      status: ['draft', 'review_requested'],
      testType: ['e2e'],
      priority: ['critical', 'high'],
    })).toEqual({
      page: 1,
      size: 25,
      status: ['draft', 'review_requested'],
      test_type: 'e2e',
      priority: ['critical', 'high'],
    })
    // Empty lists and blank values send nothing; duplicates are sent once.
    expect(buildTestCaseListParams({ ...baseFilters, status: [], testType: [''], priority: ['high', 'high'] }))
      .toEqual({ page: 1, size: 25, priority: 'high' })
  })

  it('asks for archived rows when the archived status is among several', () => {
    expect(buildTestCaseListParams({ ...baseFilters, status: ['deprecated', 'archived'] })).toMatchObject({
      status: ['deprecated', 'archived'],
      include_archived: true,
    })
    expect(buildTestCaseListParams({ ...baseFilters, status: ['deprecated', 'draft'] })).not.toHaveProperty('include_archived')
  })

  it('sends the owner as `owner` (me or a user id), never as assignee_id', () => {
    const mine = buildTestCaseListParams({ ...baseFilters, ownerFilter: 'me' })
    expect(mine).toEqual({ page: 1, size: 25, owner: 'me' })
    expect(mine).not.toHaveProperty('assignee_id')
    const someone = buildTestCaseListParams({ ...baseFilters, ownerFilter: '6f1c2a54-0000-4000-8000-000000000001' })
    expect(someone.owner).toBe('6f1c2a54-0000-4000-8000-000000000001')
    expect(someone).not.toHaveProperty('assignee_id')
    expect(buildTestCaseListParams(baseFilters)).not.toHaveProperty('owner')
  })

  it('sends the automation and requirement filters only when set', () => {
    expect(buildTestCaseListParams({ ...baseFilters, automation: 'not_automated', requirement: 'REQ-PAY-07' })).toEqual({
      page: 1,
      size: 25,
      automation: 'not_automated',
      requirement: 'REQ-PAY-07',
    })
    const none = buildTestCaseListParams({ ...baseFilters, automation: null, requirement: null })
    expect(none).not.toHaveProperty('automation')
    expect(none).not.toHaveProperty('requirement')
  })
})
