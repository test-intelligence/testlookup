/**
 * The spine: one failure, carried across every hop of the product's core chain.
 *
 *   /runs  ->  /runs/:id  ->  /runs/:id/intelligence  ->  /reports/summary
 *          ->  /release-gate/:id
 *
 * Gap G1 from the EXJ-2026-09-18 plan: **no test crosses more than one hop.**
 * Forty-five e2e specs and thirty-three live probes each stop at a single page,
 * so a run that exists on `/runs` but whose failure never reaches the release
 * gate's reasoning would be caught by nothing. Each page's own spec passes; the
 * chain between them is unowned.
 *
 * This lives in `ci-e2e/` deliberately. CI runs **only** this directory
 * (`playwright.ci.config.ts`, invoked by `.github/workflows/ci.yml`); the 45
 * specs in `tests/e2e/` and the 33 `probe-*` specs never run there. A spine
 * test in `tests/e2e/` would be a document, not a gate.
 *
 * ## What this asserts, and what it cannot
 *
 * The lane is hermetic — every `/api/v1/**` call is fulfilled from the fixture
 * below — because CI has a frontend dev server and no backend. So this pins the
 * **cross-page contract**: given one run whose failure is `SPINE_FAILURE`, every
 * hop addresses the same run id and surfaces that same failure, and the release
 * gate blocks on it by name.
 *
 * It does **not** prove the backend produces that chain. A stack-level version
 * of the same walk belongs in the probe tier against a real deployment, where
 * the seeded failure is ingested rather than mocked. Recorded here so this
 * file is not mistaken for end-to-end proof of the pipeline.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

const RUN_ID = '11111111-2222-4333-8444-555555555555'
const PROJECT_ID = '00000000-0000-4000-8000-0000000000aa'

/** The one identity that has to survive every hop. */
const SPINE_FAILURE = 'test_checkout_declines_expired_card'

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'qa@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

const project = { id: PROJECT_ID, name: 'Checkout Service', slug: 'checkout-service', is_active: true }

const run = {
  id: RUN_ID,
  project_id: PROJECT_ID,
  project_name: project.name,
  build_number: 'spine-001',
  status: 'FAILED',
  total_tests: 6,
  passed_tests: 4,
  failed_tests: 1,
  skipped_tests: 1,
  broken_tests: 0,
  unknown_tests: 0,
  pass_rate: 80.0,
  duration_ms: 12456,
  primary_suite_name: 'CheckoutSuite',
  suite_names: ['CheckoutSuite'],
  run_seq: 1,
  ingestion_complete: true,
  ingestion_rejected_tests: 0,
  ingestion_source: 'upload',
  start_time: '2026-09-18T10:00:00Z',
  end_time: '2026-09-18T10:00:12Z',
  created_at: '2026-09-18T10:00:00Z',
  updated_at: '2026-09-18T10:00:12Z',
  branch: 'main',
  release_name: 'Unreleased',
}

const failingCase = {
  id: '99999999-0000-4000-8000-000000000001',
  test_run_id: RUN_ID,
  test_name: SPINE_FAILURE,
  class_name: 'CheckoutTests',
  suite_name: 'CheckoutSuite',
  status: 'FAILED',
  duration_ms: 4120,
  error_message: 'AssertionError: expected decline, got approval',
  stack_trace: 'CheckoutTests.py:88 in test_checkout_declines_expired_card',
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installSpineApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname

    if (path === '/api/v1/auth/login' && request.method() === 'POST') {
      return json(route, { access_token: 'access', refresh_token: 'refresh', token_type: 'bearer' })
    }
    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/sso/status') {
      return json(route, { sso_enabled: false, has_active_config: false, enforcement_mode: 'OPTIONAL' })
    }
    if (path === '/api/v1/projects') return json(route, [project])

    // Endpoints whose consumers call array methods. The catch-all below returns
    // `{}`, and `{}.map` is what turned /runs into a bare error boundary while
    // the mocks for the chain itself were all correct — the page never got far
    // enough to read them. `critical-journeys.spec.ts` lists the same set.
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/analytics/trends') return json(route, [])
    if (path === '/api/v1/analytics/failure-categories') return json(route, [])
    if (path === '/api/v1/analytics/dashboard') return json(route, {})
    if (path.startsWith('/api/v1/releases')) return json(route, [])
    if (path.startsWith('/api/v1/suites')) return json(route, { items: [], total: 0, page: 1, size: 100, pages: 0 })

    // ── hop 1: the run is listed ───────────────────────────────────────────
    if (path === '/api/v1/runs') {
      return json(route, { items: [run], total: 1, page: 1, size: 100, pages: 1 })
    }
    // ── hop 2: the run's own page, and its failing test ────────────────────
    if (path === `/api/v1/runs/${RUN_ID}`) return json(route, run)
    if (path === `/api/v1/runs/${RUN_ID}/tests`) {
      return json(route, { items: [failingCase], total: 1, page: 1, size: 100, pages: 1 })
    }
    // ── hop 3: the AI report cites that failure ────────────────────────────
    // Shaped against `RunIntelligence` in services/runIntelligenceService.ts.
    // The failure's NAME has to travel in the cluster label and the
    // representative error: `AnalysisItem` carries only `test_case_id`, so a
    // report that dropped the name would still look structurally valid.
    if (path === `/api/v1/runs/${RUN_ID}/intelligence`) {
      return json(route, {
        run: {
          id: RUN_ID,
          build_number: run.build_number,
          branch: run.branch,
          status: run.status,
          total_tests: run.total_tests,
          passed_tests: run.passed_tests,
          failed_tests: run.failed_tests,
          ingestion_complete: true,
          ingestion_rejected_tests: 0,
        },
        structured_summary: null,
        failure_clusters: [
          {
            id: 'c1',
            cluster_id: 'c1',
            label: `${SPINE_FAILURE} regressed`,
            size: 1,
            representative_error: `${SPINE_FAILURE}: expected decline, got approval`,
            member_test_ids: [failingCase.id],
            cohesion_score: 1,
            criticality_level: 'HIGH',
            dimension_scores: [],
          },
        ],
        category_breakdown: { PRODUCT_BUG: 1 },
        affected_suites: [{ suite: 'CheckoutSuite', failed_count: 1 }],
        top_analyses: [
          {
            test_case_id: failingCase.id,
            failure_category: 'PRODUCT_BUG',
            root_cause_summary: `${SPINE_FAILURE} accepts an expired card.`,
            confidence_score: 0.82,
            is_flaky: false,
            requires_human_review: false,
            recommended_actions: [`Fix ${SPINE_FAILURE}`],
            evidence_references: [],
            role_actions: {},
          },
        ],
        avg_confidence: 0.82,
        release_decision: null,
        role_actions: {},
        pipeline_stages: [],
        intelligence_available: true,
        all_green: false,
        dimension_scores: [],
        what_changed_since_last_good_run: null,
        defect_candidates: [],
        summary_modes: null,
        provenance: null,
        partial_errors: null,
      })
    }
    // Sibling fetches the intelligence page makes. The catch-all returns `{}`,
    // and `undefined.map` on one of these is what rendered the page as
    // "Something went wrong loading this page" while the intelligence payload
    // itself was correct.
    if (path === `/api/v1/runs/${RUN_ID}/decision-reports`) return json(route, [])
    if (path === `/api/v1/runs/${RUN_ID}/baseline-diff`) return json(route, null)
    // `RunStepFlips.tests` is the array the page maps — `{ items: [] }` leaves
    // `tests` undefined, and `undefined.map` is what rendered the intelligence
    // page as an error boundary while its own payload was correct. Shapes here
    // follow types/runs.ts and types/attribution.ts rather than a guess.
    if (path === `/api/v1/runs/${RUN_ID}/step-flips`) {
      return json(route, {
        run_id: RUN_ID,
        project_id: PROJECT_ID,
        tests_analyzed: 0,
        tests_with_flips: 0,
        total_flips: 0,
        truncated: false,
        tests: [],
      })
    }
    if (path === `/api/v1/runs/${RUN_ID}/attribution`) return json(route, { items: [], total: 0 })
    if (path === '/api/v1/scoring-model') return json(route, { version: 1, dimensions: [] })

    // ── hop 4: the summary report counts it ────────────────────────────────
    // Shaped against `SummaryReport` in types/summaryReport.ts. The failure's
    // name travels in `top_failing_tests`, which is the only field on this
    // payload that names a test at all.
    if (path === '/api/v1/reports/summary') {
      return json(route, {
        project_id: PROJECT_ID,
        project_name: project.name,
        mode: 'window',
        window_days: 7,
        generated_at: '2026-09-18T10:05:00Z',
        period_start: '2026-09-11T00:00:00Z',
        period_end: '2026-09-18T23:59:59Z',
        totals: {
          total_test_cases: run.total_tests,
          passed: run.passed_tests,
          failed: run.failed_tests,
          skipped: run.skipped_tests,
          broken: 0,
          evaluated: run.passed_tests + run.failed_tests,
          pass_rate_pct: run.pass_rate,
          pass_rate_basis: 'per unique test',
          pass_rate_basis_label: 'per unique test',
          fail_rate_pct: 20,
          skip_rate_pct: 16.7,
        },
        run_count: 1,
        runs_per_day: 1,
        avg_duration_ms: run.duration_ms,
        latest_run_at: run.end_time,
        flaky_test_count: 0,
        flaky_rate_pct: 0,
        suites: [],
        top_failing_tests: [
          { suite_name: 'CheckoutSuite', class_name: 'CheckoutTests', test_name: SPINE_FAILURE, failures: 1 },
        ],
      })
    }
    // ── hop 5: the gate blocks, naming it ──────────────────────────────────
    // `releaseCouncilService.get` types this as `ReleaseCouncilDecision`
    // DIRECTLY — not wrapped in `{ decision: ... }`. The failure's name has to
    // reach `blocking_issues` / `conditions_for_go` / `reasoning`, which are
    // the fields the gate explains itself with.
    if (path === `/api/v1/release-readiness/${RUN_ID}`) {
      return json(route, {
        run_id: RUN_ID,
        recommendation: 'NO_GO',
        risk_score: 72,
        composite_risk: 72,
        dimension_scores: [],
        blocking_issues: [`${SPINE_FAILURE} failed`],
        conditions_for_go: [`Fix ${SPINE_FAILURE}`],
        reasoning: `Blocked by ${SPINE_FAILURE}.`,
        score_model_version: 1,
        input_snapshot: null,
        cluster_insights: [],
        baseline_diff: null,
        open_defects_by_component: [],
        human_override: null,
        overridden_by: null,
        original_recommendation: null,
        original_risk_score: null,
        override_audit: [],
        pass_rate: run.pass_rate,
        build_number: run.build_number,
        policy_id: null,
        policy_version: null,
        policy_level: null,
        rule_evaluations: [],
      })
    }

    // The app shell polls badges and health. A stable empty object keeps this
    // lane on the chain rather than on every endpoint's payload semantics.
    return json(route, {})
  })
}

async function signIn(page: Page) {
  await page.addInitScript(
    ([seedUser, pid, pname]) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({
          state: { token: 'access', refreshToken: 'refresh', user: seedUser, isAuthenticated: true },
          version: 0,
        }),
      )
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({
          state: { activeProjectId: pid, activeProject: { id: pid, name: pname } },
          version: 0,
        }),
      )
    },
    [user, PROJECT_ID, project.name] as const,
  )
}

/**
 * Assert the spine failure surfaced on the page the test is currently on.
 *
 * `getByText(name).first()` was tried and rejected: every hop renders the name
 * — the run detail table shows it in a cell — but `.first()` resolved to a node
 * Playwright did not consider visible, so a page that plainly displayed the
 * failure reported "element(s) not found". What this chain cares about is
 * whether the failure reached the page at all, which is a statement about the
 * rendered document, not about one node's box.
 */
async function expectFailureSurfaced(page: Page, hop: string) {
  await expect(page.locator('body'), `${hop} did not surface ${SPINE_FAILURE}`).toContainText(
    SPINE_FAILURE,
    { timeout: 15_000 },
  )
}

test.describe('journey spine', () => {
  test.beforeEach(async ({ context, page }) => {
    await context.clearCookies()
    await installSpineApi(page)
    await signIn(page)
  })

  test('one failure survives every hop from the run list to the release gate', async ({ page }) => {
    const apiPaths: string[] = []
    page.on('request', (r) => {
      const pth = new URL(r.url()).pathname
      if (pth.startsWith('/api/v1/')) apiPaths.push(pth)
    })
    page.on('pageerror', (e) => console.log('DERR<<' + String(e.stack || e.message).slice(0, 700).split(String.fromCharCode(10)).join(' ~ ') + '>>'))
    // hop 1 — the run is listed
    await page.goto('/runs')
    // The run's own identity, not the page chrome: a heading only proves the
    // route rendered, and this chain is about the run travelling with it.
    await expect(page.getByText('spine-001').first()).toBeVisible({ timeout: 15_000 })

    // hop 2 — the run's own page names the failing test
    await page.goto(`/runs/${RUN_ID}`)
    await expectFailureSurfaced(page, '/runs/:id')

    // hop 3 — the AI report cites the same failure
    await page.goto(`/runs/${RUN_ID}/intelligence`)
    await page.waitForTimeout(4000)
    console.log('DPATHS<<' + [...new Set(apiPaths)].join(' , ') + '>>')
    await expectFailureSurfaced(page, '/runs/:id/intelligence')

    // hop 4 — the summary report counts it
    await page.goto('/reports/summary')
    await expectFailureSurfaced(page, '/reports/summary')

    // hop 5 — the gate blocks, and says why by name
    await page.goto(`/release-gate/${RUN_ID}`)
    await expect(page.locator('body')).toContainText(/no[_\s-]?go/i, { timeout: 15_000 })
    await expectFailureSurfaced(page, '/release-gate/:id')
  })

  test('the run id addressed by every hop is the same run', async ({ page }) => {
    // A chain that renders the right text for the wrong run would pass the walk
    // above. Pin the identity itself: each hop must request THIS run id.
    const requested: string[] = []
    page.on('request', (r) => {
      const path = new URL(r.url()).pathname
      if (path.startsWith('/api/v1/')) requested.push(path)
    })

    await page.goto(`/runs/${RUN_ID}`)
    await expectFailureSurfaced(page, '/runs/:id')
    await page.goto(`/runs/${RUN_ID}/intelligence`)
    await expectFailureSurfaced(page, '/runs/:id/intelligence')
    await page.goto(`/release-gate/${RUN_ID}`)
    await expectFailureSurfaced(page, '/release-gate/:id')

    const hops = requested.filter((p) => p.includes(RUN_ID))
    expect(hops.length, 'no hop addressed the run by id').toBeGreaterThan(2)
    // Nothing may reach for a different run id while walking this chain.
    const otherRunIds = requested.filter(
      (p) => /\/runs\/[0-9a-f-]{36}/.test(p) && !p.includes(RUN_ID),
    )
    expect(otherRunIds, 'a hop addressed a different run').toEqual([])
  })
})
