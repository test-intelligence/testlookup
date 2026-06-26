/**
 * Regression: react-hooks/exhaustive-deps is an error and the last two flagged
 * sites keep their memoized SWR-derived arrays.
 *
 * Context (2026-06-26): exhaustive-deps was the final react-hooks v7 rule still
 * at `warn`. The last two violations were the same shape — an SWR-derived array
 * recreated as a fresh literal every render and then used as a useMemo
 * dependency, defeating the downstream memo:
 *   • ReleasesPage:        `const releases = data?.items ?? []`
 *   • TestManagementPage:  `const userList = (users ?? []) as UserSummary[]`
 * Each was wrapped in its own useMemo (the fix the rule itself recommends),
 * matching the `useMemo(() => … ?? [], [..])` pattern already in those files.
 * Promoting the rule to error guards against reintroducing the unmemoized form.
 *
 * Strategy: source-text invariants via Vite's ``?raw`` import (mirrors the
 * sibling react-hooks/refs regression — no Node built-ins, so the production
 * ``tsc`` build doesn't trip on this test). Reintroducing a bare
 * `data?.items ?? []` / `users ?? []` dependency would re-trip the now-error
 * rule and fails here before CI does.
 */
import { describe, expect, it } from 'vitest'

import eslintConfigSource from '../../eslint.config.js?raw'
import releasesSource from './ReleasesPage.tsx?raw'
import testMgmtSource from './TestManagementPage.tsx?raw'

describe('react-hooks/exhaustive-deps promotion (regression)', () => {
  it('eslint config pins react-hooks/exhaustive-deps to error', () => {
    expect(eslintConfigSource).toMatch(/'react-hooks\/exhaustive-deps':\s*'error'/)
    expect(eslintConfigSource).not.toMatch(/'react-hooks\/exhaustive-deps':\s*'warn'/)
  })

  it('ReleasesPage memoizes the SWR-derived releases array', () => {
    expect(releasesSource).toMatch(
      /const releases:\s*Release\[\]\s*=\s*useMemo\(\(\)\s*=>\s*data\?\.items\s*\?\?\s*\[\],\s*\[data\]\)/,
    )
    // The unmemoized literal that tripped the rule must not return.
    expect(releasesSource).not.toMatch(/const releases:\s*Release\[\]\s*=\s*data\?\.items\s*\?\?\s*\[\]\s*$/m)
  })

  it('TestManagementPage memoizes the SWR-derived userList array', () => {
    expect(testMgmtSource).toMatch(
      /const userList\s*=\s*useMemo\(\(\)\s*=>\s*\(users\s*\?\?\s*\[\]\)\s*as\s*UserSummary\[\],\s*\[users\]\)/,
    )
    expect(testMgmtSource).not.toMatch(/const userList\s*=\s*\(users\s*\?\?\s*\[\]\)\s*as\s*UserSummary\[\]\s*$/m)
  })
})
