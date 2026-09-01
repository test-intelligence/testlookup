import { expect, Page, test } from '@playwright/test';
import { mockJson, seedActiveProject } from './apiMock';
import { performRealLogin } from './realLoginHelper';

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };
const AUTOMATION_CASE = {
  id: 'execution-case-1', project_id: PROJECT.id, canonical_test_case_id: 'canonical-1',
  title: 'Automation checkout', test_type: 'automation', priority: 'medium', severity: 'major',
  test_suite_id: 'suite-1', status: 'active', version: 1, source: 'automation', is_automated: true,
  automation_status: 'automated', ai_generated: false,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
};
const MANAGED_CASE = {
  ...AUTOMATION_CASE, id: 'managed-case-1', status: 'draft', source: 'linked',
  allowed_actions: ['request_review', 'deprecate'],
};

const CANONICAL_CASE = {
  id: 'canonical-1', project_id: PROJECT.id, test_suite_id: 'suite-1',
  test_suite_name: 'Regression', test_fingerprint: 'fingerprint-1',
  test_name: 'Automation checkout', class_name: 'checkout.AutomationTest',
  status: 'active', source: 'linked', first_seen_run_id: 'run-1',
  last_seen_run_id: 'run-1', last_seen_test_case_id: 'execution-case-1',
  deleted_at_run_id: null, deleted_observed_at: null,
  managed_test_case_id: 'managed-case-1', review_tag: null, tags: null,
  run_count: 1, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
};

async function mockPromotionSurface(
  page: Page,
  onPromote: (request: { url: string; method: string; body: unknown }) => void,
) {
  let promoted = false;
  await page.route('**/api/v1/test-management/cases*', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: [promoted ? MANAGED_CASE : AUTOMATION_CASE], total: 1, page: 1, pages: 1 }),
    });
  });
  await mockJson(page, '**/api/v1/test-management/audit*', { items: [], total: 0, page: 1, pages: 0 });
  await mockJson(page, '**/api/v1/test-management/cases/evidence-gaps*', { items: [], total: 0 });
  await mockJson(page, '**/api/v1/canonical-test-cases/orphaned*', { items: [], total: 0 });
  await mockJson(page, '**/api/v1/suites', { items: [], total: 0 });
  await page.route('**/api/v1/canonical-test-cases/canonical-1/promote', async (route) => {
    onPromote({
      url: route.request().url(),
      method: route.request().method(),
      body: route.request().postDataJSON(),
    });
    promoted = true;
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        canonical: { id: 'canonical-1', managed_test_case_id: MANAGED_CASE.id, source: 'linked' },
        managed_case: MANAGED_CASE,
      }),
    });
  });
}

async function mockCanonicalDetail(page: Page) {
  await mockJson(page, '**/api/v1/canonical-test-cases/canonical-1', CANONICAL_CASE);
  await mockJson(page, '**/api/v1/canonical-test-cases/canonical-1/runs*', { items: [], total: 0 });
  await mockJson(page, '**/api/v1/suites/suite-1', { id: 'suite-1', project_id: PROJECT.id, name: 'Regression' });
  await mockJson(page, '**/api/v1/suites', { items: [], total: 0 });
}

async function assumeRole(page: Page, role: string) {
  const user = {
    id: 'role-user', email: 'role@example.test', username: 'role-user',
    full_name: 'Role User', role, is_active: true, must_change_password: false,
    avatar_color: null,
  };
  await page.addInitScript(({ nextRoleUser }) => {
    const raw = localStorage.getItem('auth-storage');
    if (!raw) return;
    const persisted = JSON.parse(raw);
    persisted.state.user = nextRoleUser;
    persisted.state.isAuthenticated = true;
    localStorage.setItem('auth-storage', JSON.stringify(persisted));
  }, { nextRoleUser: user });
  await mockJson(page, '**/api/v1/auth/me', user);
}

test.describe('Automation promotion', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
  });

  test('hides delete for automation and promotes only by canonical identity', async ({ page }) => {
    let promoteUrl = '';
    let promoteMethod = '';
    let promoteBody: unknown;
    await mockPromotionSurface(page, (request) => {
      promoteUrl = request.url;
      promoteMethod = request.method;
      promoteBody = request.body;
    });
    await page.goto('/test-management');

    const row = page.getByRole('row').filter({ hasText: 'Automation checkout' });
    await expect(row.getByRole('button', { name: 'Promote to managed case' })).toBeVisible();
    await expect(row.getByRole('button', { name: /Deprecate Automation checkout/ })).toHaveCount(0);
    await row.getByRole('button', { name: 'Promote to managed case' }).click();

    await expect(page.getByText('Automation test promoted to a managed draft')).toBeVisible();
    expect(new URL(promoteUrl).pathname).toBe('/api/v1/canonical-test-cases/canonical-1/promote');
    expect(promoteMethod).toBe('POST');
    expect(promoteBody).toEqual({});
  });

  test('disables promotion when no canonical identity was measured', async ({ page }) => {
    await mockPromotionSurface(page, () => {});
    await page.route('**/api/v1/test-management/cases*', async (route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: [{ ...AUTOMATION_CASE, canonical_test_case_id: null }], total: 1, page: 1, pages: 1 }),
      });
    });
    await mockJson(page, '**/api/v1/test-management/cases/evidence-gaps*', { items: [], total: 0 });
    await page.goto('/test-management');

    const promote = page.getByRole('row').filter({ hasText: 'Automation checkout' }).getByRole('button', { name: 'Promote to managed case' });
    await expect(promote).toBeDisabled();
    await expect(promote).toHaveAttribute('title', /no canonical test identity/i);
  });

  test('surfaces promotion failures without changing the automation row', async ({ page }) => {
    await mockPromotionSurface(page, () => {});
    await page.route('**/api/v1/canonical-test-cases/canonical-1/promote', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Canonical identity was already linked elsewhere' }),
      });
    });
    await page.goto('/test-management');

    const row = page.getByRole('row').filter({ hasText: 'Automation checkout' });
    await row.getByRole('button', { name: 'Promote to managed case' }).click();

    await expect(row.getByRole('alert')).toHaveText('Canonical identity was already linked elsewhere');
    await expect(row.getByRole('button', { name: 'Promote to managed case' })).toBeEnabled();
  });

  test('requires and sends an unlink reason, retaining the dialog after a conflict', async ({ page }) => {
    await mockCanonicalDetail(page);
    const unlinkBodies: unknown[] = [];
    let attempts = 0;
    await page.route('**/api/v1/canonical-test-cases/canonical-1/managed-link', async (route) => {
      unlinkBodies.push(route.request().postDataJSON());
      attempts += 1;
      if (attempts === 1) {
        await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'Link changed in another session' }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...CANONICAL_CASE, managed_test_case_id: null }) });
    });
    await page.goto('/canonical-test-cases/canonical-1');

    await page.getByRole('button', { name: 'Unlink managed case' }).click();
    const dialog = page.getByRole('dialog', { name: 'Unlink managed case' });
    const confirm = dialog.getByRole('button', { name: 'Unlink' });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole('textbox').fill('Incorrect automation mapping');
    await confirm.click();
    await expect(page.getByText('Could not remove the managed case link')).toBeVisible();
    await expect(dialog).toBeVisible();
    await confirm.click();
    await expect(dialog).toHaveCount(0);

    expect(unlinkBodies).toEqual([
      { reason: 'Incorrect automation mapping' },
      { reason: 'Incorrect automation mapping' },
    ]);
  });

  test('requires retirement proof, sends its body and displays mutation failure', async ({ page }) => {
    await mockPromotionSurface(page, () => {});
    await mockJson(page, '**/api/v1/canonical-test-cases/orphaned*', {
      items: [{ ...CANONICAL_CASE, status: 'deleted', deleted_observed_at: '2026-08-31T00:00:00Z' }],
      total: 1,
    });
    await page.route('**/api/v1/canonical-test-cases/canonical-1/confirm-retirement', async (route) => {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'A newer execution restored this test' }) });
    });
    await page.goto('/test-management');

    await page.getByRole('button', { name: /Unconfirmed automation retirements/ }).click();
    await page.getByRole('button', { name: 'Confirm retirement' }).click();
    const dialog = page.getByRole('dialog', { name: 'Confirm automation retirement' });
    const confirm = dialog.getByRole('button', { name: 'Confirm retirement' });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole('textbox').fill('Removed intentionally from the source suite');
    const retirementRequest = page.waitForRequest((request) => (
      request.method() === 'POST'
      && new URL(request.url()).pathname === '/api/v1/canonical-test-cases/canonical-1/confirm-retirement'
    ));
    await confirm.click();

    expect((await retirementRequest).postDataJSON()).toEqual({ reason: 'Removed intentionally from the source suite' });
    await expect(page.getByRole('alert').filter({ hasText: 'A newer execution restored this test' })).toBeVisible();
    await expect(dialog).toBeVisible();
  });

  test('hides promotion controls below QA engineer', async ({ page }) => {
    await assumeRole(page, 'TESTER');
    await mockPromotionSurface(page, () => {});
    await page.goto('/test-management');

    const row = page.getByRole('row').filter({ hasText: 'Automation checkout' });
    await expect(row.getByRole('button', { name: 'Promote to managed case' })).toHaveCount(0);
  });
});
