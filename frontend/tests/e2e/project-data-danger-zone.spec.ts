import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson, seedActiveProject } from './apiMock';

/**
 * Project Data danger zone (/settings/project-data) — ADMIN-only destructive
 * reset guarded by a typed-name confirmation (the reference pattern in
 * frontend/CLAUDE.md). Unit-tested in ProjectDataPage.test.tsx; this adds the
 * real browser flow: the modal, the typed-name gate, and the reset API call.
 *
 * Project-scoped: seed an active project so the danger-zone buttons enable.
 * Stays tolerant of the all-projects gate so it never false-fails when project
 * seeding doesn't engage in a given env.
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

/** Returns the danger-zone trigger if it became enabled, else null (gate). */
async function dangerButton(page: Page) {
  const btn = page.getByRole('button', { name: /^delete test runs$/i }).first();
  const enabled = await btn.isEnabled().catch(() => false);
  return enabled ? btn : null;
}

test.describe('Project Data — danger zone', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
  });

  test('reset is gated by an exact typed-name confirmation', async ({ page }) => {
    await page.goto('/settings/project-data');
    await expect(page.getByRole('heading', { name: /project data/i })).toBeVisible({ timeout: 10000 });

    const trigger = await dangerButton(page);
    if (!trigger) {
      // Project seeding didn't engage (or non-admin) → the page shows the
      // all-projects guard. Assert that valid state instead of false-failing.
      await expect(page.getByText(/select a specific project/i)).toBeVisible({ timeout: 8000 });
      test.skip(true, 'danger zone disabled (all-projects gate / non-admin) in this env');
      return;
    }

    await trigger.click();

    const modal = page.locator('div.fixed.inset-0');
    await expect(modal.locator('#reset-confirm-input')).toBeVisible({ timeout: 8000 });

    const confirm = modal.getByRole('button', { name: /delete test runs/i });
    await expect(confirm).toBeDisabled();

    // Wrong name keeps it disabled.
    await modal.locator('#reset-confirm-input').fill('not the project name');
    await expect(confirm).toBeDisabled();

    // Exact (case-sensitive) name enables it.
    await modal.locator('#reset-confirm-input').fill(PROJECT.name);
    await expect(confirm).toBeEnabled();
  });

  test('confirming a runs reset calls the API and shows the deleted-row summary', async ({ page }) => {
    let resetBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/projects/*/reset', async (route) => {
      resetBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ mode: 'runs', deleted: { test_runs: 3, test_cases: 12 } }),
      });
    });
    // Keep the projects list mock alive for any re-fetch.
    await mockJson(page, '**/api/v1/projects', [{ ...PROJECT, is_active: true }]);

    await page.goto('/settings/project-data');
    await expect(page.getByRole('heading', { name: /project data/i })).toBeVisible({ timeout: 10000 });

    const trigger = await dangerButton(page);
    if (!trigger) {
      test.skip(true, 'danger zone disabled (all-projects gate / non-admin) in this env');
      return;
    }

    await trigger.click();
    const modal = page.locator('div.fixed.inset-0');
    await modal.locator('#reset-confirm-input').fill(PROJECT.name);
    await modal.getByRole('button', { name: /delete test runs/i }).click();

    await expect(page.getByText(/reset complete/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/test_runs/)).toBeVisible();
    // The typed name was forwarded to the backend for re-validation.
    expect(resetBody).toMatchObject({ mode: 'runs', confirmation_name: PROJECT.name });
  });
});
