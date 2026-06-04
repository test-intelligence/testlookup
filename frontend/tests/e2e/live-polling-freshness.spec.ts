import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { seedActiveProject } from './apiMock';

/**
 * Live polling freshness (Tier-3). LiveExecutionPage polls GET
 * /api/v1/stream/active on an SWR refreshInterval (5s, or 10s once the
 * WebSocket is open) and re-renders without a manual reload. We serve a
 * counter-based mock: the first poll returns one running session, every
 * later poll returns two — so the "N active run(s)" hero must tick from 1 → 2
 * on its own, proving the page stays fresh via background polling.
 *
 * (/my-failures uses the same SWR-refreshInterval pattern at 30s; /live's 5s
 * cadence is the deterministic one to assert against.)
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

// A session only counts toward the "N active runs" hero when it looks fresh:
// isActivelyRunning() requires last_event_at/started_at within 60s of now
// (utils/liveSessionFreshness.ts). So each poll stamps the current time.
function session(now: string, over: Record<string, unknown>) {
  return {
    run_id: 'live-sess',
    project_id: PROJECT.id,
    build_number: 'build-100',
    run_seq: 1,
    status: 'running',
    total: 10,
    passed: 6,
    failed: 1,
    skipped: 0,
    broken: 0,
    pass_rate: 85.7,
    current_test: 'test_login',
    suite_name: 'Checkout Suite',
    started_at: now,
    last_event_at: now,
    ...over,
  };
}

/** First poll → one session; every later poll → two. Timestamps are stamped
 *  "now" at fulfil time so the sessions stay within the freshness window. */
async function mockActiveSessions(page: Page) {
  let calls = 0;
  await page.route('**/api/v1/stream/active*', async (route) => {
    calls += 1;
    const now = new Date().toISOString();
    const s1 = session(now, { run_id: 'live-sess-1', build_number: 'build-100', run_seq: 1 });
    const s2 = session(now, { run_id: 'live-sess-2', build_number: 'build-101', run_seq: 2, passed: 8, failed: 0 });
    const sessions = calls === 1 ? [s1] : [s1, s2];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions, count: sessions.length }),
    });
  });
}

test.describe('Live execution — polling freshness', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockActiveSessions(page);
  });

  test('the active-runs hero refreshes from 1 → 2 via background polling', async ({ page }) => {
    await page.goto('/live');

    // First poll: one running session.
    await expect(page.getByText('1 active run', { exact: true })).toBeVisible({ timeout: 10000 });

    // A later poll adds a second session — the hero updates WITHOUT a reload.
    await expect(page.getByText('2 active runs', { exact: true })).toBeVisible({ timeout: 20000 });
  });
});
