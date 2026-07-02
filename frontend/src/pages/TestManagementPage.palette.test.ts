/**
 * Regression: TestManagementPage renders status/priority/decision surfaces
 * through per-theme CSS tokens (--status-*, --color-*) instead of raw Tailwind
 * palette classes, so they stay legible on the light themes.
 *
 * Context (2026-07-02): the design-audit palette-token ratchet
 * (`no-restricted-syntax` in eslint.config.js) flagged 74 raw palette classes
 * in this page — every one bypassed the token system and was a light-theme
 * legibility defect. They were mapped by semantic role (passed/failed/broken/
 * skipped greens/reds/oranges+ambers/yellows; AI+decision purples → color;
 * info blues → accent) to tokens defined per-theme in src/index.css.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (matches the
 * sibling refs / suite-aggregates regressions — no Node built-ins so the
 * production ``tsc`` build doesn't trip on this test). Reintroducing a raw
 * palette class re-trips the now-tokenized page and fails here before the
 * lint ratchet does.
 */
import { describe, expect, it } from 'vitest'

import pageSource from './TestManagementPage.tsx?raw'

// The exact families the `no-restricted-syntax` rule flags.
const FLAGGED = /(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)-\d{2,3}/

describe('TestManagementPage palette-token ratchet (regression)', () => {
  it('has no raw palette classes the design-audit rule flags', () => {
    expect(pageSource).not.toMatch(FLAGGED)
  })

  it('renders status surfaces through per-theme status tokens', () => {
    expect(pageSource).toContain('var(--status-passed)')
    expect(pageSource).toContain('var(--status-failed)')
    expect(pageSource).toContain('var(--status-broken)')
    expect(pageSource).toContain('var(--status-skipped)')
  })

  it('routes AI/decision purples and info blues to their theme tokens', () => {
    expect(pageSource).toContain('var(--color-purple)')
    expect(pageSource).toContain('var(--color-accent)')
  })
})
