/**
 * Regression: react-hooks/refs is an error and CasesFilterBar keeps its
 * destructured props.
 *
 * Context (2026-06-16): promoting the react-hooks v7 `refs` rule from warn to
 * error surfaced 18 violations, all in CasesFilterBar. The component took its
 * props as an undestructured `p` object whose `searchInputRef: RefObject`
 * member made the rule treat *every* `p.*` read as a ref-read-during-render.
 * Destructuring the props at the parameter keeps the ref a named binding
 * (forwarded straight to a DOM `ref=`, which the rule allows) and clears all
 * false positives.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (matches the
 * sibling suite-aggregates regression — no Node built-ins so the production
 * ``tsc`` build doesn't trip on this test). A refactor that reintroduces the
 * undestructured props object would re-trip the now-error rule and fails here
 * before CI does.
 */
import { describe, expect, it } from 'vitest'

import eslintConfigSource from '../../eslint.config.js?raw'
import pageSource from './TestManagementPage.tsx?raw'

describe('react-hooks/refs promotion (regression)', () => {
  it('eslint config pins react-hooks/refs to error', () => {
    expect(eslintConfigSource).toMatch(/'react-hooks\/refs':\s*'error'/)
    expect(eslintConfigSource).not.toMatch(/'react-hooks\/refs':\s*'warn'/)
  })

  it('CasesFilterBar destructures its props instead of taking an undestructured `p`', () => {
    // The undestructured form is what tripped the rule — guard against it.
    expect(pageSource).not.toMatch(/function CasesFilterBar\(p:\s*CasesFilterBarProps\)/)
    expect(pageSource).toMatch(/function CasesFilterBar\(\{/)
    expect(pageSource).toMatch(/\}:\s*CasesFilterBarProps\)\s*\{/)
  })

  it('forwards the search ref via the named binding, not p.searchInputRef', () => {
    expect(pageSource).toMatch(/ref=\{searchInputRef\}/)
    expect(pageSource).not.toMatch(/ref=\{p\.searchInputRef\}/)
  })
})
