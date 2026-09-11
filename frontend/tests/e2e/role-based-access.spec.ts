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

/** Exercise a guarded route through a React Router-compatible history entry.
 *
 * A full document navigation races the ManagementGuard's immediate redirect
 * in Firefox and can surface as NS_BINDING_ABORTED or NS_ERROR_FAILURE. These
 * tests target that client-side guard. Add the history metadata React Router
 * expects, then dispatch a synthetic POP so the router evaluates the route;
 * the resulting URL remains the independent authorization assertion.
 */
async function navigateInApp(page: Page, route: string): Promise<void> {
  await page.evaluate((nextRoute) => {
    const currentState = window.history.state as { idx?: unknown } | null;
    const currentIndex = typeof currentState?.idx === 'number' ? currentState.idx : -1;
    const nextState = {
      usr: null,
      key: Math.random().toString(36).substring(2, 10),
      idx: currentIndex + 1,
    };

    window.history.pushState(nextState, '', nextRoute);
    window.dispatchEvent(new PopStateEvent('popstate', { state: nextState }));
  }, route);
}

/**
 * Log in for REAL first, then assume the role.
 *
 * The order matters. performRealLogin trusts a 200 from /auth/me as proof the
 * stored token is live. With the mock installed first, that 200 is the mock's,
 * so a token revoked earlier in the run (auth-flows' sign-out calls the server
 * logout, which revokes the token's jti and the user's refresh family) passed
 * as a session; the first real API call then 401'd, the refresh was refused,
 * and every test here landed on /login. Alone, the token was fresh and all
 * three passed (E1). After a real login the token is known-good; the reload
 * makes fetchUser pick up the mocked role.
 */
async function loginAs(page: Page, role: string): Promise<void> {
  await performRealLogin(page);
  await mockRole(page, role);
  await page.goto('/overview');
  await page.locator('aside').waitFor({ state: 'visible', timeout: 10000 });
}

test.describe('Role-based access control', () => {
  test('VIEWER is redirected from management routes but can use normal pages', async ({ page }) => {
    await loginAs(page, 'VIEWER');

    for (const route of MANAGEMENT_ROUTES) {
      await navigateInApp(page, route);
      await expect(page, `${route} should redirect a VIEWER`).toHaveURL(/\/overview/, { timeout: 8000 });
    }

    // A real Router link to a non-management route stays available to every
    // authenticated role and proves the router rendered the destination.
    await page.getByRole('link', { name: 'Testing', exact: true }).click();
    await expect(page).toHaveURL(/\/runs/, { timeout: 8000 });
    await expect(page.getByRole('heading', { name: 'Test Runs', exact: true })).toBeVisible({
      timeout: 10000,
    });
  });

  test('QA_ENGINEER (below QA_LEAD) is still redirected from management routes', async ({ page }) => {
    await loginAs(page, 'QA_ENGINEER');

    await navigateInApp(page, '/projects');
    await expect(page).toHaveURL(/\/overview/, { timeout: 8000 });

    await navigateInApp(page, '/users');
    await expect(page).toHaveURL(/\/overview/, { timeout: 8000 });
  });

  test('QA_LEAD can reach the management routes', async ({ page }) => {
    await loginAs(page, 'QA_LEAD');

    // The guard admits QA_LEAD — the route renders instead of redirecting.
    await page.goto('/users');
    await expect(page).toHaveURL(/\/users$/, { timeout: 8000 });
    await expect(page).not.toHaveURL(/\/overview/);

    await page.goto('/projects');
    await expect(page).toHaveURL(/\/projects$/, { timeout: 8000 });
  });
});
