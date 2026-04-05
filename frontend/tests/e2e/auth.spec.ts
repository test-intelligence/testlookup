import { test, expect } from '@playwright/test';

test.describe('Authentication Flow', () => {
  // This suite tests the login flow from scratch, so it must not inherit the
  // pre-authenticated storageState set in playwright.config.ts.
  test.use({ storageState: { cookies: [], origins: [] } });

  test('should allow user to login and redirect to overview', async ({ page }) => {
    await page.goto('/login');

    await expect(page.locator('input[name="username"]')).toBeVisible();

    // Use the native HTMLInputElement prototype setter to set values, then
    // dispatch a synthetic 'input' event with bubbles:true. This is the only
    // cross-browser reliable way to trigger React's synthetic onChange on
    // controlled inputs when running in a cleared-origins security context.
    // fill() and pressSequentially() can both fail to update React state in
    // Chromium's cleared-storage context.
    // IMPORTANT: `process` does not exist in the browser context — pass env
    // values as arguments to page.evaluate().
    const adminPassword = process.env['E2E_ADMIN_PASSWORD'] ?? '';
    await page.evaluate(({ password }) => {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!;

      const u = document.querySelector('input[name="username"]') as HTMLInputElement;
      nativeSetter.call(u, 'admin');
      u.dispatchEvent(new Event('input', { bubbles: true }));

      const p = document.querySelector('input[name="password"]') as HTMLInputElement;
      nativeSetter.call(p, password);
      p.dispatchEvent(new Event('input', { bubbles: true }));
    }, { password: adminPassword });

    // Verify DOM values are set before submitting
    await expect(page.locator('input[name="username"]')).toHaveValue('admin');
    await expect(page.locator('input[name="password"]')).toHaveValue(adminPassword);

    // Intercept the login response to report a clear error if the API fails.
    const [loginResponse] = await Promise.all([
      page.waitForResponse(
        resp => resp.url().includes('/api/v1/auth/login') && resp.request().method() === 'POST',
        { timeout: 15000 },
      ),
      page.locator('button[type="submit"]').click(),
    ]);

    expect(
      loginResponse.status(),
      `Login API returned ${loginResponse.status()} — expected 200`,
    ).toBe(200);

    await page.waitForURL(/.*\/overview/, { timeout: 20000 });
    // Sidebar navigation is always present once authenticated
    await expect(page.getByRole('navigation')).toBeVisible({ timeout: 10000 });
  });

});
