import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
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
      // Measured 2026-09-15: 60.80 / 56.12 / 52.34 / 62.26. Keep a small
      // operating margin for V8 instrumentation drift while making a material
      // loss of exercised frontend behaviour block CI.
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
