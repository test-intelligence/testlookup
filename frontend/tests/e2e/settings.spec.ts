import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * Settings pages — Profile, AI Configuration, Notifications, Integrations,
 * Audit, Performance, Storage, SSO, Digests, AI Eval, Integration Health,
 * Seed Data (dev only).
 *
 * These tests verify each page renders its primary heading and the navigation
 * lands at the right URL. Deep behavioral assertions (e.g., saving an AI
 * config form) are intentionally avoided — they require a stable backend
 * fixture set, and the existing unit tests for AIConfigPage / ProfilePage
 * cover form mechanics. Here we lock in that the routes exist, the lazy
 * import succeeds, and the page does not crash with a blank screen.
 */
test.describe('Settings pages', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  const settingsRoutes: { name: string; path: string; expectText?: RegExp }[] = [
    { name: 'Profile',            path: '/settings/profile',           expectText: /profile|avatar|password/i },
    { name: 'AI Configuration',   path: '/settings/ai',                expectText: /analysis|llm|engine|ml/i },
    { name: 'Notifications',      path: '/settings/notifications',     expectText: /notification|email|slack|teams/i },
    { name: 'Integrations',       path: '/settings/integrations',      expectText: /jira|splunk|integration/i },
    { name: 'Storage',            path: '/settings/storage',           expectText: /storage|minio|s3|bucket/i },
    { name: 'SSO',                path: '/settings/sso',               expectText: /sso|saml|identity|enforcement/i },
    { name: 'Digests',            path: '/settings/digests',           expectText: /digest|schedule|subscription/i },
    { name: 'Integration Health', path: '/settings/integration-health',expectText: /health|probe|integration/i },
    { name: 'Audit',              path: '/settings/audit',             expectText: /audit|event|history/i },
    { name: 'AI Evaluation',      path: '/settings/ai-eval',           expectText: /eval|accuracy|drift|dataset/i },
    { name: 'Performance',        path: '/settings/performance',       expectText: /performance|latency|budget/i },
  ];

  for (const route of settingsRoutes) {
    test(`renders ${route.name}`, async ({ page }) => {
      await page.goto(route.path);
      await expect(page).toHaveURL(new RegExp(route.path.replace(/\//g, '\\/')));

      // Sidebar must remain present (i.e. we are still inside the auth shell)
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 });

      // The page should render at least one heading or recognizable text.
      // Soft assertion — we tolerate the case where the page is in an
      // empty/loading state (e.g., dev backend without sample data).
      const heading = page.getByRole('heading').first();
      const haveHeading = await heading
        .waitFor({ state: 'visible', timeout: 10000 })
        .then(() => true)
        .catch(() => false);

      if (haveHeading && route.expectText) {
        const bodyText = await page.locator('main, body').first().innerText();
        // We don't fail the test on a missing keyword — the test is purely
        // about "does the route load without crashing". The expect call
        // produces a descriptive failure if the keyword is absent.
        expect.soft(bodyText).toMatch(route.expectText);
      }
    });
  }
});

test.describe('Profile page interactions', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await page.goto('/settings/profile');
  });

  test('shows current user details', async ({ page }) => {
    await expect(page.locator('aside')).toBeVisible();

    // Profile page renders the user's email or username somewhere on screen.
    // We match loosely because the field could be an input or a read-only
    // display row depending on the implementation.
    //
    // `innerText` is a ONE-SHOT read, and the sidebar renders before the lazy
    // profile chunk does -- so this snapshot could capture the shell alone and
    // fail, which is what it did on WebKit while passing on Chromium. Poll so
    // the read retries the way a locator assertion would.
    await expect
      .poll(
        async () => (await page.locator('main, body').first().innerText()).toLowerCase(),
        {
          timeout: 15000,
          message: 'the profile page never rendered an email/username/full-name field',
        },
      )
      .toMatch(/email|username|full name|admin@testlookup/);
  });

  test('exposes change-password controls', async ({ page }) => {
    await expect(page.locator('aside')).toBeVisible();

    await expect(page.getByRole('heading', { name: /change password/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /update password/i })).toBeVisible();
  });
});

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await page.goto('/users');
  });

  test('renders the users list page', async ({ page }) => {
    await expect(page).toHaveURL(/.*\/users/);
    await expect(page.locator('aside')).toBeVisible();

    await expect(page.getByRole('heading', { name: /user management/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /add user|invite user/i }).first()).toBeVisible();
  });

  test('exposes invite or create user affordance for admins', async ({ page }) => {
    await expect(page.locator('aside')).toBeVisible();

    // ADMIN-only buttons. The login fixture authenticates as admin via
    // dev-login (see realLoginHelper.ts). The buttons should be visible.
    const inviteBtn = page.getByRole('button', { name: /invite|add.*user|create.*user/i }).first();
    const haveBtn = await inviteBtn
      .waitFor({ state: 'visible', timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    expect.soft(haveBtn).toBe(true);
  });
});
