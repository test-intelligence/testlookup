import { expect, test } from '@playwright/test';
import { mockJson, seedActiveProject } from './apiMock';
import { performRealLogin } from './realLoginHelper';

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

test.describe('Test Management', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockJson(page, '**/api/v1/test-management/cases*', { items: [], total: 0, page: 1, pages: 0 });
    await mockJson(page, '**/api/v1/test-management/cases/evidence-gaps*', { items: [], total: 0 });
    await mockJson(page, '**/api/v1/canonical-test-cases/orphaned*', { items: [], total: 0 });
    await mockJson(page, '**/api/v1/test-management/audit*', { items: [], total: 0, page: 1, pages: 0 });
  });

  test('renders the managed catalog and opens creation without fail-open assertions', async ({ page }) => {
    await page.goto('/test-management');

    await expect(page.getByRole('heading', { name: 'Test Case Management' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /New Test Case/i })).toHaveCount(2);
    await page.getByRole('button', { name: 'New test case from catalog toolbar' }).click();
    await expect(page.getByRole('dialog', { name: 'New Test Case' })).toBeVisible();
  });
});
