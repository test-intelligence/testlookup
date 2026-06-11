import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Phase 2 — cross-run test-case history + flakiness + metadata panel.
 *
 * Flow under test: a logical test has been seen across MULTIPLE runs → open the
 * test's detail page (/runs/:runId/tests/:testId) → the "History & Flakiness"
 * panel (`TestHistoryPanel`) renders the pass/fail timeline (one dot per run),
 * the computed flakiness chips (classification + failure-rate + impact, reusing
 * analytics_service / test_health_coach values — no new formula), the in-window
 * pass/fail/total counts, and the identity metadata block (suite / owner / first
 * & last seen).
 *
 * STUB STATUS: skipped (`test.describe.skip`). The selectors + mock shapes are
 * real and ready, but a faithful end-to-end run needs the live stack so that
 * MULTIPLE runs of the same logical test are ingested and the read-only
 * `/history` endpoint computes the project-scoped timeline + flakiness from
 * `test_case_history`. Un-skip once a seeded multi-run fixture (or the live
 * ingestion path) is wired into the e2e harness. Until then this documents the
 * contract:
 *   - GET /api/v1/runs/:runId/tests/:testId/history returns TestCaseHistory
 *     ({ history[], flakiness, metadata }), project-scoped server-side.
 *   - TestHistoryPanel renders one timeline dot per history point (titled with
 *     run label / status / duration / when), the flakiness classification +
 *     `failure_rate_pct` + `impact_score` chips when `is_flaky`, the
 *     passed/failed/total(window) summary, and the Suite/Owner/Severity/Feature/
 *     First-seen/Last-seen metadata rows.
 *
 * The mocks below mirror the real `/history` output for a flaky test seen across
 * 6 runs (3 PASSED + 3 FAILED → 50% fail), so the spec is exercisable today by
 * flipping `.skip` to `.only` against a stack that serves these shapes.
 */

const RUN_ID = '66666666-6666-4666-8666-666666666666';
const TEST_ID = '77777777-7777-4777-8777-777777777777';
const FINGERPRINT = 'cafe'.repeat(16);

const RUN = {
  id: RUN_ID,
  build_number: 3100,
  jenkins_job: 'nightly',
  status: 'failed',
  passed_tests: 0,
  failed_tests: 1,
  skipped_tests: 0,
  broken_tests: 0,
  total_tests: 1,
  pass_rate: 0,
  created_at: '2026-06-09T10:00:00Z',
  primary_suite_name: 'Checkout',
  suite_names: ['Checkout'],
  trigger_source: 'file_upload',
};

const TEST_CASE = {
  id: TEST_ID,
  test_name: 'test_checkout',
  class_name: 'Checkout',
  suite_name: 'Checkout',
  status: 'FAILED',
  duration_ms: 500,
};

const TEST_DETAIL = {
  ...TEST_CASE,
  full_name: 'shop.test_checkout',
  severity: 'critical',
  feature: 'Checkout',
  owner: 'qa-lead',
  created_at: '2026-06-09T10:00:00Z',
  tags: ['regression'],
  error_message: 'AssertionError: checkout button not found',
  has_attachments: false,
};

// Six cross-run history points (most-recent-first), 3 PASSED + 3 FAILED.
// 50% failure rate → flaky (in the 0.05–0.95 auto-detector band) → INVESTIGATE.
function historyPoint(seq: number, status: string, hoursAgo: number) {
  return {
    run_id: `run-${seq.toString().padStart(8, '0')}-0000-4000-8000-000000000000`,
    run_label: `Run #${seq}`,
    build_number: `build-${seq}`,
    run_seq: seq,
    status,
    duration_ms: 400 + seq,
    created_at: new Date(Date.now() - hoursAgo * 3600_000).toISOString(),
  };
}

const HISTORY = {
  run_id: RUN_ID,
  test_id: TEST_ID,
  test_fingerprint: FINGERPRINT,
  test_name: 'test_checkout',
  history: [
    historyPoint(6, 'FAILED', 1),
    historyPoint(5, 'PASSED', 4),
    historyPoint(4, 'FAILED', 8),
    historyPoint(3, 'PASSED', 12),
    historyPoint(2, 'FAILED', 24),
    historyPoint(1, 'PASSED', 48),
  ],
  // failure_rate / failure_rate_pct match analytics_service.flaky_tests;
  // classification + impact_score reuse test_health_coach thresholds.
  flakiness: {
    is_flaky: true,
    failure_rate: 0.5,
    failure_rate_pct: 50.0,
    impact_score: 28.1,
    classification: 'INVESTIGATE',
    window_days: 30,
    total_runs: 6,
    passed: 3,
    failed: 3,
  },
  metadata: {
    owner: 'qa-lead',
    assigned_to_user_id: null,
    suite: 'Checkout',
    severity: 'critical',
    feature: 'Checkout',
    first_seen_run_id: 'run-00000001-0000-4000-8000-000000000000',
    first_seen_run_label: 'build-1',
    first_seen_at: '2026-05-20T10:00:00Z',
    last_seen_run_id: 'run-00000006-0000-4000-8000-000000000000',
    last_seen_run_label: 'build-6',
    last_seen_at: '2026-06-09T10:00:00Z',
    created_at: '2026-05-20T10:00:00Z',
    updated_at: '2026-06-09T10:00:00Z',
  },
};

function page200(items: unknown[]) {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

/** Catch-all that routes every /runs request, including the /history payload. */
async function mockRuns(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/runs**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown;

    if (p.endsWith(`/tests/${TEST_ID}/history`)) {
      body = HISTORY;
    } else if (p.endsWith(`/tests/${TEST_ID}/steps`)) {
      // Steps panel shares the detail page; keep it off the live backend.
      body = {
        run_id: RUN_ID, test_id: TEST_ID, test_name: 'test_checkout',
        status: 'FAILED', step_count: 0, retry_count: 0, is_flaky_run: false,
        stack_trace: null, steps: [], attachments: [],
      };
    } else if (p.endsWith(`/tests/${TEST_ID}`)) {
      body = TEST_DETAIL;
    } else if (p === `/api/v1/runs/${RUN_ID}/tests`) {
      body = page200([TEST_CASE]);
    } else if (p === `/api/v1/runs/${RUN_ID}`) {
      body = RUN;
    } else if (p === '/api/v1/runs') {
      body = page200([RUN]);
    } else {
      body = { baseline_available: false };
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  // AI root-cause panel fetch — keep it off the live backend.
  await mockJson(page, '**/api/v1/analyze/*', { detail: 'no analysis yet' }, 404);
}

// SKIP: needs a live stack (or a seeded multi-run fixture) so the read-only
// /history endpoint computes the project-scoped timeline + flakiness. Selectors
// are real; flip `.skip` → run to execute against such a stack.
test.describe.skip('Test history & flakiness — cross-run panel', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await mockRuns(page);
  });

  test('opening a test shows the History & Flakiness panel with timeline + flakiness', async ({ page }) => {
    await page.goto(`/runs/${RUN_ID}/tests/${TEST_ID}`);

    // Page title + the "History & Flakiness" section heading render.
    await expect(page.getByRole('heading', { name: 'test_checkout' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('heading', { name: 'History & Flakiness' })).toBeVisible();

    // Flakiness chips: classification + failure-rate% + impact (only when is_flaky).
    await expect(page.getByText('INVESTIGATE')).toBeVisible();
    await expect(page.getByText(/50\.0% fail/)).toBeVisible();
    await expect(page.getByText(/impact 28\.1/)).toBeVisible();

    // In-window pass/fail/total summary.
    await expect(page.getByText('3 passed')).toBeVisible();
    await expect(page.getByText('3 failed')).toBeVisible();
    await expect(page.getByText(/6 runs \(30d\)/)).toBeVisible();

    // Pass/fail timeline: 6 dots, one per history point. Each dot carries a
    // title attribute starting with its run label.
    await expect(page.getByText('Recent runs (oldest → newest)')).toBeVisible();
    await expect(page.locator('[aria-label^="Run #6"]')).toBeVisible();
    await expect(page.locator('[aria-label^="Run #1"]')).toBeVisible();

    // Metadata rows: suite / owner / first-seen / last-seen.
    await expect(page.getByText('Suite')).toBeVisible();
    await expect(page.getByText('Checkout').first()).toBeVisible();
    await expect(page.getByText('Owner')).toBeVisible();
    await expect(page.getByText('First seen')).toBeVisible();
    await expect(page.getByText('Last seen')).toBeVisible();
  });

  test('a test with no prior runs shows the empty history state', async ({ page }) => {
    // Override /history with an empty timeline + non-flaky HEALTHY block.
    await page.route(`**/api/v1/runs/${RUN_ID}/tests/${TEST_ID}/history`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...HISTORY,
          history: [],
          flakiness: {
            is_flaky: false, failure_rate: 0.0, failure_rate_pct: 0.0,
            impact_score: 0.0, classification: 'HEALTHY', window_days: 30,
            total_runs: 0, passed: 0, failed: 0,
          },
        }),
      });
    });

    await page.goto(`/runs/${RUN_ID}/tests/${TEST_ID}`);
    await expect(page.getByRole('heading', { name: 'test_checkout' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('No prior runs for this test.')).toBeVisible();
    // No flakiness chips when not flaky — INVESTIGATE must not be present.
    await expect(page.getByText('INVESTIGATE')).toHaveCount(0);
  });
});
