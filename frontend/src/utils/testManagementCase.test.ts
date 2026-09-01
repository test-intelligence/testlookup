import { describe, expect, it } from 'vitest'

import type { ManagedTestCase } from '@/types/test-management'
import { getTestManagementCaseDetailPath } from './testManagementCase'

function testCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'test-1',
    project_id: 'project-1',
    title: 'test_login',
    test_type: 'automation',
    priority: 'medium',
    severity: 'major',
    status: 'active',
    version: 1,
    is_automated: true,
    automation_status: 'automated',
    ai_generated: false,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

describe('getTestManagementCaseDetailPath', () => {
  it('routes automation rows to their complete latest execution detail', () => {
    expect(getTestManagementCaseDetailPath(testCase({
      source: 'automation',
      latest_run_id: 'run-1',
      latest_test_case_id: 'test-1',
    }))).toBe('/runs/run-1/tests/test-1')
  })

  it('keeps authored catalog cases in the Test Management side panel', () => {
    expect(getTestManagementCaseDetailPath(testCase({
      source: 'managed',
      latest_run_id: 'run-1',
      latest_test_case_id: 'test-1',
    }))).toBeNull()
  })

  it('falls back safely when an older API omits latest execution identity', () => {
    expect(getTestManagementCaseDetailPath(testCase({ source: 'automation' }))).toBeNull()
  })
})
