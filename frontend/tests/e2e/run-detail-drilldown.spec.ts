import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Run detail → test-case drill-down (/runs/:runId → /runs/:runId/tests/:testId).
 * Exercises the real user path: the run summary header + per-test table, a
 * status-filter re-query, and clicking a failed row through to the test-case
 * page with its stack trace + AI panel.
 *
 * All /runs endpoints are intercepted by one catch-all so the flow is
 * deterministic (the live backend's data is empty / variable). The status
 * filter is honoured server-side via the ?status= query param.
 */

const RUN_ID = '11111111-1111-4111-8111-111111111111';
const FAIL_ID = '22222222-2222-4222-8222-222222222222';
const PASS_ID = '33333333-3333-4333-8333-333333333333';

const RUN = {
  id: RUN_ID,
  build_number: 1042,
  jenkins_job: 'nightly',
  status: 'failed',
  passed_tests: 8,
  failed_tests: 2,
  skipped_tests: 1,
  broken_tests: 0,
  total_tests: 11,
  pass_rate: 72.7,
  created_at: '2026-06-01T10:00:00Z',
  primary_suite_name: 'Checkout Suite',
  suite_names: ['Checkout Suite'],
  trigger_source: 'file_upload',
};

const FAIL_CASE = {
  id: FAIL_ID,
  test_name: 'test_checkout_payment',
  class_name: 'CheckoutTests',
  suite_name: 'Checkout Suite',
  status: 'FAILED',
  duration_ms: 1234,
  failure_category: 'assertion_error',
};

const PASS_CASE = {
  id: PASS_ID,
  test_name: 'test_cart_add_item',
  class_name: 'CartTests',
  suite_name: 'Checkout Suite',
  status: 'PASSED',
  duration_ms: 200,
};

const FAIL_DETAIL = {
  ...FAIL_CASE,
  full_name: 'CheckoutTests.test_checkout_payment',
  severity: 'critical',
  feature: 'Checkout',
  owner: 'qa-lead',
  created_at: '2026-06-01T10:00:00Z',
  tags: ['regression'],
  error_message: 'AssertionError: expected status 200 but got 500',
  has_attachments: false,
};

function page200(items: unknown[]) {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

/** One catch-all that routes every /runs request by path + ?status= param. */
async function mockRuns(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/runs**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown;

    if (p.endsWith(`/tests/${FAIL_ID}/rich-detail`)) {
      // Exercise the compatibility path used against deployments that do not
      // expose the additive rich-detail contract yet. A 404 is the only
      // response that should trigger the legacy detail request.
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Rich detail is not available' }),
      });
      return;
    } else if (p.endsWith(`/tests/${FAIL_ID}`)) {
      body = FAIL_DETAIL;
    } else if (p === `/api/v1/runs/${RUN_ID}/tests`) {
      // Honour the status filter so the filter UI is testable.
      const status = url.searchParams.get('status');
      const all = [FAIL_CASE, PASS_CASE];
      const items = status ? all.filter((c) => c.status === status) : all;
      body = page200(items);
    } else if (p === `/api/v1/runs/${RUN_ID}`) {
      body = RUN;
    } else if (p === '/api/v1/runs') {
      // Suite "compare with previous" lookup — just the current run.
      body = page200([RUN]);
    } else {
      // regression-diff (only on expand) and any other sub-path.
      body = { baseline_available: false };
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  // AI root-cause panel fetch on the test-case page — getAnalysis swallows a
  // 404 and shows its initial state, so this just keeps the request off the
  // live backend.
  await mockJson(page, '**/api/v1/analyze/*', { detail: 'no analysis yet' }, 404);
}

test.describe('Run detail → test-case drill-down', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await mockRuns(page);
  });

  test('run summary + test table render and a failed row drills into the test case', async ({ page }) => {
    await page.goto(`/runs/${RUN_ID}`);

    // Run summary header (build number + status counts).
    await expect(page.getByRole('heading', { name: /Run #1042/ })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('8 passed')).toBeVisible();
    await expect(page.getByText('2 failed')).toBeVisible();

    // Both test cases listed.
    await expect(page.getByText('test_checkout_payment')).toBeVisible();
    await expect(page.getByText('test_cart_add_item')).toBeVisible();

    // Drill into the failed test.
    await page.getByRole('row', { name: /test_checkout_payment/ }).click();
    await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}/tests/${FAIL_ID}`), { timeout: 8000 });

    // Test-case page: title, stack trace, and the AI analysis panel.
    await expect(page.getByRole('heading', { name: 'test_checkout_payment' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/AssertionError: expected status 200 but got 500/)).toBeVisible();
    await expect(page.getByRole('heading', { name: /AI Root Cause Analysis/i })).toBeVisible();
  });

  test('the FAILED status filter re-queries and hides passing rows', async ({ page }) => {
    await page.goto(`/runs/${RUN_ID}`);
    await expect(page.getByText('test_cart_add_item')).toBeVisible({ timeout: 10000 });

    // Apply the FAILED filter → server returns only the failed case.
    await page.getByRole('button', { name: 'FAILED', exact: true }).click();

    await expect(page.getByText('test_checkout_payment')).toBeVisible();
    await expect(page.getByText('test_cart_add_item')).toHaveCount(0);
  });
});
