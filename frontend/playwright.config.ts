import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  // 60 s per test: performRealLogin fast-path (6 s) + real login fallback
  // (20 s) + test body leaves comfortable headroom.
  timeout: 60000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Single worker: tests run sequentially, no parallel load on the backend
  workers: 1,
  reporter: 'html',

  // Run the one-time login before any test file executes
  globalSetup: './tests/e2e/global-setup.ts',

  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    // All test contexts start pre-authenticated (JWT already in localStorage)
    storageState: './tests/e2e/.auth/user.json',
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ]
  // webServer block removed so we use the live server
});
