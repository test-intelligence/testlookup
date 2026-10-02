/**
 * The hermetic LCP harness (Wave 2.6, plan 4.1 A): `tests/perf/lcp.spec.ts`
 * against a PRODUCTION build served by `vite preview`.
 *
 * Run by hand, on a quiet machine (no other builds, tests or agents), never
 * in CI: it measures wall-clock paint times, and a wall-clock assertion in CI
 * measures the runner, not the change. It asserts nothing about the numbers;
 * it records them (see the spec for what, and where).
 *
 *   npx playwright test --config playwright.perf.config.ts
 *
 * The web server BUILDS the tree first (`vite build`, no `tsc`: the type
 * check changes no byte of `dist/`), so the numbers are always the current
 * tree's. `LCP_SKIP_BUILD=1` serves the existing `dist/` as it is (only for
 * a `dist/` you just built from this tree). `LCP_PORT` moves the server.
 * Comparing two trees in ONE run: see `LCP_CELLS` in the spec.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = Number(process.env.LCP_PORT ?? 4180)
const preview = `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`

// One id for the whole run, fixed here in the runner process before any
// worker starts; workers inherit it, so a worker restarted after a failure
// keeps appending to the same results file.
process.env.LCP_RUN_ID ??= new Date().toISOString().replace(/[:.]/g, '-')

export default defineConfig({
  testDir: './tests/perf',
  // Playwright empties its output folder at the start of a run; the results
  // file lives beside it (`test-results/lcp/`), so it survives.
  outputDir: './test-results/perf-artifacts',
  timeout: 90_000,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  // One page at a time: two measured pages would share the CPU.
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'off',
    video: 'off',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 },
    },
  ],
  webServer: {
    command: process.env.LCP_SKIP_BUILD ? preview : `npx vite build && ${preview}`,
    url: `http://127.0.0.1:${PORT}/login`,
    // Never measure whatever happens to be listening on the port.
    reuseExistingServer: false,
    timeout: 300_000,
  },
})
