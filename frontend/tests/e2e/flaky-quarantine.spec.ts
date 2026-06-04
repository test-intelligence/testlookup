import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson, seedActiveProject } from './apiMock';

/**
 * Flaky-quarantine review workflow (Tier-2). The QuarantinePage state machine
 * (proposals → active → history) is QA_LEAD-gated; each proposal row has
 * Approve / Reject, and active quarantines have Release. Each action prompts
 * for optional notes (window.prompt) then POSTs to
 * /api/v1/quarantine/:id/{approve,reject,release}.
 *
 * The list + stats are mocked so the rows are deterministic; the proposals and
 * active tabs share one live_only=true fetch (the page filters client-side by
 * status), so a single list with a PROPOSED + a QUARANTINED row drives both.
 * Every test self-skips if the action buttons are hidden (non-QA-lead session).
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

const PROPOSED = {
  id: 'q-proposed-0001',
  project_id: PROJECT.id,
  test_fingerprint: 'fp-login-0000000000000000',
  test_name: 'test_flaky_login',
  suite_name: 'Auth Suite',
  status: 'PROPOSED',
  detection_method: 'flip_rate',
  flip_rate: 0.3,
  flip_window_size: 10,
  pass_count: 7,
  fail_count: 3,
  detected_at: '2026-06-01T09:00:00Z',
  quarantine_duration_days: 7,
  reviewer_notes: null,
  created_at: '2026-06-01T09:00:00Z',
  updated_at: '2026-06-01T09:00:00Z',
};

const QUARANTINED = {
  ...PROPOSED,
  id: 'q-active-0002',
  test_fingerprint: 'fp-checkout-000000000000',
  test_name: 'test_flaky_checkout',
  suite_name: 'Checkout Suite',
  status: 'QUARANTINED',
  flip_rate: 0.45,
};

const STATS = {
  detected: 0, proposed: 1, approved: 0, quarantined: 1, recheck_scheduled: 0,
  re_quarantined: 0, released: 4, rejected: 2, expired: 0, total_live: 2,
};

async function mockQuarantine(page: Page) {
  // stats + list share the /quarantine prefix; the list glob's trailing * does
  // not cross '/', so it matches /quarantine(+query) but not /stats or /:id/*.
  await mockJson(page, '**/api/v1/quarantine/stats*', STATS);
  await mockJson(page, '**/api/v1/quarantine*', [PROPOSED, QUARANTINED]);
}

test.describe('Flaky quarantine — review workflow', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockQuarantine(page);
  });

  test('approve a proposal POSTs the decision with notes', async ({ page }) => {
    let approveUrl = '';
    let approveBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/quarantine/*/approve', async (route) => {
      approveUrl = route.request().url();
      approveBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...PROPOSED, status: 'APPROVED' }),
      });
    });
    // The Approve handler opens window.prompt for notes — accept with text.
    page.on('dialog', (d) => d.accept('Confirmed flaky on CI'));

    await page.goto('/quarantine');
    await expect(page.getByRole('heading', { name: /flaky quarantine/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('test_flaky_login')).toBeVisible();

    const approveBtn = page.getByRole('button', { name: /approve/i });
    if (!(await approveBtn.isVisible().catch(() => false))) {
      test.skip(true, 'Approve action hidden — non-QA-lead session in this env.');
      return;
    }
    await approveBtn.click();

    await expect(page.getByText('Quarantine approved')).toBeVisible({ timeout: 8000 });
    expect(approveUrl).toContain('/quarantine/q-proposed-0001/approve');
    expect(approveBody).toMatchObject({ notes: 'Confirmed flaky on CI' });
  });

  test('reject a proposal POSTs to the reject endpoint', async ({ page }) => {
    let rejectUrl = '';
    await page.route('**/api/v1/quarantine/*/reject', async (route) => {
      rejectUrl = route.request().url();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...PROPOSED, status: 'REJECTED' }),
      });
    });
    page.on('dialog', (d) => d.accept('Not actually flaky — env issue'));

    await page.goto('/quarantine');
    await expect(page.getByText('test_flaky_login')).toBeVisible({ timeout: 10000 });

    const rejectBtn = page.getByRole('button', { name: /reject/i });
    if (!(await rejectBtn.isVisible().catch(() => false))) {
      test.skip(true, 'Reject action hidden — non-QA-lead session in this env.');
      return;
    }
    await rejectBtn.click();

    await expect(page.getByText('Proposal rejected')).toBeVisible({ timeout: 8000 });
    expect(rejectUrl).toContain('/quarantine/q-proposed-0001/reject');
  });

  test('release an active quarantine from the Active tab', async ({ page }) => {
    let releaseUrl = '';
    await page.route('**/api/v1/quarantine/*/release', async (route) => {
      releaseUrl = route.request().url();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...QUARANTINED, status: 'RELEASED' }),
      });
    });
    page.on('dialog', (d) => d.accept('Stable for 2 weeks'));

    await page.goto('/quarantine');
    await expect(page.getByRole('heading', { name: /flaky quarantine/i })).toBeVisible({ timeout: 10000 });

    // Switch to the Active tab — the QUARANTINED row lives there.
    await page.getByRole('button', { name: 'Active', exact: true }).click();
    await expect(page.getByText('test_flaky_checkout')).toBeVisible({ timeout: 8000 });

    const releaseBtn = page.getByRole('button', { name: /release/i });
    if (!(await releaseBtn.isVisible().catch(() => false))) {
      test.skip(true, 'Release action hidden — non-QA-lead session in this env.');
      return;
    }
    await releaseBtn.click();

    await expect(page.getByText('Quarantine released')).toBeVisible({ timeout: 8000 });
    expect(releaseUrl).toContain('/quarantine/q-active-0002/release');
  });
});
