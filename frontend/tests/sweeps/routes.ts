/**
 * Every route the app serves, spelled as `src/App.tsx` spells it, and how the
 * sweeps fill its parameters from a fixtures file.
 *
 * `src/routing/sweepRoutes.test.ts` fails when App.tsx gains a route this list
 * lacks (or drops one it still has), so the sweeps cannot fall behind the app.
 */

export const ROUTE_TEMPLATES: readonly string[] = [
  'overview', 'getting-started', 'docs', 'docs/:docId', 'value-metrics', 'intelligence',
  'runs', 'runs/compare', 'runs/:runId', 'runs/:runId/intelligence', 'runs/:runId/tests/:testId',
  'coverage', 'coverage/suite', 'suites', 'suites/:suiteId', 'canonical-test-cases/:canonicalId',
  'failures', 'trends', 'explore', 'defects', 'search', 'chat', 'agents', 'agents/run/:runId',
  'agents/workflows', 'deep-investigate', 'deep-investigate/:runId', 'release-gate', 'release-gate/:runId',
  'flaky', 'flaky-coach', 'quarantine', 'reviews', 'test-management', 'live', 'my-failures',
  'reports/summary', 'settings/profile', 'settings/my-notifications', 'settings/my-api-keys', 'projects',
  'releases', 'releases/:releaseId', 'activity', 'users', 'settings', 'settings/notifications',
  'settings/team-channels', 'settings/ai', 'settings/integrations', 'settings/storage', 'settings/sso',
  'settings/digests', 'settings/integration-health', 'settings/audit', 'settings/ai-eval',
  'settings/performance', 'settings/seed-data', 'settings/feature-flags', 'settings/billing',
  'settings/github', 'settings/gitlab', 'settings/webhooks', 'settings/api-keys', 'settings/project-data',
  'settings/retention', 'settings/mfa-policy', 'settings/ai-agents', 'settings/agent-activity', 'policies',
  'policies/:policyId', 'ownership',
]

/** Fixture id key for each route parameter. */
const PARAM_IDS: Record<string, string> = {
  runId: 'failedRun',
  testId: 'failedTest',
  suiteId: 'suite',
  canonicalId: 'canonical',
  releaseId: 'release',
  policyId: 'policy',
  docId: '__unknown_doc__',
}

/** Routes swept more than once: a passing run's pages as well as a failing run's. */
const EXTRA_VARIANTS: Record<string, Record<string, string>[]> = {
  'runs/:runId': [{ runId: 'passedRun' }],
  'runs/:runId/tests/:testId': [{ runId: 'passedRun', testId: 'passedTest' }],
}

/** Stands in for an id the project does not have: the page must say "not found", never 500. */
export const MISSING_ID = '00000000-0000-0000-0000-000000000000'

function fill(template: string, ids: Record<string, string | null | undefined>, keys: Record<string, string>): string {
  return '/' + template.replace(/:(\w+)/g, (_, param: string) => {
    const key = keys[param] ?? PARAM_IDS[param]
    if (key === '__unknown_doc__') return 'does-not-exist'
    return ids[key] ?? MISSING_ID
  })
}

/** Concrete paths to visit, every parameter filled from the fixtures' ids. */
export function concreteRoutes(ids: Record<string, string | null | undefined>): string[] {
  const out: string[] = []
  for (const template of ROUTE_TEMPLATES) {
    out.push(fill(template, ids, {}))
    for (const keys of EXTRA_VARIANTS[template] ?? []) out.push(fill(template, ids, keys))
  }
  return out
}
