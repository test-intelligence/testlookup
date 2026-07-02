/**
 * Regression: SuiteDetailPage renders every status surface (KPI cards, status
 * badge, per-test + recent-run tables, the flaky pill and the missing-detail
 * warning box) through per-theme CSS tokens (--status-*) instead of raw
 * Tailwind palette classes, so they stay legible on the light themes.
 *
 * Context (2026-07-02): the design-audit palette-token ratchet
 * (`no-restricted-syntax` in eslint.config.js) flagged 26 raw palette classes
 * in this page — every one bypassed the token system and was a light-theme
 * legibility defect. They were mapped by semantic role (passed greens →
 * --status-passed; failed reds → --status-failed; broken/warning oranges+ambers
 * → --status-broken; skipped ambers → --status-skipped; the flaky pill →
 * --status-flaky) to tokens defined per-theme in src/index.css.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (matches the
 * sibling TestManagementPage.palette regression — no Node built-ins so the
 * production ``tsc`` build doesn't trip on this test). Reintroducing a raw
 * palette class re-trips the now-tokenized page and fails here before the
 * lint ratchet does. Recharts fill/stroke hex values and the cyan avg-duration
 * KPI stay untouched — they are not Tailwind classes the rule flags.
 */
import { describe, expect, it } from 'vitest'

import pageSource from './SuiteDetailPage.tsx?raw'

// The exact families the `no-restricted-syntax` rule flags.
const FLAGGED = /(text|bg|border|ring)-(emerald|green|red|amber|yellow|orange|purple|blue)-\d{2,3}/

describe('SuiteDetailPage palette-token ratchet (regression)', () => {
  it('has no raw palette classes the design-audit rule flags', () => {
    expect(pageSource).not.toMatch(FLAGGED)
  })

  it('renders status surfaces through per-theme status tokens', () => {
    expect(pageSource).toContain('var(--status-passed)')
    expect(pageSource).toContain('var(--status-failed)')
    expect(pageSource).toContain('var(--status-broken)')
    expect(pageSource).toContain('var(--status-skipped)')
  })

  it('routes the flaky pill to the flaky status token', () => {
    expect(pageSource).toContain('var(--status-flaky)')
    expect(pageSource).toContain('var(--status-flaky-bg)')
    expect(pageSource).toContain('var(--status-flaky-bd)')
  })
})
