import { describe, expect, it } from 'vitest'

import type { ManagedTestCase } from '@/types/test-management'
import { latestResultPath, runHistoryPath } from './testManagementCase'

function testCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'test-1',
    project_id: 'project-1',
    title: 'test_login',
    test_type: 'automation',
    priority: 'medium',
    severity: 'major',
    test_suite_id: null,
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

describe('latestResultPath', () => {
  it('links an automation row to its latest execution', () => {
    expect(latestResultPath(testCase({
      source: 'automation',
      latest_run_id: 'run-1',
      latest_test_case_id: 'test-1',
    }))).toBe('/runs/run-1/tests/test-1')
  })

  it('links an authored case linked to automation the same way', () => {
    expect(latestResultPath(testCase({
      source: 'managed',
      latest_run_id: 'run-1',
      latest_test_case_id: 'tc / 1',
    }))).toBe('/runs/run-1/tests/tc%20%2F%201')
  })

  it('has no link without both execution ids', () => {
    expect(latestResultPath(testCase({ source: 'automation', latest_run_id: 'run-1' }))).toBeNull()
    expect(latestResultPath(testCase({ source: 'managed' }))).toBeNull()
  })
})

describe('runHistoryPath', () => {
  it('links to the test across runs', () => {
    expect(runHistoryPath(testCase({ canonical_test_case_id: 'canonical / 1' })))
      .toBe('/canonical-test-cases/canonical%20%2F%201')
  })

  it('has no link without a canonical id', () => {
    expect(runHistoryPath(testCase({ source: 'automation' }))).toBeNull()
  })
})
