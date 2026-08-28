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

    test('submitting the global search navigates to /search with the query', async ({ page }) => {
      // This test was unreliable for two compounding reasons, and it only
      // showed on Firefox:
      //
      // 1. `input[placeholder*="search" i]` matches TWO inputs -- the TopBar
      //    global search and SearchPage's own box -- and `.first()` took the
      //    TopBar one. That input does nothing on `fill()`; its handler is
      //    onKeyDown and fires only on Enter. So the action under test never
      //    happened.
      // 2. It then accepted "any /api/v1/search response within 5s" as proof.
      //    /search auto-runs on mount (measured: 3 calls before any typing),
      //    so the test could pass on a request the typing did not cause.
      //
      // Drive the real control and assert the real outcome instead.
      await page.goto('/overview');
      const globalSearch = page.getByPlaceholder(/search tests, runs, defects/i);
      await expect(globalSearch).toBeVisible({ timeout: 10000 });

      await globalSearch.fill('login');
      await globalSearch.press('Enter');

      await expect(page).toHaveURL(/\/search\?q=login/, { timeout: 10000 });
    });
  });

  // ── Projects ──────────────────────────────────────────────────────────────

  test.describe('Projects', () => {
    test('renders projects page with create affordance', async ({ page }) => {
      await page.goto('/projects');
      await expect(page).toHaveURL(/.*\/projects/);
      await expect(page.locator('aside')).toBeVisible();

      await expect(page.getByRole('heading', { name: /projects/i })).toBeVisible({ timeout: 10000 });
      await expect(page.getByRole('button', { name: /new project|create.*project/i })).toBeVisible();
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
  // /chat was once fully disabled — route commented out — and this block
  // pinned that. The route came back (US-2.1, Ask-AI chat): App.tsx registers
  // it deliberately "so a direct URL renders the page's own 'switch mode'
  // guidance instead of 404", while the SIDEBAR entry stays gated on the
  // ask_ai_chat flag plus a non-rules AI mode.
  //
  // So the contract is now two-sided, and both halves are asserted below:
  // reachable by URL, absent from the sidebar. The old redirect assertion
  // failed against a deployment doing exactly what it was designed to do.

  test.describe('Chat / AI agent (route registered, sidebar gated)', () => {
    test('navigating to /chat renders the page rather than redirecting', async ({ page }) => {
      await page.goto('/chat');
      await expect(page).toHaveURL(/.*\/chat/);
      // `.first()` is required: ChatPage renders its OWN <aside> (the
      // conversation list), so a bare locator('aside') matches two elements
      // and dies on strict mode. The nav shell is the first in the DOM.
      await expect(page.locator('aside').first()).toBeVisible({ timeout: 10000 });
      // In Rules mode the page explains itself instead of offering a prompt.
      // Either state is valid; what must NOT happen is a 404 or a bounce.
      //
      // `.or()` rather than two `.count()` reads: count is a ONE-SHOT check,
      // and ChatPage is a lazy chunk, so on WebKit both counts were taken
      // before either had rendered. This retries until one appears.
      const rendered = page
        .getByText(/chat is unavailable in rules mode/i)
        .or(page.locator('textarea'));
      await expect(
        rendered.first(),
        '/chat rendered neither the composer nor the rules-mode guidance',
      ).toBeVisible({ timeout: 15000 });
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
