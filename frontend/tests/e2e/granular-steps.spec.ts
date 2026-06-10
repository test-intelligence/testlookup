import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Phase 1 — granular test-step snapshot (Allure path).
 *
 * Flow under test: upload an Allure result → open the failed test's detail page
 * (/runs/:runId/tests/:testId) → the "Steps" panel renders the nested step tree
 * and auto-expands the FAILING step to surface its assertion message.
 *
 * STUB STATUS: skipped (`test.describe.skip`). The selectors + mock shapes are
 * real and ready, but a faithful end-to-end run needs the live stack so an
 * actual Allure `-result.json` is parsed by the ingestion pipeline into the
 * `test_steps` / `test_attachments` snapshot the `/steps` endpoint serves.
 * Un-skip once a seeded fixture (or the live ingestion path) is wired into the
 * e2e harness. Until then this documents the contract:
 *   - GET /api/v1/runs/:runId/tests/:testId/steps returns the TestStepsTree
 *   - TestStepsPanel renders one StepNode per step, nests children by depth,
 *     auto-opens FAILED/BROKEN steps, and shows `assertion_message` + the
 *     step-level attachment name.
 *
 * The mocks below mirror the real ingestion output for an Allure run whose
 * "click checkout" sub-step failed with an assertion + a DOM-snapshot
 * attachment, so the spec is exercisable today by flipping `.skip` to `.only`
 * against a stack that serves these shapes.
 */

const RUN_ID = '44444444-4444-4444-8444-444444444444';
const FAIL_ID = '55555555-5555-4555-8555-555555555555';

const RUN = {
  id: RUN_ID,
  build_number: 2099,
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

const FAIL_CASE = {
  id: FAIL_ID,
  test_name: 'test_checkout',
  class_name: 'Checkout',
  suite_name: 'Checkout',
  status: 'FAILED',
  duration_ms: 500,
};

const FAIL_DETAIL = {
  ...FAIL_CASE,
  full_name: 'shop.test_checkout',
  severity: 'critical',
  feature: 'Checkout',
  owner: 'qa-lead',
  created_at: '2026-06-09T10:00:00Z',
  tags: ['regression'],
  error_message: 'AssertionError: checkout button not found',
  has_attachments: true,
};

// The granular snapshot the ingestion pipeline writes for an Allure run:
// a passing parent step ("open cart") with a FAILED child ("click checkout")
// that carries the assertion message + a DOM-snapshot attachment.
const STEPS_TREE = {
  run_id: RUN_ID,
  test_id: FAIL_ID,
  test_name: 'test_checkout',
  status: 'FAILED',
  step_count: 1,
  retry_count: 1,
  is_flaky_run: true,
  stack_trace: 'Traceback (most recent call last): ...',
  steps: [
    {
      id: 'step-0000-0000-0000-000000000001',
      parent_step_id: null,
      ordinal: 0,
      depth: 0,
      name: 'open cart',
      keyword: null,
      status: 'PASSED',
      duration_ms: 100,
      start_ms: 1000,
      assertion_message: null,
      assertion_trace: null,
      expected_value: null,
      actual_value: null,
      parameters: { url: '/cart' },
      created_at: '2026-06-09T10:00:00Z',
      attachments: [],
      steps: [
        {
          id: 'step-0000-0000-0000-000000000002',
          parent_step_id: 'step-0000-0000-0000-000000000001',
          ordinal: 1,
          depth: 1,
          name: 'click checkout',
          keyword: null,
          status: 'FAILED',
          duration_ms: 50,
          start_ms: 1100,
          assertion_message: 'checkout button not found',
          assertion_trace: 'NoSuchElementException at checkout.py:42',
          expected_value: null,
          actual_value: null,
          parameters: null,
          created_at: '2026-06-09T10:00:00Z',
          steps: [],
          attachments: [
            {
              id: 'att-0000-0000-0000-000000000001',
              test_step_id: 'step-0000-0000-0000-000000000002',
              name: 'dom-snapshot.html',
              source_ref: 'd.html',
              media_type: 'text/html',
              created_at: '2026-06-09T10:00:00Z',
            },
          ],
        },
      ],
    },
  ],
  attachments: [],
};

function page200(items: unknown[]) {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

/** Catch-all that routes every /runs request, including the /steps snapshot. */
async function mockRuns(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/runs**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown;

    if (p.endsWith(`/tests/${FAIL_ID}/steps`)) {
      body = STEPS_TREE;
    } else if (p.endsWith(`/tests/${FAIL_ID}`)) {
      body = FAIL_DETAIL;
    } else if (p === `/api/v1/runs/${RUN_ID}/tests`) {
      body = page200([FAIL_CASE]);
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

// SKIP: needs a live stack (or seeded Allure fixture) to produce the snapshot.
// Selectors are real; flip `.skip` → run to execute against such a stack.
test.describe.skip('Granular steps — Allure failing-step snapshot', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await mockRuns(page);
  });

  test('opening a failed test shows the Steps panel with the failing step + assertion', async ({ page }) => {
    // Open the test-case detail page directly.
    await page.goto(`/runs/${RUN_ID}/tests/${FAIL_ID}`);

    // Page title + the "Steps" section heading render.
    await expect(page.getByRole('heading', { name: 'test_checkout' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('heading', { name: 'Steps', exact: true })).toBeVisible();

    // Step tree: the passing parent and the nested failing child are both shown.
    await expect(page.getByText('open cart')).toBeVisible();
    await expect(page.getByText('click checkout')).toBeVisible();

    // The FAILED step auto-expands → its assertion message is visible without a
    // click (StepNode opens FAILED/BROKEN steps by default).
    await expect(page.getByText('checkout button not found')).toBeVisible();

    // The step-level attachment surfaces by name under the failing step.
    await expect(page.getByText('dom-snapshot.html')).toBeVisible();

    // Flaky / retry chips from the snapshot metadata render.
    await expect(page.getByText('flaky run')).toBeVisible();
    await expect(page.getByText(/1 retry/)).toBeVisible();
  });

  test('empty snapshot shows the "no granular steps" empty state', async ({ page }) => {
    // Override the /steps response with an empty tree for this test.
    await page.route(`**/api/v1/runs/${RUN_ID}/tests/${FAIL_ID}/steps`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...STEPS_TREE, steps: [], attachments: [] }),
      });
    });

    await page.goto(`/runs/${RUN_ID}/tests/${FAIL_ID}`);
    await expect(page.getByRole('heading', { name: 'test_checkout' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('No granular steps captured for this test.')).toBeVisible();
  });
});
