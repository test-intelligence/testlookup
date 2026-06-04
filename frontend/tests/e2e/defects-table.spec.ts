import { test, expect, Page } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { seedActiveProject } from './apiMock';

/**
 * Defects table — filter / sort (Tier-3, companion to runs-table.spec.ts).
 * Unlike RunsPage (server-side status param), DefectsPage fetches every defect
 * once via useDefects() and filters/sorts entirely client-side: status tabs,
 * a free-text search box, and Age/Severity/Status column sorts. The table has
 * no pagination — it renders all visible rows — so this covers the filter +
 * sort legs that runs-table covered server-side / with pagination.
 *
 * The Title column renders the defect's test_name, so the test_name values are
 * the row anchors.
 */

const PROJECT = { id: '00000000-0000-4000-8000-0000000000e2', name: 'E2E Project' };

const DEFECTS = [
  { test_name: 'test_login_open',       resolution_status: 'OPEN',        created_at: '2026-06-04T12:00:00Z' },
  { test_name: 'test_cart_inprogress',  resolution_status: 'IN_PROGRESS', created_at: '2026-06-03T12:00:00Z' },
  { test_name: 'test_pay_resolved',     resolution_status: 'RESOLVED',    created_at: '2026-06-02T12:00:00Z' },
  { test_name: 'test_search_closed',    resolution_status: 'CLOSED',      created_at: '2026-06-01T00:00:00Z' },
].map((d, i) => ({
  id: `20000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`,
  jira_ticket_id: i % 2 === 0 ? `EVG-${100 + i}` : undefined,
  jira_ticket_url: i % 2 === 0 ? `https://jira.example.com/browse/EVG-${100 + i}` : undefined,
  failure_category: 'PRODUCT_BUG',
  suite_name: 'Checkout Suite',
  ...d,
}));

async function mockDefects(page: Page) {
  // useDefects(1, undefined) → no resolution_status param → all defects; the
  // page filters by tab/search client-side.
  await page.route('**/api/v1/analytics/defects*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: DEFECTS, total: DEFECTS.length, page: 1, size: 50, pages: 1 }),
    });
  });
}

/** The defects table, scoped by its unique "Title" column header. */
const defectsTable = (page: Page) =>
  page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: 'Title' }) });

test.describe('Defects table — filter / sort', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    await seedActiveProject(page, PROJECT);
    await mockDefects(page);
  });

  test('the status tabs filter the table client-side', async ({ page }) => {
    await page.goto('/defects');
    // Default "All" tab shows every defect.
    await expect(page.getByText('test_login_open')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('test_pay_resolved')).toBeVisible();

    // Switch to the Resolved tab → only the RESOLVED defect remains.
    await page.getByRole('tab', { name: /Resolved/i }).click();

    await expect(page.getByText('test_pay_resolved')).toBeVisible();
    await expect(page.getByText('test_login_open')).toHaveCount(0);
    await expect(page.getByText('test_cart_inprogress')).toHaveCount(0);
  });

  test('the search box narrows the table by test name', async ({ page }) => {
    await page.goto('/defects');
    await expect(page.getByText('test_login_open')).toBeVisible({ timeout: 10000 });

    await page.getByRole('searchbox', { name: /Search defects/i }).fill('cart');

    await expect(page.getByText('test_cart_inprogress')).toBeVisible();
    await expect(page.getByText('test_login_open')).toHaveCount(0);
    await expect(page.getByText('test_pay_resolved')).toHaveCount(0);
  });

  test('sorting by Status reorders the rows (oldest-by-age → OPEN-first)', async ({ page }) => {
    await page.goto('/defects');
    const table = defectsTable(page);
    // Default sort is Age desc → the oldest defect (the CLOSED one) is first.
    await expect(table.locator('tbody tr').first()).toContainText('test_search_closed', { timeout: 10000 });

    // Sort by Status → OPEN rows lead, so the OPEN defect becomes first.
    await page.getByRole('columnheader', { name: /Status/ }).click();

    await expect(table.locator('tbody tr').first()).toContainText('test_login_open');
  });
});
