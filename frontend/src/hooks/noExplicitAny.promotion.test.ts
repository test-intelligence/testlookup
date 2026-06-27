/**
 * Regression: @typescript-eslint/no-explicit-any is an error and the last
 * production site (useTableSort) stays free of `any`.
 *
 * Context (2026-06-27): no-explicit-any was the warn-level TypeScript rule with
 * no remaining production violations. The final source case was useTableSort's
 * generic constraint `T extends Record<string, any>`, which existed only so the
 * hook could index rows by a runtime sort key. The constraint was dropped to a
 * plain `<T>` (so interface-typed rows like ManagedTestCase still satisfy it and
 * `sorted` keeps its `T[]` element type), and the dynamic index now goes through
 * a localized `as Record<string, unknown>` view — the sort body already narrows
 * each value with runtime `typeof` checks and a `String()` fallback. The one
 * remaining `any` in the tree is a test-only `File.prototype` mock that carries a
 * scoped disable. Promoting the rule to error guards against reintroducing
 * untyped `any` in app code.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (mirrors the
 * sibling react-hooks promotion regressions — no Node built-ins, so the
 * production ``tsc`` build doesn't trip on this test).
 */
import { describe, expect, it } from 'vitest'

import eslintConfigSource from '../../eslint.config.js?raw'
import useTableSortSource from './useTableSort.ts?raw'

describe('@typescript-eslint/no-explicit-any promotion (regression)', () => {
  it('eslint config pins no-explicit-any to error', () => {
    expect(eslintConfigSource).toMatch(/'@typescript-eslint\/no-explicit-any':\s*'error'/)
    expect(eslintConfigSource).not.toMatch(/'@typescript-eslint\/no-explicit-any':\s*'warn'/)
  })

  it('useTableSort no longer uses an `any` generic constraint', () => {
    expect(useTableSortSource).not.toMatch(/Record<string,\s*any>/)
    expect(useTableSortSource).not.toMatch(/no-explicit-any/)
    // The dynamic-index view stays typed as `unknown`, not `any`.
    expect(useTableSortSource).toMatch(/as Record<string,\s*unknown>/)
  })
})
