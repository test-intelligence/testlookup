/**
 * Display names for the stores `GET /{project_id}/storage` returns.
 *
 * Separate from the panel so it can be asserted without a DOM, and because a
 * component file that exports non-components breaks Fast Refresh.
 *
 * The table falls back to the raw key for an unknown store, so a backend that
 * adds a line renders it verbatim rather than failing — which is exactly the
 * kind of thing nobody notices. A test pins this map against the stores the
 * backend can actually return.
 */
export const STORE_LABELS: Record<string, string> = {
  object_storage: 'Object storage',
  postgres: 'Runs & test cases',
  mongo: 'Run document collections',
  // Its own line, not folded into `mongo`: "how much are my reports costing
  // me" cannot be answered from a figure that also contains raw Allure
  // payloads, and reports ride the audit clock rather than the runs clock, so
  // the two numbers describe different retention windows.
  reports: 'Decision reports',
}
