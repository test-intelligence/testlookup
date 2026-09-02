/**
 * Regression: the frontend lint toolchain is on ESLint 10, and the
 * `minimatch` → `brace-expansion` override keeps it loadable.
 *
 * Context (2026-09-02): supersedes Dependabot #921 (@eslint/js 10 alone), which
 * could not land — @eslint/js 10 peers `eslint: ^10`, so bumping it without
 * eslint itself leaves an unsatisfiable peer. eslint and @eslint/js were bumped
 * 9→10 together.
 *
 * The subtle part is a dependency-override collision. ESLint 10 resolves its
 * config globs through `@eslint/config-array` → `minimatch@10`, which imports
 * the **named** `expand` export from `brace-expansion` (`brace_expansion_1.expand`,
 * the v2+/v5 API). The repo carries a global `brace-expansion: 1.1.13` override
 * (a ReDoS-CVE safety pin) — but v1 exports the function as the module itself
 * with no `.expand` property, so forcing it onto minimatch@10 crashes eslint at
 * startup with `(0 , brace_expansion_1.expand) is not a function` and every lint
 * run dies. minimatch is the ONLY brace-expansion consumer in the tree, so a
 * `minimatch`-scoped override routes it to a v5 (named-export, CVE-patched)
 * build while the global v1 pin stays as a safety net for any future non-minimatch
 * consumer. This test guards against a well-meaning "simplify the overrides"
 * change that drops the minimatch scope and silently re-breaks `npm run lint`.
 *
 * Strategy: source-text invariants over package.json via Vite's ``?raw`` import
 * (mirrors the sibling ratchet-promotion regressions — no Node built-ins, so the
 * production ``tsc`` build doesn't trip on this test).
 */
import { describe, expect, it } from 'vitest'

import packageJsonSource from '../../package.json?raw'

describe('ESLint 10 upgrade (regression)', () => {
  const pkg = JSON.parse(packageJsonSource) as {
    devDependencies: Record<string, string>
    overrides: Record<string, unknown>
  }

  it('pins eslint and @eslint/js to the same v10 major', () => {
    expect(pkg.devDependencies.eslint).toMatch(/^\^?10\./)
    expect(pkg.devDependencies['@eslint/js']).toMatch(/^\^?10\./)
  })

  it('routes minimatch to a v5 brace-expansion so eslint 10 can load its config', () => {
    const minimatchOverride = pkg.overrides.minimatch as
      | { 'brace-expansion'?: string }
      | undefined
    const braceForMinimatch = minimatchOverride?.['brace-expansion']

    // Must exist and be v5+ (the named-export `expand` API minimatch@10 calls) —
    // NOT the global v1 pin, whose default-only export crashes eslint 10.
    expect(braceForMinimatch).toBeDefined()
    expect(braceForMinimatch).toMatch(/^[5-9]\./)
    expect(braceForMinimatch).not.toMatch(/^1\./)
  })

  it('keeps the global brace-expansion CVE pin as a safety net', () => {
    // The v1 pin still guards any future non-minimatch consumer against the
    // brace-expansion ReDoS CVE; only the minimatch path is scoped off it.
    expect(pkg.overrides['brace-expansion']).toBeDefined()
  })
})
