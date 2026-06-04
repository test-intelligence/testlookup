import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { seedActiveProject } from './apiMock';

/**
 * Runs table — filter / sort / pagination (Tier-3). The RunsPage table fetches
 * a wide window (size 500) and paginates + sorts it client-side (25/page,
 * Started column toggles asc/desc), while the status filter is a server param
 * that re-fetches. We mock GET /api/v1/runs with 30 deterministic runs and
 * branch on the ?status= param so the filter demonstrably narrows the set.
 *
 * The Build column renders "Run #<run_seq>" (unique to the table; cluster /
 * velocity cards use "#<build_number>"), so the run_seq labels are safe row
 * anchors. Created-at ascends with run_seq, so seq 30 is newest (top in the
 * default desc sort) and seq 1 is oldest (page 2).
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

function makeRuns() {
  return Array.from({ length: 30 }, (_, idx) => {
    const i = idx + 1;
    const failed = i <= 4;
    return {
      id: `10000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
      build_number: 1000 + i,
      run_seq: i,
      status: failed ? 'failed' : 'passed',
      passed_tests: failed ? 5 : 10,
      failed_tests: failed ? 5 : 0,
      total_tests: 10,
      pass_rate: failed ? 50 : 100,
      created_at: new Date(Date.UTC(2026, 4, i)).toISOString(), // 2026-05-01..30
      primary_suite_name: 'Checkout Suite',
      suite_names: ['Checkout Suite'],
      branch: 'main',
      release_name: 'v2.4',
      duration_ms: 1000,
      jenkins_job: 'nightly',
    };
  });
}

/** One mock for both the main list and useSuiteOptions; branches on ?status=. */
async function mockRuns(page: Page) {
  await page.route('**/api/v1/runs*', async (route) => {
    const status = new URL(route.request().url()).searchParams.get('status');
    const all = makeRuns();
    const items = status === 'FAILED' ? all.filter((r) => r.status === 'failed') : all;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, total: items.length, page: 1, size: 500, pages: 1 }),
    });
  });
}

const runLink = (page: Page, seq: number) =>
  page.getByRole('link', { name: `Run #${seq}`, exact: true });

test.describe('Runs table — filter / sort / pagination', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockRuns(page);
  });

  test('paginates the 30-run window client-side (25 per page)', async ({ page }) => {
    await page.goto('/runs');
    await expect(runLink(page, 30)).toBeVisible({ timeout: 10000 });

    // Default desc: page 1 = seq 30..6; the oldest (seq 1) is on page 2.
    await expect(page.getByText('Page 1 of 2')).toBeVisible();
    await expect(runLink(page, 1)).toHaveCount(0);

    // Advance to page 2 via the next chevron (sibling of the page indicator).
    await page.getByText('Page 1 of 2').locator('xpath=following-sibling::button[1]').click();

    await expect(page.getByText('Page 2 of 2')).toBeVisible();
    await expect(runLink(page, 1)).toBeVisible();
    await expect(runLink(page, 30)).toHaveCount(0);
  });

  test('toggling the Started column sort reorders across the page boundary', async ({ page }) => {
    await page.goto('/runs');
    // Default desc → newest (seq 30) on page 1, oldest (seq 1) on page 2.
    await expect(runLink(page, 30)).toBeVisible({ timeout: 10000 });
    await expect(runLink(page, 1)).toHaveCount(0);

    await page.getByRole('columnheader', { name: /Started/ }).click();

    // Asc → oldest (seq 1) now on page 1, newest (seq 30) pushed to page 2.
    await expect(runLink(page, 1)).toBeVisible();
    await expect(runLink(page, 30)).toHaveCount(0);
  });

  test('the status filter re-queries and narrows the table to failed runs', async ({ page }) => {
    await page.goto('/runs');
    // A passing run (seq 30) is visible on page 1 before filtering.
    await expect(runLink(page, 30)).toBeVisible({ timeout: 10000 });

    // Select Failed → server returns only the 4 failed runs (seq 1..4).
    await page.locator('select').filter({ hasText: 'All statuses' }).selectOption('FAILED');

    await expect(runLink(page, 1)).toBeVisible();      // failed → present
    await expect(runLink(page, 30)).toHaveCount(0);    // passed → filtered out
    await expect(page.getByText('Page 1 of 2')).toHaveCount(0); // only 4 rows → no pager
  });
});
