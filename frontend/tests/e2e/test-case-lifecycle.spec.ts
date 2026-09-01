import { expect, Page, test } from '@playwright/test';
import { mockJson, seedActiveProject } from './apiMock';
import { performRealLogin } from './realLoginHelper';

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };
const BASE_CASE = {
  id: 'managed-case-1', project_id: PROJECT.id, title: 'Governed sign in',
  objective: 'A user can sign in', test_type: 'functional', priority: 'high',
  severity: 'major', test_suite_id: null, version: 1, source: 'managed', is_automated: false,
  automation_status: 'manual', ai_generated: false,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
};
const ACTION_RESULT: Record<string, { status: string; allowed_actions: string[]; [key: string]: unknown }> = {
  request_review: { status: 'review_requested', allowed_actions: ['claim_review', 'withdraw_review'] },
  claim_review: { status: 'under_review', allowed_actions: ['unclaim', 'approve', 'reject', 'request_changes'] },
  request_changes: { status: 'draft', allowed_actions: ['request_review', 'deprecate'] },
  approve: { status: 'approved', allowed_actions: ['activate', 'deprecate'], approved_at: '2026-09-01T00:01:00Z' },
  activate: { status: 'active', allowed_actions: ['flag_stale', 'deprecate'] },
  flag_stale: { status: 'needs_update', allowed_actions: ['revise', 'deprecate'], needs_update_reason: 'automation_vanished' },
  revise: { status: 'draft', allowed_actions: ['request_review', 'deprecate'], needs_update_reason: null },
  deprecate: { status: 'deprecated', allowed_actions: ['reinstate', 'archive'], deprecated_at: '2026-09-01T00:02:00Z' },
  reinstate: { status: 'draft', allowed_actions: ['request_review', 'deprecate'], deprecated_at: null, deprecation_reason: null },
  archive: { status: 'archived', allowed_actions: [], archived_at: '2026-09-01T00:03:00Z' },
};

async function mockLifecycle(
  page: Page,
  transitionBodies: Record<string, unknown>[],
  lifecycleV2 = true,
) {
  let current = { ...BASE_CASE, status: 'draft', allowed_actions: ['request_review', 'deprecate'] };
  await mockJson(page, '**/api/v1/feature-flags/test_case_lifecycle_v2/status*', {
    key: 'test_case_lifecycle_v2', enabled: lifecycleV2,
  });
  await page.route('**/api/v1/test-management/cases*', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [current], total: 1, page: 1, size: 25, pages: 1 }) });
  });
  await mockJson(page, '**/api/v1/test-management/audit*', { items: [], total: 0, page: 1, pages: 0 });
  await mockJson(page, '**/api/v1/test-management/cases/evidence-gaps*', { items: [], total: 0 });
  await mockJson(page, '**/api/v1/canonical-test-cases/orphaned*', { items: [], total: 0 });
  await page.route('**/api/v1/test-management/cases/managed-case-1/allowed-transitions', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current.allowed_actions.map((action) => ({ action, allowed: true }))) });
  });
  await page.route('**/api/v1/test-management/cases/managed-case-1/transition', async (route) => {
    const body = route.request().postDataJSON() as { action: string };
    transitionBodies.push(body);
    const next = ACTION_RESULT[body.action];
    if (!next) {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'Unexpected transition' }) });
      return;
    }
    current = { ...current, ...next, updated_at: '2026-09-01T00:01:00Z' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current) });
  });
}

async function openLifecycle(page: Page) {
  await page.goto('/test-management');
  await page.getByText('Governed sign in', { exact: true }).first().click();
  await page.getByRole('button', { name: 'Lifecycle' }).click();
  const lifecycle = page.getByLabel('Test case lifecycle');
  await expect(lifecycle).toBeVisible();
  await expect(lifecycle.getByText('draft', { exact: true })).toBeVisible();
  return lifecycle;
}

async function submitReason(page: Page, action: string, reason: string, confirmLabel = action) {
  const dialog = page.getByRole('dialog', { name: action });
  const confirm = dialog.getByRole('button', { name: confirmLabel });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole('textbox').fill(`  ${reason}  `);
  await confirm.click();
}

test.describe('Test-case lifecycle governance', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
  });

  test('covers review, staleness, revision, reinstatement and archival with audit inputs', async ({ page }) => {
    const transitionBodies: Record<string, unknown>[] = [];
    await mockLifecycle(page, transitionBodies);
    const lifecycle = await openLifecycle(page);

    await lifecycle.getByRole('button', { name: 'Request review' }).click();
    await expect(lifecycle.getByText('review requested', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Claim review' }).click();
    await expect(lifecycle.getByText('under review', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Request changes' }).click();
    await submitReason(page, 'Request changes', 'Clarify the expected error state');
    await expect(lifecycle.getByText('draft', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Request review' }).click();
    await lifecycle.getByRole('button', { name: 'Claim review' }).click();
    await lifecycle.getByRole('button', { name: 'Approve' }).click();
    await submitReason(page, 'Approve', 'Independently verified expected behavior');
    await expect(lifecycle.getByText('approved', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Activate' }).click();
    await expect(lifecycle.getByText('active', { exact: true })).toBeVisible();

    await lifecycle.getByRole('button', { name: 'Flag stale' }).click();
    await submitReason(page, 'Flag stale', 'automation_vanished');
    await expect(lifecycle.getByText('needs update', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Revise' }).click();
    await expect(lifecycle.getByText('draft', { exact: true })).toBeVisible();

    await lifecycle.getByRole('button', { name: 'Deprecate' }).click();
    await submitReason(page, 'Deprecate', 'Superseded by consolidated sign-in case');
    await expect(lifecycle.getByText('deprecated', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Reinstate' }).click();
    await submitReason(page, 'Reinstate', 'Consolidated case was withdrawn');
    await expect(lifecycle.getByText('draft', { exact: true })).toBeVisible();
    await lifecycle.getByRole('button', { name: 'Deprecate' }).click();
    await submitReason(page, 'Deprecate', 'No longer part of supported behavior');
    await lifecycle.getByRole('button', { name: 'Archive' }).click();
    await submitReason(page, 'Archive', 'Retention period completed');
    await expect(lifecycle.getByText('archived', { exact: true })).toBeVisible();

    expect(transitionBodies).toEqual([
      { action: 'request_review' },
      { action: 'claim_review' },
      { action: 'request_changes', notes: 'Clarify the expected error state' },
      { action: 'request_review' },
      { action: 'claim_review' },
      { action: 'approve', notes: 'Independently verified expected behavior' },
      { action: 'activate' },
      { action: 'flag_stale', reason: 'automation_vanished' },
      { action: 'revise' },
      { action: 'deprecate', reason: 'Superseded by consolidated sign-in case' },
      { action: 'reinstate', reason: 'Consolidated case was withdrawn' },
      { action: 'deprecate', reason: 'No longer part of supported behavior' },
      { action: 'archive', reason: 'Retention period completed' },
    ]);
  });

  test('surfaces transition conflicts and retains the current lifecycle state', async ({ page }) => {
    const transitionBodies: Record<string, unknown>[] = [];
    await mockLifecycle(page, transitionBodies);
    await page.route('**/api/v1/test-management/cases/managed-case-1/transition', async (route) => {
      transitionBodies.push(route.request().postDataJSON());
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: { current_state: 'draft', attempted_action: 'request_review', message: 'Case changed in another session' } }),
      });
    });
    const lifecycle = await openLifecycle(page);

    await lifecycle.getByRole('button', { name: 'Request review' }).click();

    await expect(lifecycle.getByRole('alert')).toHaveText('Case changed in another session');
    await expect(lifecycle.getByText('draft', { exact: true })).toBeVisible();
    expect(transitionBodies).toEqual([{ action: 'request_review' }]);
  });

  test('renders server authorization decisions fail-closed', async ({ page }) => {
    await mockLifecycle(page, []);
    await page.route('**/api/v1/test-management/cases/managed-case-1/allowed-transitions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ action: 'request_review', allowed: false, blocked_reason: 'Insufficient permissions' }]),
      });
    });
    const lifecycle = await openLifecycle(page);

    const requestReview = lifecycle.getByRole('button', { name: 'Request review' });
    await expect(requestReview).toBeDisabled();
    await expect(requestReview).toHaveAttribute('title', 'Insufficient permissions');
  });

  test('preserves reasoned legacy request-review and delete shims when v2 is disabled', async ({ page }) => {
    const transitionBodies: Record<string, unknown>[] = [];
    await mockLifecycle(page, transitionBodies, false);
    let legacyDeleteBody: unknown;
    let legacyReviewRequests = 0;
    await page.route('**/api/v1/test-management/cases/managed-case-1', async (route) => {
      if (route.request().method() === 'DELETE') {
        legacyDeleteBody = route.request().postDataJSON();
        await route.fulfill({ status: 204, body: '' });
        return;
      }
      await route.fallback();
    });
    await page.route('**/api/v1/test-management/cases/managed-case-1/request-review', async (route) => {
      legacyReviewRequests += 1;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'review-1' }) });
    });
    await page.goto('/test-management');

    await expect(page.getByRole('button', { name: 'Lifecycle' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Delete Governed sign in' }).click();
    await submitReason(page, 'Delete test case', 'Legacy compatibility cleanup', 'Delete');
    expect(legacyDeleteBody).toEqual({ reason: 'Legacy compatibility cleanup' });

    await page.getByText('Governed sign in', { exact: true }).first().click();
    await page.getByRole('button', { name: 'Request Review' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: 'Review requested' }),
    ).toBeVisible();
    expect(legacyReviewRequests).toBe(1);
    expect(transitionBodies).toEqual([]);
  });
});
