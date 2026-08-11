/**
 * Regression: the design-audit palette-token `no-restricted-syntax` rule is an
 * error, closing the final warn-level ESLint ratchet in the frontend.
 *
 * Context (2026-08-11): raw Tailwind palette classes (text-emerald-400,
 * bg-red-900/40, border-amber-700/60, …) bypass the per-theme CSS-token system
 * (--status-*, --gate-*, --color-* in src/index.css) and are each a light-theme
 * legibility defect. Over successive page-by-page passes every raw class in
 * src/ was mapped by semantic role to its token, leaving zero UI sites. The
 * only remaining literal matches are assertion guards in two test files
 * (RightRail/VerdictBand) that name the raw classes to prove they are ABSENT;
 * those carry scoped disables, mirroring the avatar-color-swatch exemption.
 * Promoting the rule to error guards against reintroducing token-bypassing
 * palette classes in app code.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (mirrors the
 * sibling no-explicit-any / no-non-null-assertion / react-hooks promotion
 * regressions — no Node built-ins, so the production ``tsc`` build doesn't trip
 * on this test).
 */
import { describe, expect, it } from 'vitest'

import eslintConfigSource from '../../eslint.config.js?raw'

describe('no-restricted-syntax palette ratchet promotion (regression)', () => {
  it('eslint config pins no-restricted-syntax to error', () => {
    expect(eslintConfigSource).toMatch(/'no-restricted-syntax':\s*\[\s*'error'/)
    expect(eslintConfigSource).not.toMatch(/'no-restricted-syntax':\s*\[\s*'warn'/)
  })

  it('still targets the raw palette families it was built to catch', () => {
    expect(eslintConfigSource).toContain(
      '(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)',
    )
  })
})
