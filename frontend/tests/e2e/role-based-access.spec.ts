import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Role-based access control (Tier-3). The ManagementGuard in App.tsx redirects
 * any role below QA_LEAD away from the management routes (projects, releases,
 * users, settings/*, policies, ownership) to /overview; usePermissions derives
 * the role from the user returned by GET /api/v1/auth/me.
 *
 * Rather than mint per-role tokens via dev-login (which depends on the backend
 * honouring ?role=), we mock /auth/me so the session's role is deterministic.
 * fetchUser persists the role, so subsequent navigations evaluate the guard
 * against it. performRealLogin guarantees the storageState token is present
 * across browsers; the mock overrides whatever role that token carried.
 */

const MANAGEMENT_ROUTES = ['/users', '/settings', '/projects'];

async function mockRole(page: Page, role: string): Promise<void> {
  await mockJson(page, '**/api/v1/auth/me', {
    id: '00000000-0000-4000-8000-0000000000aa',
    email: 'roletest@example.com',
    username: 'roletest',
    full_name: 'Role Test',
    role,
    is_active: true,
    must_change_password: false,
    avatar_color: 'blue',
  });
}

test.describe('Role-based access control', () => {
  test('VIEWER is redirected from management routes but can use normal pages', async ({ page }) => {
    await mockRole(page, 'VIEWER');
    await performRealLogin(page);

    for (const route of MANAGEMENT_ROUTES) {
      await page.goto(route);
      await expect(page, `${route} should redirect a VIEWER`).toHaveURL(/\/overview/, { timeout: 8000 });
    }

    // A non-management route stays put for any authenticated role.
    await page.goto('/runs');
    await expect(page).toHaveURL(/\/runs/, { timeout: 8000 });
  });

  test('QA_ENGINEER (below QA_LEAD) is still redirected from management routes', async ({ page }) => {
    await mockRole(page, 'QA_ENGINEER');
    await performRealLogin(page);

    await page.goto('/projects');
    await expect(page).toHaveURL(/\/overview/, { timeout: 8000 });

    await page.goto('/users');
    await expect(page).toHaveURL(/\/overview/, { timeout: 8000 });
  });

  test('QA_LEAD can reach the management routes', async ({ page }) => {
    await mockRole(page, 'QA_LEAD');
    await performRealLogin(page);

    // The guard admits QA_LEAD — the route renders instead of redirecting.
    await page.goto('/users');
    await expect(page).toHaveURL(/\/users$/, { timeout: 8000 });
    await expect(page).not.toHaveURL(/\/overview/);

    await page.goto('/projects');
    await expect(page).toHaveURL(/\/projects$/, { timeout: 8000 });
  });
});
