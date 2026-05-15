import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * Feature smoke tests for areas not previously covered by the e2e suite:
 *   - Search (global keyword search across entities)
 *   - Projects management
 *   - Releases
 *   - Live execution
 *   - Chat (LLM agent)
 *   - Intelligence Hub
 *   - Deep investigation
 *   - Release Gate
 *
 * Each test confirms the route loads inside the auth shell and renders
 * recognizable content. Deep behavioral assertions (ingesting data, running
 * the agent) require live backend fixtures and are intentionally avoided —
 * the unit tests under frontend/src/pages/*.test.tsx cover form mechanics
 * and the backend integration tests cover API contracts.
 */
test.describe('Feature smoke tests', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  // ── Search ────────────────────────────────────────────────────────────────

  test.describe('Global search', () => {
    test('renders search page', async ({ page }) => {
      await page.goto('/search');
      await expect(page).toHaveURL(/.*\/search/);
      await expect(page.locator('aside')).toBeVisible();

      const searchInput = page
        .locator('input[type="search"], input[placeholder*="search" i], input[name*="q" i]')
        .first();
      await expect(searchInput).toBeVisible({ timeout: 10000 });
    });

    test('typing a query updates the URL or triggers a fetch', async ({ page }) => {
      await page.goto('/search');
      const searchInput = page
        .locator('input[type="search"], input[placeholder*="search" i], input[name*="q" i]')
        .first();
      await searchInput.fill('login');

      // Either the URL gets a ?q= param or an API call to /api/v1/search fires.
      // We give it 5s and accept either signal.
      const apiCall = page
        .waitForResponse((r) => r.url().includes('/api/v1/search'), { timeout: 5000 })
        .then(() => true)
        .catch(() => false);
      const urlChange = page
        .waitForURL(/.*q=login/, { timeout: 5000 })
        .then(() => true)
        .catch(() => false);

      const triggered = (await Promise.race([apiCall, urlChange])) === true;
      expect.soft(triggered).toBe(true);
    });
  });

  // ── Projects ──────────────────────────────────────────────────────────────

  test.describe('Projects', () => {
    test('renders projects page with create affordance', async ({ page }) => {
      await page.goto('/projects');
      await expect(page).toHaveURL(/.*\/projects/);
      await expect(page.locator('aside')).toBeVisible();

      // Create button or empty state must be visible.
      const createBtn = page.getByRole('button', { name: /create.*project|new project|\+/i }).first();
      const emptyState = page.locator('text=/no projects|create your first/i').first();
      const visible =
        (await createBtn.isVisible().catch(() => false)) ||
        (await emptyState.isVisible().catch(() => false));
      expect(visible).toBe(true);
    });
  });

  // ── Releases ──────────────────────────────────────────────────────────────

  test.describe('Releases', () => {
    test('renders releases page', async ({ page }) => {
      await page.goto('/releases');
      await expect(page).toHaveURL(/.*\/releases/);
      await expect(page.locator('aside')).toBeVisible();

      const heading = page.getByRole('heading').first();
      await expect(heading).toBeVisible({ timeout: 10000 });
    });
  });

  // ── Live execution ────────────────────────────────────────────────────────

  test.describe('Live execution', () => {
    test('renders live page', async ({ page }) => {
      await page.goto('/live');
      await expect(page).toHaveURL(/.*\/live/);
      await expect(page.locator('aside')).toBeVisible();

      // Live page either shows running sessions or a "no live sessions" empty state.
      const haveContent = await page
        .locator('text=/live|streaming|no.*session|no active/i')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 })
        .then(() => true)
        .catch(() => false);
      expect(haveContent).toBe(true);
    });
  });

  // ── Chat / AI agent (route disabled) ─────────────────────────────────────
  //
  // The /chat route was disabled in DefectsPage cleanup work — the route
  // and the sidebar entry are commented out. These tests pin the disabled
  // state so the route doesn't silently re-render without a sidebar entry
  // and so the sidebar doesn't grow back a Chat link without re-enabling
  // the route. Re-enable both checks if /chat is brought back.

  test.describe('Chat / AI agent (disabled)', () => {
    test('navigating to /chat falls through to the auth shell only', async ({ page }) => {
      await page.goto('/chat');
      // App.tsx routes the unknown path to /overview or shows a not-found —
      // either way the auth shell must stay and there must be no chat textarea.
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 });
      const textareaCount = await page.locator('textarea').count();
      expect(textareaCount).toBe(0);
    });

    test('sidebar does not expose a Chat link', async ({ page }) => {
      await page.goto('/overview');
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 });
      // No sidebar anchor should target /chat while the route is disabled.
      const chatLinkCount = await page.locator('aside a[href="/chat"]').count();
      expect(chatLinkCount).toBe(0);
    });
  });

  // ── Intelligence Hub ──────────────────────────────────────────────────────

  test.describe('Intelligence Hub', () => {
    test('renders intelligence hub', async ({ page }) => {
      await page.goto('/intelligence');
      await expect(page).toHaveURL(/.*\/intelligence/);
      await expect(page.locator('aside')).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
    });
  });

  // ── Deep investigation ────────────────────────────────────────────────────

  test.describe('Deep investigation', () => {
    test('renders deep investigation page', async ({ page }) => {
      await page.goto('/deep-investigate');
      await expect(page).toHaveURL(/.*\/deep-investigate/);
      await expect(page.locator('aside')).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
    });
  });

  // ── Release Gate ──────────────────────────────────────────────────────────

  test.describe('Release Gate', () => {
    test('renders release gate page', async ({ page }) => {
      await page.goto('/release-gate');
      await expect(page).toHaveURL(/.*\/release-gate/);
      await expect(page.locator('aside')).toBeVisible();

      // Gate page either shows GO/NO_GO/AT_RISK/CONDITIONAL or a "select a
      // run" empty state. Tolerate either.
      const haveSignal = await page
        .locator('text=/go|no.?go|at.?risk|conditional|select.*run|no.*run/i')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 })
        .then(() => true)
        .catch(() => false);
      expect(haveSignal).toBe(true);
    });
  });

  // ── Coverage > Suite detail ───────────────────────────────────────────────

  test.describe('Coverage', () => {
    test('renders coverage page', async ({ page }) => {
      await page.goto('/coverage');
      await expect(page).toHaveURL(/.*\/coverage/);
      await expect(page.locator('aside')).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
    });
  });

  // ── Trends ────────────────────────────────────────────────────────────────

  test.describe('Trends', () => {
    test('renders trends page with charts container', async ({ page }) => {
      await page.goto('/trends');
      await expect(page).toHaveURL(/.*\/trends/);
      await expect(page.locator('aside')).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
    });
  });
});

// ── 404 / unknown route handling ────────────────────────────────────────────

test.describe('Unknown route', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('redirects unknown routes without crashing', async ({ page }) => {
    await page.goto('/this-route-does-not-exist');
    // Either the app redirects to /overview or shows a not-found page —
    // both are acceptable, but the auth shell must remain.
    await expect(page.locator('aside')).toBeVisible({ timeout: 10000 });
  });
});
