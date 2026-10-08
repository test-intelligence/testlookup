/**
 * The workflow editor at 1280 and 1440 px (the UX redesign's browser E2E
 * pass, 2026-10-07). The graph preview of a 20-step workflow is ~3,400 px wide
 * and scrolls inside its own box. Its column was a grid item with the default
 * `min-width` (its content's), so the column grew to the graph's width and the
 * whole page scrolled sideways (every role but admin saw it in the local-stack
 * crawl; admin's workflow list differs).
 *
 * Hermetic and fail closed (`production-pages.ts`): every request is mocked.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, expectNoHorizontalOverflow, networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT, PROJECT_ID } from '../visual/production/fixtures'

/** The built-in deep workflow's 20 steps, chained: one graph level each. */
const STEPS = [
  'ingestion', 'anomaly_detection', 'failure_clustering', 'cluster_investigation_dispatch', 'root_cause_analysis',
  'cluster_investigation_join', 'summary', 'triage', 'contract_validation', 'log_intelligence',
  'regression_watchman', 'change_ownership', 'defect_commander', 'flaky_quarantine', 'release_readiness',
  'test_impact', 'coverage_gap', 'notification', 'report', 'review',
]

const WORKFLOW = {
  id: 'deep',
  workflow_id: 'deep',
  version: 1,
  project_id: null,
  name: 'Deep workflow',
  description: 'Built-in TestLookup workflow template.',
  base: 'deep',
  definition: {
    workflow_id: 'deep',
    version: 1,
    project_id: null,
    base: 'deep',
    steps: STEPS.map((id) => ({ id, agent_id: `agent.${id}.v1`, tools: [], reviews: [] })),
    edges: STEPS.slice(1).map((to, i) => ({ from: STEPS[i], to })),
    loops: [],
    retry_policy: { max_attempts: 5, base_seconds: 30, cap_seconds: 600 },
    review_policy: 'human_required',
    deadline_seconds: 1500,
  },
  definition_sha256: '55e932804f2a9a44f68f30addb36dcc4c5376dd47ed9bda360d64087721f4063',
  status: 'published',
  published_at: null,
  eval_verdict: null,
  eval_coverage: null,
  eval_gate_run_id: null,
  evaluated_at: null,
  eval_regression_accepted: false,
  eval_regression_reason: null,
  eval_regression_accepted_at: null,
  read_only: true,
  built_in: true,
}

for (const width of [1280, 1440]) {
  test(`at ${width} px the workflow graph scrolls in its own box, never the page`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/agents/workflows', {
      handlers: [...LAYOUT, [`/api/v1/projects/${PROJECT_ID}/workflows`, () => ({ workflows: [WORKFLOW] })]],
      ready: (p) => p.locator('[data-workflow-preview-column] .overflow-auto svg').first(),
    })
    await networkQuiet(page, api)
    const box = page.locator('[data-workflow-preview-column] .overflow-auto').first()
    const { scrollWidth, clientWidth } = await box.evaluate((el) => ({ scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }))
    // Not vacuous: the graph really is far wider than its box.
    expect(scrollWidth, 'the graph is wider than its box').toBeGreaterThan(clientWidth + 1000)
    await expectNoHorizontalOverflow(page, `/agents/workflows at ${width} px`)
    assertHermetic(api, errors)
  })
}
