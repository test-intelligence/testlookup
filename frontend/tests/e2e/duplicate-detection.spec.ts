import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson, seedActiveProject } from './apiMock';

/**
 * Phase 4 — duplicate authored-test-case detection (Test Management ▸ Duplicates).
 *
 * Flow under test: open Test Management → switch to the "Duplicates" tab for a
 * seeded project → see a banded near-duplicate PAIR with its human-readable
 * reason + component-score chips → click "Dismiss" → the pair disappears from
 * the open queue (the service records a `dismissed_duplicate_pairs` suppression
 * row so it never resurfaces on the next detection run).
 *
 * STUB STATUS: skipped (`test.describe.skip`). The selectors + mock shapes are
 * real and ready, but a faithful end-to-end run wants the live stack so the
 * detection sweep actually scores authored `managed_test_cases` into the
 * `duplicate_test_case_candidates` rows the list endpoint serves. Un-skip once a
 * seeded fixture (or the live detection path) is wired into the e2e harness.
 * Until then this documents the contract:
 *   - GET  /api/v1/projects/:id/duplicate-candidates → { items, total, open_count }
 *     where each item carries band (exact|strong|possible), score, reason,
 *     method (fingerprint|structural|semantic), component_scores, status, and
 *     the two case refs (case_a / case_b).
 *   - POST /api/v1/projects/:id/duplicate-candidates/:cid/dismiss → flips status
 *     to "dismissed" + writes the suppression row (the router owns the commit).
 *   - The DuplicatesTab is project-gated: in "All Projects" mode it shows a
 *     "Select a single project" gate, so we seed an active project first.
 *
 * The mocks below mirror the real detector output for one STRONG structural pair
 * ("User can log in with valid credentials" vs "Login with valid credentials")
 * so the spec is exercisable today by flipping `.skip` → run against a stack
 * that serves these shapes.
 */

const PROJECT = {
  id: '00000000-0000-4000-8000-0000000000e2',
  name: 'E2E Project',
};

const CASE_A = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  title: 'User can log in with valid credentials',
  suite_name: 'Auth',
  status: 'active',
};
const CASE_B = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  title: 'Login with valid credentials',
  suite_name: 'Auth',
  status: 'active',
};

const CANDIDATE = {
  id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  project_id: PROJECT.id,
  band: 'strong' as const,
  score: 0.93,
  reason:
    'titles share {credentials, login, valid}; title 0.94, text 0.88',
  method: 'structural' as const,
  component_scores: { title: 0.94, text: 0.88, steps: 0.91 },
  status: 'open' as const,
  detected_at: '2026-06-10T10:00:00Z',
  case_a: CASE_A,
  case_b: CASE_B,
};

const OPEN_LIST = { items: [CANDIDATE], total: 1, open_count: 1 };
const EMPTY_LIST = { items: [], total: 0, open_count: 0 };

/** Route the duplicate-candidates list + dismiss; the list empties after dismiss. */
async function mockDuplicates(page: import('@playwright/test').Page) {
  let dismissed = false;

  await page.route(
    `**/api/v1/projects/${PROJECT.id}/duplicate-candidates**`,
    async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      const method = route.request().method();

      // POST .../:cid/dismiss → flip the in-memory flag; subsequent list is empty.
      if (method === 'POST' && p.endsWith(`/${CANDIDATE.id}/dismiss`)) {
        dismissed = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            candidate_id: CANDIDATE.id,
            status: 'dismissed',
            deprecated_case_id: null,
          }),
        });
        return;
      }

      // GET list (default status=open) → the pair until it is dismissed.
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(dismissed ? EMPTY_LIST : OPEN_LIST),
      });
    },
  );
}

// SKIP: needs a live stack (or a seeded candidate fixture) to produce the row.
// Selectors are real; flip `.skip` → run to execute against such a stack.
test.describe.skip('Duplicate detection — review & dismiss', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockDuplicates(page);
  });

  test('Duplicates tab shows a banded pair with its reason, then Dismiss removes it', async ({
    page,
  }) => {
    await page.goto('/test-management');

    // Switch to the Duplicates tab.
    await page.getByRole('button', { name: 'Duplicates' }).click();

    // The banded pair renders with its band, both case titles, and the reason.
    await expect(page.getByText('strong', { exact: false })).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.getByText('User can log in with valid credentials'),
    ).toBeVisible();
    await expect(page.getByText('Login with valid credentials')).toBeVisible();
    await expect(page.getByText(/titles share/i)).toBeVisible();

    // Component-score chips surface the explainable breakdown.
    await expect(page.getByText(/title/i).first()).toBeVisible();

    // Dismiss the pair → it disappears from the open queue (suppressed).
    await page.getByRole('button', { name: /dismiss/i }).click();

    await expect(
      page.getByText('User can log in with valid credentials'),
    ).toHaveCount(0, { timeout: 8000 });
    await expect(page.getByText(/no duplicate candidates/i)).toBeVisible();
  });

  test('All-Projects mode shows the per-project gate instead of the queue', async ({
    page,
  }) => {
    // Override the active-project seed with the all-projects sentinel.
    await page.addInitScript(() => {
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({ state: { activeProjectId: null }, version: 0 }),
      );
    });
    await mockJson(page, '**/api/v1/projects', [
      { id: PROJECT.id, name: PROJECT.name, is_active: true },
    ]);

    await page.goto('/test-management');
    await page.getByRole('button', { name: 'Duplicates' }).click();

    await expect(page.getByText(/select a single project/i)).toBeVisible({
      timeout: 10000,
    });
  });
});
