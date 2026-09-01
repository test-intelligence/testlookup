import type { ManagedTestCase } from '@/types/test-management'

/**
 * Automation rows are execution snapshots, so their authoritative detail is
 * the rich run/test page. Authored catalog rows intentionally return null and
 * keep using Test Management's editing/review side panel.
 */
export function getTestManagementCaseDetailPath(testCase: ManagedTestCase): string | null {
  if (testCase.source !== 'automation') return null
  if (testCase.latest_run_id && testCase.latest_test_case_id) {
    return `/runs/${encodeURIComponent(testCase.latest_run_id)}/tests/${encodeURIComponent(testCase.latest_test_case_id)}`
  }
  if (testCase.canonical_test_case_id) {
    return `/canonical-test-cases/${encodeURIComponent(testCase.canonical_test_case_id)}`
  }
  return null
}
