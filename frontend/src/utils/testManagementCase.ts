import type { ManagedTestCase } from '@/types/test-management'

/**
 * The links a test case's panel offers.
 *
 * Every row in Test Management opens the same case panel, in place. Before the
 * owner's review of the test case view (2026-10-10) a click did one of four
 * things depending on data the reader could not see: an authored case opened
 * the panel, an automation row left the page for ONE run's result, an
 * automation row with only a canonical id left for a third page, and one with
 * neither raised a toast. Leaving the page also lost the list's search,
 * filters and page. The rich pages are now one deliberate click away from the
 * panel, for authored cases linked to automation too (the API attaches the ids
 * by fingerprint):
 *
 * - {@link latestResultPath}: the latest execution's run detail
 * - {@link runHistoryPath}: the test across runs (the canonical page)
 */
export function latestResultPath(testCase: ManagedTestCase): string | null {
  if (!testCase.latest_run_id || !testCase.latest_test_case_id) return null
  return `/runs/${encodeURIComponent(testCase.latest_run_id)}/tests/${encodeURIComponent(testCase.latest_test_case_id)}`
}

export function runHistoryPath(testCase: ManagedTestCase): string | null {
  if (!testCase.canonical_test_case_id) return null
  return `/canonical-test-cases/${encodeURIComponent(testCase.canonical_test_case_id)}`
}
