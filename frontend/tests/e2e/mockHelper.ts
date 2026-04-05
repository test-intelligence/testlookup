import { Page } from '@playwright/test';

export async function mockLogin(page: Page) {
  // Mock login endpoint
  await page.route('**/api/v1/auth/login', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'fake-access-token',
        refresh_token: 'fake-refresh-token'
      })
    });
  });

  // Mock user details endpoint
  await page.route('**/api/v1/auth/me', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 1,
        username: 'admin',
        email: 'admin@testlookup.com',
        role: 'admin',
        is_active: true
      })
    });
  });

  // Mock dashboard overview endpoint to prevent further errors
  await page.route('**/api/v1/dashboard/*', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
  });
  
  // Mock standard test runs endpoint
  await page.route('**/api/v1/runs*', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

export async function performMockLogin(page: Page) {
  await mockLogin(page);
  await page.goto('/login');
  
  // Provide input and submit
  const emailInput = page.locator('input[name="username"]');
  const passwordInput = page.locator('input[name="password"]');
  const submitButton = page.locator('button[type="submit"]');
  
  await emailInput.fill('admin@testlookup.com');
  await passwordInput.fill(process.env['E2E_ADMIN_PASSWORD'] ?? '');
  await submitButton.click();
  
  // Wait for the UI to redirect to /overview
  await page.waitForURL('**/overview');
}
