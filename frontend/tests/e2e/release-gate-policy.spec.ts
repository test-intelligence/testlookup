import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Release-gate policy *application* (/release-gate/:runId) — the downstream half
 * of the policy CRUD covered in policies.spec.ts. Once a policy exists, its
 * evaluation surfaces on the gate decision: the "Evaluated with …" policy badge
 * (level + version) and the per-rule "Policy Rules Evaluated" breakdown.
 *
 * The decision is served by GET /api/v1/release-readiness/:runId (see
 * releaseCouncilService); we mock it so the flow is deterministic and does not
 * depend on a deep-investigation pass having run against live data.
 */

const RUN_ID = '11111111-1111-4111-8111-111111111111';

/** A graded decision evaluated under a project-level policy (v3). */
function decision(overrides: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    recommendation: 'CONDITIONAL_GO',
    risk_score: 45,
    composite_risk: 45,
    dimension_scores: [],
    blocking_issues: [],
    conditions_for_go: ['Resolve 2 flaky tests before release'],
    reasoning: 'Pass rate within band but flaky count is near the policy cap.',
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
    pass_rate: 93.5,
    build_number: '1042',
    policy_id: 'pol-e2e',
    policy_version: 3,
    policy_level: 'project',
    rule_evaluations: [
      {
        rule_id: 'r1',
        rule_name: 'Max P0 defects',
        rule_type: 'hard_cap',
        passed: true,
        action: 'ALLOW',
        message: '0 of max 0',
        actual_value: 0,
        threshold_value: 0,
      },
      {
        rule_id: 'r2',
        rule_name: 'Pass rate floor',
        rule_type: 'threshold',
        passed: false,
        action: 'BLOCK',
        message: '93.5% below 95% floor',
        actual_value: 93.5,
        threshold_value: 95,
      },
    ],
    synthesized: false,
    ...overrides,
  };
}

test.describe('Release Gate — policy application', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    // AI config + recent-runs are read on mount; keep them deterministic.
    await mockJson(page, '**/api/v1/settings/ai', { analysis_mode: 'auto', ml_model_available: false });
    await mockJson(page, '**/api/v1/runs*', { items: [], total: 0, page: 1, size: 1, pages: 0 });
  });

  test('a project policy decision shows the policy badge and per-rule breakdown', async ({ page }) => {
    await mockJson(page, `**/api/v1/release-readiness/${RUN_ID}`, decision());

    await page.goto(`/release-gate/${RUN_ID}`);

    // Recommendation banner reflects the graded verdict.
    await expect(page.getByText('CONDITIONAL GO')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Pass rate: 93\.5%/)).toBeVisible();

    // Policy badge — level + version are surfaced (ENT-02).
    await expect(page.getByText(/project policy/i)).toBeVisible();
    await expect(page.getByText(/v3/)).toBeVisible();

    // Per-rule evaluations render with the failing rule's message.
    await expect(page.getByRole('heading', { name: /policy rules evaluated/i })).toBeVisible();
    await expect(page.getByText('Pass rate floor')).toBeVisible();
    await expect(page.getByText(/93\.5% below 95% floor/)).toBeVisible();
    await expect(page.getByText('Max P0 defects')).toBeVisible();
  });

  test('a hardcoded-fallback decision labels the policy as system defaults', async ({ page }) => {
    // No ReleaseGatePolicy row → backend falls back to hardcoded thresholds
    // (Known Pitfall #12). The badge must say "system defaults", not "project".
    await mockJson(
      page,
      `**/api/v1/release-readiness/${RUN_ID}`,
      decision({ policy_level: 'hardcoded', policy_version: null, rule_evaluations: [] }),
    );

    await page.goto(`/release-gate/${RUN_ID}`);

    await expect(page.getByText('CONDITIONAL GO')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/system defaults/i)).toBeVisible();
    // With no rule evaluations there is no per-rule breakdown card.
    await expect(page.getByRole('heading', { name: /policy rules evaluated/i })).toHaveCount(0);
  });
});
