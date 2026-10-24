/**
 * W12 SRCH-04 — the search box's filter keys, for the page's Syntax help. Pure.
 *
 * Mirrors the server's `FILTER_KEYS` (order) and `FILTER_SUPPORT` (which kinds
 * apply each key) in `backend/app/core/search_vocab.py`;
 * `backend/tests/test_search_syntax_frontend_parity.py` reads THIS file as text
 * and compares. Keep one entry per line, in the shape
 * `{ key: '<key>', example: '<example>', appliesTo: ['<entity_type>', …] },`.
 * An entity type that cannot apply a key is NOT searched when the key is used
 * (the page says so under the chips).
 */

export interface SyntaxKey {
  key: string
  example: string
  appliesTo: readonly string[]
}

export const SEARCH_SYNTAX: readonly SyntaxKey[] = [
  { key: 'status', example: 'status:failed', appliesTo: ['test_case', 'test_run'] },
  { key: 'suite', example: 'suite:"API Regression"', appliesTo: ['test_case', 'test_run', 'suite', 'flaky_test'] },
  { key: 'branch', example: 'branch:main', appliesTo: ['test_case', 'test_run', 'suite', 'flaky_test'] },
  { key: 'env', example: 'env:staging', appliesTo: ['test_case', 'test_run', 'suite', 'flaky_test'] },
  { key: 'type', example: 'type:flaky', appliesTo: ['test_case', 'test_run', 'suite', 'defect', 'flaky_test', 'release'] },
  { key: 'since', example: 'since:7d', appliesTo: ['test_case', 'test_run', 'suite', 'defect', 'flaky_test', 'release'] },
  { key: 'release', example: 'release:"2026.11"', appliesTo: ['test_case', 'test_run', 'suite', 'flaky_test', 'release'] },
  { key: 'owner', example: 'owner:payments', appliesTo: ['test_case', 'defect'] },
]

/** The server's `ENTITY_LABELS`. */
export const SYNTAX_ENTITY_LABELS: Record<string, string> = {
  test_case: 'Tests',
  test_run: 'Runs',
  suite: 'Suites',
  defect: 'Defects',
  flaky_test: 'Flaky',
  release: 'Releases',
}

/** One sentence per key's values, under the table. */
export const SYNTAX_NOTES: readonly string[] = [
  'status: takes passed, failed, error or skipped (runs: passed or failed).',
  'since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days.',
  'type: takes test, run, suite, defect, flaky or release.',
  'Quote a value with spaces: suite:"API Regression". A key the list does not name is searched as text.',
]

/** "Tests, Runs" — or "Every kind" when a key applies to all six. */
export function appliesToLabel(row: Pick<SyntaxKey, 'appliesTo'>): string {
  const all = Object.keys(SYNTAX_ENTITY_LABELS)
  if (all.every((entity) => row.appliesTo.includes(entity))) return 'Every kind'
  return row.appliesTo.map((entity) => SYNTAX_ENTITY_LABELS[entity] ?? entity).join(', ')
}
