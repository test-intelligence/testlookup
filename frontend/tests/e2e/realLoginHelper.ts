import { Page } from '@playwright/test';

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Ensures the page is authenticated before each test.
 *
 * Strategy:
 * 1. Navigate to /overview with the globalSetup storageState already injected.
 * 2. Wait briefly for both the authenticated sidebar (<aside>) and a successful
 *    /auth/me response.
 *    - If both appear: auth is confirmed, return immediately (fast path).
 *    - If it doesn't appear (Firefox HTTP storageState bug, or backend down/slow
 *      causing fetchUser → logout): fall through to a token-based re-auth.
 * 3. Token re-auth: call the dev-login endpoint and inject the JWT into
 *    localStorage directly (no credentials required, no password in tests).
 *    Falls back to E2E_ADMIN_PASSWORD env var if dev-login is unavailable.
 */
export async function performRealLogin(page: Page) {
  // ── Fast path: storageState ──────────────────────────────────────────────
  // The persisted store can render <aside> before ProtectedRoute's background
  // token verification settles. Returning on the sidebar alone lets an expired
  // token redirect race the caller's next page.goto, which Firefox reports as
  // NS_BINDING_ABORTED. Arm the response waiter before navigation and require
  // the verified response as the fast-path boundary.
  const verifiedAuth = page.waitForResponse(
    response => response.url().includes('/api/v1/auth/me') && response.ok(),
    { timeout: 6000 },
  ).then(() => true).catch(() => false);
  await page.goto('/overview');

  const [authenticated, tokenVerified] = await Promise.all([
    page.locator('aside')
      .waitFor({ state: 'visible', timeout: 6000 })
      .then(() => true)
      .catch(() => false),
    verifiedAuth,
  ]);

  if (authenticated && tokenVerified) return;

  // ── Fallback 1: dev-login endpoint (no credentials) ──────────────────────
  try {
    const resp = await page.request.post(`${BACKEND_URL}/api/v1/auth/dev-login?role=admin`);
    if (resp.ok()) {
      const { access_token, refresh_token } = await resp.json();
      await page.evaluate(
        ({ at, rt }) => {
          localStorage.setItem(
            'auth-storage',
            JSON.stringify({ state: { token: at, refreshToken: rt }, version: 0 }),
          );
        },
        { at: access_token, rt: refresh_token },
      );
      await page.goto('/overview');
      await page.waitForSelector('aside', { state: 'visible', timeout: 10000 });
      return;
    }
  } catch {
    // dev-login not available — fall through to form login
  }

  // ── Fallback 2: form login via E2E_ADMIN_PASSWORD env var ─────────────────
  await page.goto('/login');
  await page.waitForSelector('input[name="username"]', { state: 'visible', timeout: 10000 });
  await page.locator('input[name="username"]').fill('admin');
  await page.locator('input[name="password"]').fill(process.env.E2E_ADMIN_PASSWORD ?? '');
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/.*\/overview/, { timeout: 20000 });
  await page.waitForSelector('aside', { state: 'visible', timeout: 10000 });
}
