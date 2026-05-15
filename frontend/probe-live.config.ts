/**
 * Standalone playwright config for the live homelab probe — bypasses
 * ``tests/e2e/global-setup.ts`` which assumes a localhost dev server.
 */
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  testMatch: 'probe-live.spec.ts',
  timeout: 60_000,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://testlookup.local',
    headless: true,
    trace: 'retain-on-failure',
    // Bypass the e2e auth state so we sign in fresh inside the spec.
    storageState: undefined,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
