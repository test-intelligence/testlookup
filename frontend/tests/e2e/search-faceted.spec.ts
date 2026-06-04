import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Faceted global search (Tier-2). SearchPage auto-runs a browse query on mount,
 * then the scope chips (All / Tests / Runs / Suites / Defects / Flaky /
 * Releases) re-run GET /api/v1/search/global with an `entity_types` filter, and
 * each result row navigates to its entity. The mode chips (Hybrid / Keyword /
 * Semantic) feed the provenance footer.
 *
 * index-status + entity-counts (chip counts) and the global search are all
 * mocked; the global mock branches on the `entity_types` query param so a
 * scope switch demonstrably narrows the result set.
 */

const RID = '11111111-1111-4111-8111-111111111111';
const TID = '22222222-2222-4222-8222-222222222222';

const TEST_CASE = {
  entity_type: 'test_case', entity_id: TID, title: 'Login regression test',
  subtitle: 'Auth Suite', project_id: null, project_name: 'E2E Project',
  navigation_url: `/runs/${RID}/tests/${TID}`, relevance_score: 0.91, match_reasons: [], metadata: {},
};
const TEST_RUN = {
  entity_type: 'test_run', entity_id: RID, title: 'Checkout nightly run',
  subtitle: 'Build 1042', project_id: null, project_name: 'E2E Project',
  navigation_url: `/runs/${RID}`, relevance_score: 0.88, match_reasons: [], metadata: {},
};
const DEFECT = {
  entity_type: 'defect', entity_id: 'd-0001', title: 'Checkout 500 defect',
  subtitle: 'P0', project_id: null, project_name: 'E2E Project',
  navigation_url: '/defects', relevance_score: 0.80, match_reasons: [], metadata: {},
};

const COUNTS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 };
const INDEX_STATUS = { status: 'healthy', document_count: 140, last_indexed_at: '2026-06-04T10:00:00Z' };

/** Wire the three search endpoints; the global mock narrows by entity_types and
 *  records the last requested filter. Returns a getter for that filter. */
async function mockSearch(page: Page): Promise<() => string | null> {
  let lastEntityTypes: string | null = null;
  await mockJson(page, '**/api/v1/search/index-status*', INDEX_STATUS);
  await mockJson(page, '**/api/v1/search/entity-counts*', COUNTS);
  await page.route('**/api/v1/search/global*', async (route) => {
    const url = new URL(route.request().url());
    lastEntityTypes = url.searchParams.get('entity_types');
    const items = lastEntityTypes === 'test_case' ? [TEST_CASE] : [TEST_CASE, TEST_RUN, DEFECT];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items,
        total: items.length,
        query: url.searchParams.get('q') ?? '',
        search_type: 'hybrid',
        entity_counts: COUNTS,
        page: 1, size: 25, pages: 1,
      }),
    });
  });
  return () => lastEntityTypes;
}

test.describe('Faceted search', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('switching the scope facet re-queries with the entity_types filter', async ({ page }) => {
    const getEntityTypes = await mockSearch(page);

    await page.goto('/search');
    // Auto-run (scope=all) shows the mixed result set.
    await expect(page.getByText('Login regression test')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Checkout nightly run')).toBeVisible();
    await expect(page.getByText('Checkout 500 defect')).toBeVisible();

    // Click the Tests scope chip → server returns only the test_case row.
    await page.getByRole('radio', { name: /Tests/ }).click();

    await expect(page.getByText('Login regression test')).toBeVisible();
    await expect(page.getByText('Checkout nightly run')).toHaveCount(0);
    await expect(page.getByText('Checkout 500 defect')).toHaveCount(0);
    expect(getEntityTypes()).toBe('test_case');
  });

  test('clicking a result row navigates to its entity', async ({ page }) => {
    await mockSearch(page);

    await page.goto('/search');
    const runRow = page.getByRole('button', { name: /Checkout nightly run/ });
    await expect(runRow).toBeVisible({ timeout: 10000 });

    await runRow.click();
    await expect(page).toHaveURL(new RegExp(`/runs/${RID}$`), { timeout: 8000 });
  });

  test('the mode facet updates the retrieval provenance footer', async ({ page }) => {
    await mockSearch(page);

    await page.goto('/search');
    await expect(page.getByText('Hybrid retrieval')).toBeVisible({ timeout: 10000 });

    // Switch the retrieval mode chip Hybrid → Keyword.
    await page.getByRole('radio', { name: 'Keyword', exact: true }).click();
    await expect(page.getByText('Keyword retrieval')).toBeVisible({ timeout: 8000 });
  });
});
