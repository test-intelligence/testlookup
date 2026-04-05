import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

test.describe('Defects and Failure Analysis', () => {

  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('should render Failure Analysis page', async ({ page }) => {
    await page.goto('/failures');
    // When no project is selected the page renders an EmptyState <h3>; when a
    // project is selected it renders a PageHeader <h1>.  Either way a heading
    // at any level is present — don't constrain to level 1.
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
  });

  test('should render Defects tracking page', async ({ page }) => {
    await page.goto('/defects');
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
  });
});
