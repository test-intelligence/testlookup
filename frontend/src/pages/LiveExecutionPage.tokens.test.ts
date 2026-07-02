/**
 * Regression: /live palette tokens (design-audit ratchet).
 *
 * LiveExecutionPage previously hard-coded raw Tailwind palette classes
 * (text-emerald-400, bg-red-500, bg-amber-900/30, border-red-500/45, …)
 * for its status colours. Every one bypassed the per-theme CSS-token
 * system in ``src/index.css`` and rendered illegibly on the light
 * themes (e.g. emerald-300 text on a pale surface).
 *
 * Fix: the pass-rate helper, WS-status badge, LIVE badge, pass/fail
 * bars, session status/failed cells, workflow subway cards, and the
 * pipeline-events feed all resolve their success/warning/failure tone
 * through ``--status-passed`` / ``--status-broken`` / ``--status-failed``
 * (and the -bg/-bd variants) instead of raw palette classes.
 *
 * Strategy: pull the page source via Vite's ``?raw`` import (no Node
 * built-ins, so the production ``tsc`` build doesn't trip on this test)
 * and assert the status tokens are present while the raw palette
 * classes for the converted roles are gone. Catches an accidental
 * revert that would re-introduce a light-theme legibility defect.
 */
import { describe, expect, it } from 'vitest'

// Vite ``?raw`` query imports the file as a string at build time.
import pageSource from './LiveExecutionPage.tsx?raw'

describe('LiveExecutionPage — palette tokens (regression)', () => {
  it('resolves status colours through the CSS status tokens', () => {
    expect(pageSource).toMatch(/text-\[var\(--status-passed\)\]/)
    expect(pageSource).toMatch(/text-\[var\(--status-failed\)\]/)
    expect(pageSource).toMatch(/text-\[var\(--status-broken\)\]/)
    // Solid pass/fail bars + dots use the vivid token as a background.
    expect(pageSource).toMatch(/bg-\[var\(--status-passed\)\]/)
    expect(pageSource).toMatch(/bg-\[var\(--status-failed\)\]/)
    // Idle/stale badge surface uses the -bg / -bd variants.
    expect(pageSource).toMatch(/bg-\[var\(--status-broken-bg\)\]/)
    expect(pageSource).toMatch(/border-\[var\(--status-failed-bd\)\]/)
  })

  it('no longer hard-codes the converted raw palette classes', () => {
    // The exact raw classes that used to render illegibly on light themes.
    expect(pageSource).not.toMatch(/text-emerald-\d/)
    expect(pageSource).not.toMatch(/text-red-\d/)
    expect(pageSource).not.toMatch(/text-amber-\d/)
    expect(pageSource).not.toMatch(/text-yellow-\d/)
    expect(pageSource).not.toMatch(/bg-emerald-\d/)
    expect(pageSource).not.toMatch(/bg-red-\d/)
    expect(pageSource).not.toMatch(/border-red-\d/)
  })
})
