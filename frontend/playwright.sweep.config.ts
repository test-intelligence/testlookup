import { defineConfig } from '@playwright/test'

/**
 * Route and action sweeps against a LIVE deployment (see tests/sweeps/common.ts).
 * Not part of the hermetic CI lanes: they need a running backend with data.
 *
 *   SWEEP_BASE_URL=http://testlookup.local SWEEP_FIXTURES=/tmp/sweep-fixtures.json \
 *     npx playwright test -c playwright.sweep.config.ts [route-sweep|action-sweep]
 *
 * One worker by default: a sweep should not be the load that breaks the deployment.
 */
export default defineConfig({
  testDir: './tests/sweeps',
  testMatch: /.*-sweep\.spec\.ts/,
  timeout: 120_000,
  fullyParallel: true,
  workers: Number(process.env.SWEEP_WORKERS ?? 1),
  retries: 0,
  reporter: [['line'], ['json', { outputFile: 'test-results/sweeps/report.json' }]],
  use: {
    channel: process.env.SWEEP_CHANNEL ?? undefined,
    headless: true,
    actionTimeout: 8_000,
    navigationTimeout: 30_000,
  },
})
