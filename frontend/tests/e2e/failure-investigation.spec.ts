import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson, seedActiveProject } from './apiMock';

/**
 * Failure-investigation workflow (Tier-2): the real mutations behind triaging a
 * failure. The /failures page itself is read-only verdict analytics whose CTAs
 * are Phase-2 toast placeholders, so the genuine "investigate → categorise →
 * assign" actions live on:
 *
 *  - Defect intake (/defects): file a defect from a failure with a severity +
 *    failure_category (the triage tags) → POST /api/v1/analytics/defects.
 *    defects-failures.spec.ts already covers open/validation/escape; this adds
 *    the create round-trip.
 *  - Failed-test reassignment (/my-failures): move an auto-assigned failure to
 *    another owner → PUT /api/v1/me/assigned-failures/:id/reassign (QA_LEAD+).
 *
 * Project-scoped pages → seedActiveProject; both flows stay tolerant of the
 * all-projects gate / non-lead session so they never false-fail.
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

// ── Defect intake — create round-trip ───────────────────────────────────────
test.describe('Failure investigation — defect intake', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
  });

  test('files a defect with severity + category and POSTs the payload', async ({ page }) => {
    let postBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/analytics/defects*', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = route.request().postDataJSON();
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'defabc12-3456-4789-8abc-ef0123456789', title: postBody?.title }),
        });
      } else {
        // The defects list GET → empty so the page renders deterministically.
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0 }) });
      }
    });

    await page.goto('/defects');
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });

    // Fail closed. beforeEach seeds a concrete project, so the all-projects
    // gate is not a state this test can legitimately reach -- and if it does,
    // the seeding regressed, which is a defect rather than a reason to skip.
    const newDefectBtn = page.getByRole('button', { name: /^new defect$/i }).first();
    await expect(newDefectBtn, 'the New defect CTA did not render for a seeded project')
      .toBeVisible({ timeout: 10000 });
    await newDefectBtn.click();

    const dialog = page.getByRole('dialog', { name: /new defect/i });
    await expect(dialog, 'the intake modal did not open for a seeded project')
      .toBeVisible({ timeout: 8000 });

    // Investigate → categorise: title + severity (P0) + failure_category (FLAKY).
    await page.getByLabel(/^title/i).fill('Checkout 500 on submit');
    await page.getByRole('button', { name: /^P0/ }).click();
    await page.getByLabel(/failure category/i).selectOption('FLAKY');
    await page.getByLabel(/^component/i).fill('checkout-service');

    await page.getByRole('button', { name: /create defect/i }).click();

    await expect(page.getByText(/Defect defabc12 created/)).toBeVisible({ timeout: 8000 });
    expect(postBody).toMatchObject({
      project_id: PROJECT.id,
      title: 'Checkout 500 on submit',
      severity: 'P0',
      failure_category: 'FLAKY',
      component: 'checkout-service',
    });
  });
});

// ── Failed-test reassignment ────────────────────────────────────────────────
const FAILURE = {
  id: '44444444-4444-4444-8444-444444444444',
  test_name: 'test_checkout_payment',
  suite_name: 'Checkout Suite',
  class_name: 'CheckoutTests',
  status: 'FAILED',
  severity: 'high',
  failure_category: 'assertion_error',
  error_message: 'AssertionError: expected 200 but got 500',
  created_at: '2026-06-01T10:00:00Z',
  test_run_id: '11111111-1111-4111-8111-111111111111',
  build_number: '1042',
  project_id: PROJECT.id,
  project_name: PROJECT.name,
  navigation_url: '/runs/11111111-1111-4111-8111-111111111111/tests/44444444-4444-4444-8444-444444444444',
  run_seq: 12,
  failure_count: 3,
};

const REASSIGN_OPTIONS = {
  project_id: PROJECT.id,
  suite_name: 'Checkout Suite',
  suite_owner: null,
  qa_engineers: [
    { user_id: 'eng-0001', email: 'eng@example.com', username: 'qaeng', full_name: 'QA Engineer One' },
  ],
};

async function mockMyFailures(page: Page) {
  await mockJson(page, '**/api/v1/me/assigned-failures*', {
    items: [FAILURE], total: 1, page: 1, size: 25, pages: 1, unresolved_total: 1,
  });
  await mockJson(page, '**/api/v1/me/assigned-failures/*/reassign-options', REASSIGN_OPTIONS);
}

test.describe('Failure investigation — reassignment', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockMyFailures(page);
  });

  test('reassigns an auto-assigned failure to a QA engineer', async ({ page }) => {
    let putBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/me/assigned-failures/*/reassign', async (route) => {
      putBody = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FAILURE) });
    });

    await page.goto('/my-failures');
    await expect(page.getByText('test_checkout_payment')).toBeVisible({ timeout: 10000 });

    // The Reassign control is QA_LEAD/ADMIN-only.
    const reassignBtn = page.getByRole('button', { name: 'Reassign', exact: true }).first();
    await expect(
      reassignBtn,
      'the Reassign control is hidden. The suite logs in as admin, which '
      + 'satisfies the QA_LEAD/ADMIN gate, so its absence is a regression',
    ).toBeVisible({ timeout: 10000 });
    await reassignBtn.click();

    // Modal loads its options; the sole QA engineer is pre-selected.
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/QA Engineer One/)).toBeVisible({ timeout: 8000 });

    const submit = dialog.getByRole('button', { name: 'Reassign', exact: true });
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect(page.getByText('Failure reassigned')).toBeVisible({ timeout: 8000 });
    expect(putBody).toMatchObject({ new_assignee_user_id: 'eng-0001' });
  });
});
