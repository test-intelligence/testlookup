import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Release-gate policy CRUD (/policies, /policies/:id) — list, the New Policy
 * editor, client-side validation, and the create round-trip. Endpoints under
 * /api/v1/release-gate-policies are mocked so the flow is deterministic.
 *
 * Reaches the editor via the list's "New Policy" button (the real user path),
 * which sidesteps direct /policies/new route ambiguity.
 */

const POLICY = {
  id: 'pol-e2e',
  project_id: null,
  version: 1,
  name: 'E2E Gate Policy',
  description: 'created by e2e',
  rules: {
    schema_version: 1,
    thresholds: { go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
    dimension_weights: {
      user_impact: 0.25, env_sensitivity: 0.10, reproducibility: 0.15, regression_likely: 0.20,
      hist_recurrence: 0.10, blast_radius: 0.15, diagnosis_conf: 0.05,
    },
    rules: [],
    pass_rate_bands: { orange_min: 90, yellow_min: 95, green_min: 99 },
    hard_caps: { max_p0_defects: 0, max_flaky_count: 10, max_new_failures_24h: 20 },
  },
  is_active: false,
  is_draft: true,
  created_by: 'admin',
  activated_by: null,
  activated_at: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: null,
};

async function openNewPolicyEditor(page: import('@playwright/test').Page) {
  await page.goto('/policies');
  await expect(page.getByRole('heading', { name: /release gate policies/i })).toBeVisible({ timeout: 10000 });
  await page.getByRole('button', { name: /new policy/i }).click();
  await expect(page.getByRole('heading', { name: /^new policy$/i })).toBeVisible({ timeout: 8000 });
}

test.describe('Release Gate Policies — CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    // List endpoint (with or without query) → empty to start.
    await mockJson(page, '**/api/v1/release-gate-policies*', []);
  });

  test('list renders and "New Policy" opens the editor', async ({ page }) => {
    await openNewPolicyEditor(page);
    // The editor exposes the metadata form.
    await expect(page.getByPlaceholder('Policy Name *')).toBeVisible();
    await expect(page.getByRole('button', { name: /save draft/i })).toBeVisible();
  });

  test('saving without a name is blocked by validation', async ({ page }) => {
    await openNewPolicyEditor(page);
    // Name is empty by default → Save Draft should toast the validation error
    // and NOT navigate away from the new-policy editor.
    await page.getByRole('button', { name: /save draft/i }).click();
    await expect(page.getByText('Name is required')).toBeVisible({ timeout: 8000 });
    await expect(page.getByRole('heading', { name: /^new policy$/i })).toBeVisible();
  });

  test('creating a policy with valid defaults posts and navigates to it', async ({ page }) => {
    let created = false;
    // POST create → return the saved policy (valid default weights sum to 1.0).
    await page.route('**/api/v1/release-gate-policies*', async (route) => {
      if (route.request().method() === 'POST') {
        created = true;
        await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(POLICY) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
    });
    // After create the app navigates to /policies/:id which re-fetches the policy.
    await mockJson(page, '**/api/v1/release-gate-policies/*', POLICY);

    await openNewPolicyEditor(page);
    await page.getByPlaceholder('Policy Name *').fill(POLICY.name);
    await page.getByRole('button', { name: /save draft/i }).click();

    await expect(page.getByText('Policy created')).toBeVisible({ timeout: 8000 });
    await expect(page).toHaveURL(/\/policies\/pol-e2e/, { timeout: 8000 });
    expect(created).toBe(true);
  });
});
