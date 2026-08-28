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

/** Navigate somewhere the app is expected to bounce us out of.
 *
 * Firefox reports NS_BINDING_ABORTED when a client-side redirect fires while
 * the document load is still in flight -- which is exactly what these tests
 * provoke, so the redirect under test was failing the navigation that
 * triggered it. Chromium tolerates it, so this only ever failed on Firefox.
 *
 * The aborted load is not the assertion; the resulting URL is. Swallow the
 * abort and let the toHaveURL check that follows decide the verdict -- it
 * still fails if the redirect does not happen.
 */
const NAVIGATION_ABORTED =
  /NS_BINDING_ABORTED|net::ERR_ABORTED|Frame load interrupted|NS_ERROR_ABORT/i;

async function gotoTolerantOfRedirect(page: import('@playwright/test').Page, route: string) {
  await page.goto(route, { waitUntil: 'commit' }).catch((err: Error) => {
    if (!NAVIGATION_ABORTED.test(err.message)) throw err;
  });
}

test.describe('Role-based access control', () => {
  test('VIEWER is redirected from management routes but can use normal pages', async ({ page }) => {
    await mockRole(page, 'VIEWER');
    await performRealLogin(page);

    for (const route of MANAGEMENT_ROUTES) {
      await gotoTolerantOfRedirect(page, route);
      await expect(page, `${route} should redirect a VIEWER`).toHaveURL(/\/overview/, { timeout: 8000 });
    }

    // A non-management route stays put for any authenticated role. This one
    // aborts too -- not because IT redirects, but because the previous
    // redirect is still settling when it starts. The toHaveURL below is the
    // real check either way: if /runs bounced, it fails.
    await gotoTolerantOfRedirect(page, '/runs');
    await expect(page).toHaveURL(/\/runs/, { timeout: 8000 });
  });

  test('QA_ENGINEER (below QA_LEAD) is still redirected from management routes', async ({ page }) => {
    await mockRole(page, 'QA_ENGINEER');
    await performRealLogin(page);

    await gotoTolerantOfRedirect(page, '/projects');
    await expect(page).toHaveURL(/\/overview/, { timeout: 8000 });

    await gotoTolerantOfRedirect(page, '/users');
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
