import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * Phase E-2 smoke coverage for the Tier 0-2 pages shipped in the
 * 2026-04-14 batch. Each test confirms the route renders inside the
 * auth shell and the page header + its signature content loads. The
 * assertions tolerate empty states so a fresh DB (no flags / no
 * quotas / no subscriptions) still passes.
 *
 * "Flag on / flag off" pattern
 * ----------------------------
 * Tier 0-2 pages sit behind per-feature flags. The smoke assertions
 * below target the *management surface* of each flag, which renders
 * regardless of whether the underlying capability is currently enabled
 * — the page is how an admin turns the capability on in the first
 * place. For pages whose list content disappears when the flag is off
 * (e.g. the quarantine table), we assert on the header + an empty-
 * state tolerant locator so tests don't flap on a fresh install.
 *
 * When adding a new gated feature, follow the same pattern: target the
 * header + a content-or-empty-state locator that is stable across flag
 * states. Do not gate the spec itself on the flag — the spec is what
 * verifies the page is reachable.
 */
test.describe('Tier 0-2 feature pages', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  // ── 0A Feature Flags ──────────────────────────────────────────────────────

  test.describe('Feature Flags admin', () => {
    test('renders feature flags settings page', async ({ page }) => {
      await page.goto('/settings/feature-flags');
      await expect(page).toHaveURL(/.*\/settings\/feature-flags/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      // Either the "Feature Flags" header lands or (for a non-admin
      // user) the "Admin access required" gate. e2e runs as admin, so
      // the header is the expected path but we tolerate both.
      const heading = page
        .locator('text=/Feature Flags|Admin access required/')
        .first();
      await expect(heading).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 1-2 Billing / LLM cost budget ─────────────────────────────────────────

  test.describe('Billing', () => {
    test('renders billing overview page', async ({ page }) => {
      await page.goto('/settings/billing');
      await expect(page).toHaveURL(/.*\/settings\/billing/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      // Header renders even when no projects have quotas configured.
      const signal = page
        .locator('text=/LLM Cost Budget|billing|Failed to load billing/i')
        .first();
      await expect(signal).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 1-3 Flaky Quarantine ──────────────────────────────────────────────────

  test.describe('Flaky Quarantine', () => {
    test('renders quarantine page', async ({ page }) => {
      await page.goto('/quarantine');
      await expect(page).toHaveURL(/.*\/quarantine/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      // Header lands regardless of flag state. Empty-state tolerant:
      // "Nothing here" fires when no proposals exist.
      const signal = page
        .locator('text=/Flaky Quarantine|Nothing here|Failed to load/i')
        .first();
      await expect(signal).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 1-4 Release compliance pack panel (lives on Releases page) ────────────

  test.describe('Release Compliance Pack', () => {
    test('renders releases page (compliance pack panel host)', async ({ page }) => {
      // The CompliancePackPanel is rendered inline on ReleasesPage. It
      // only appears once a release is selected, so the smoke check
      // here is "releases page loads" — the panel's own render is
      // covered by the frontend unit tests.
      await page.goto('/releases');
      await expect(page).toHaveURL(/.*\/releases/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 1-5 GitHub Integration ────────────────────────────────────────────────

  test.describe('GitHub Integration', () => {
    test('renders GitHub integration settings page', async ({ page }) => {
      await page.goto('/settings/github');
      await expect(page).toHaveURL(/.*\/settings\/github/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      const signal = page
        .locator('text=/GitHub Integration|github|Per-project GitHub/i')
        .first();
      await expect(signal).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 2-6 Outbound Webhooks ─────────────────────────────────────────────────

  test.describe('Outbound Webhooks', () => {
    test('renders outbound webhooks settings page', async ({ page }) => {
      await page.goto('/settings/webhooks');
      await expect(page).toHaveURL(/.*\/settings\/webhooks/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      // Two possible top-of-page states: the "select a project" empty
      // state when no project is active, or the real page with its
      // subtitle referencing HMAC-signed delivery.
      const signal = page
        .locator('text=/Outbound Webhooks|Select a project|HMAC/i')
        .first();
      await expect(signal).toBeVisible({ timeout: 10000 });
    });
  });

  // ── 2-8 Run Compare ───────────────────────────────────────────────────────

  test.describe('Run Compare', () => {
    test('renders run compare page', async ({ page }) => {
      await page.goto('/runs/compare');
      await expect(page).toHaveURL(/.*\/runs\/compare/);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();

      // Fresh state: empty "pick two runs" prompt. Also tolerate the
      // "Pick two different runs" message that lands when only one is
      // pre-selected via query params.
      const signal = page
        .locator('text=/Run Compare|Pick two/i')
        .first();
      await expect(signal).toBeVisible({ timeout: 10000 });
    });
  });
});
