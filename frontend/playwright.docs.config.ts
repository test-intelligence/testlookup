/**
 * Config for the CI diagram-rendering check.
 *
 * Serves the repo with `vite dev` and points at the harness page, so the check
 * needs no backend, no deployment and no credentials — the reason it can run on
 * every push while the live probes cannot.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = 5199

export default defineConfig({
  testDir: './tests',
  testMatch: ['docs-diagrams.spec.ts'],
  timeout: 120_000,
  workers: 1,
  reporter: 'line',
  forbidOnly: !!process.env.CI,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    trace: 'retain-on-failure',
    storageState: undefined,
  },
  webServer: {
    // --host is explicit: Vite 8 binds ::1 only by default, so a 127.0.0.1
    // health check never connects and the server appears to never start.
    command: `npx vite --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/tests/diagram-harness/index.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
