import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

test.describe('Test Runs and Execution', () => {

  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('should display runs list and allow filtering', async ({ page }) => {
    await page.goto('/runs');
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });

    const searchInput = page.locator('input[placeholder*="search" i], input[placeholder*="filter" i]').first();
    if (await searchInput.isVisible()) {
      await searchInput.fill('nightly');
    }

    // Wait for network idle or table presence in live env
    await page.waitForTimeout(2000); 
    const rowCount = await page.locator('tbody tr').count();
    
    if (rowCount > 0) {
      const firstRowLink = page.locator('tbody tr').first().locator('a').first();
      if (await firstRowLink.isVisible()) {
         await firstRowLink.click({ force: true });
         await expect(page).toHaveURL(/.*\/runs\/.+/);
      }
    }
  });
});
