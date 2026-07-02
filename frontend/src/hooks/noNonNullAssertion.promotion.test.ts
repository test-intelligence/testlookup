/**
 * Regression: @typescript-eslint/no-non-null-assertion is an error, closing the
 * final warn-level TypeScript ratchet.
 *
 * Context (2026-07-02): no-non-null-assertion was the last TypeScript rule left
 * at 'warn'. Every `x!` non-null assertion in src/ had already been replaced
 * with a real guard, early-return, default, or narrowing as its owning
 * page/area was cleaned up over successive ratchet passes — e.g. the suite-keyed
 * SWR fetchers moved from `suiteId!` to a `([, id]) => …` tuple-key destructure
 * (see useSuites.test.ts), and useLatestSuiteCompare replaced `suiteName!.trim()`
 * with an explicit `if (!suiteName) throw` guard (see useRunCompare.test.ts) —
 * leaving zero remaining sites. Promoting the rule to error guards against
 * reintroducing unchecked `!` assertions, which silence the compiler's
 * null/undefined analysis and turn a would-be type error into a runtime crash.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (mirrors the
 * sibling no-explicit-any / react-hooks promotion regressions — no Node
 * built-ins, so the production ``tsc`` build doesn't trip on this test).
 */
import { describe, expect, it } from 'vitest'

import eslintConfigSource from '../../eslint.config.js?raw'

describe('@typescript-eslint/no-non-null-assertion promotion (regression)', () => {
  it('eslint config pins no-non-null-assertion to error', () => {
    expect(eslintConfigSource).toMatch(/'@typescript-eslint\/no-non-null-assertion':\s*'error'/)
    expect(eslintConfigSource).not.toMatch(/'@typescript-eslint\/no-non-null-assertion':\s*'warn'/)
  })
})
