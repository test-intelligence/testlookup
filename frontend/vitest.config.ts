import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

/**
 * Every unit test runs in ONE time zone, west of UTC, whatever the machine's.
 *
 * Without a pin a date test means different things in different places: CI's
 * Ubuntu runner is UTC, where a local-vs-UTC day bug cannot show, so a test
 * written to catch exactly that bug (utcDayLabel dropping `timeZone: 'UTC'`
 * labels every column one day early for anyone west of UTC) passed in CI and
 * failed only on a developer's machine in the Americas (R1 F12). A NEGATIVE
 * offset is the one that exposes it: local midnight there is still the previous
 * UTC day. America/Chicago also has DST, so a test cannot bake in one offset.
 *
 * Set on this process BEFORE any worker starts, so forked workers inherit it,
 * and again through `test.env` for the worker itself; src/test/timeZone.test.ts
 * fails if it ever stops applying (run it under `TZ=UTC` to see the pin win).
 */
export const TEST_TIME_ZONE = 'America/Chicago'
process.env.TZ = TEST_TIME_ZONE

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    env: { TZ: TEST_TIME_ZONE },
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    /**
     * Vitest defaults to roughly (CPU count - 1) workers — 23 on a 24-core dev
     * box. Every worker boots its own jsdom + React module graph, so at that
     * width the suite thrashes memory and unrelated tests fail
     * non-deterministically: 6-10 failures per run across useSuites,
     * ValueMetricsPage, AIConfigPage, useSeedStatus and useWebVitals, a
     * DIFFERENT set each run, every one of them passing in isolation. The
     * symptom is a 5s test timeout, and the "found multiple elements" errors
     * that follow are the knock-on — a timed-out test never reaches RTL
     * cleanup, so the next test in the file sees the previous render's DOM.
     *
     * Halving the width fixed it (3 consecutive clean full runs at 6 workers,
     * 2 at 12; the default failed every run). A percentage keeps this correct
     * on smaller CI runners, where 50% of 2-4 cores is 1-2 workers.
     */
    maxWorkers: '50%',
    /**
     * The default 5s is too tight for the page-level tests, which dynamically
     * `await import()` a large page module and then drive real SWR hooks. That
     * is a genuinely slow operation, not a hang, and it tipped over the limit
     * under load even at half worker width. A truly hung test still fails here
     * — just after 15s instead of misreporting a slow one as broken.
     */
    testTimeout: 15_000,
    coverage: {
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/**/*.d.ts'],
      // The gate (statements / branches / functions / lines) is 60 / 55 / 51 / 61.
      // Last measured 2026-09-29 (Wave 2.5, full `vitest run --coverage`, 386
      // files): 76.48 / 68.98 / 68.20 / 77.90. The gate was set 2026-09-15 at
      // 60.80 / 56.12 / 52.34 / 62.26 with a small margin for V8
      // instrumentation drift, so that a material loss of exercised frontend
      // behaviour blocks CI; coverage has grown since and the gate has not been
      // raised with it.
      thresholds: {
        statements: 60,
        branches: 55,
        functions: 51,
        lines: 61,
      },
    },
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
})
