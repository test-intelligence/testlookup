/**
 * Regression: creating a test case refreshed the paginated table but not the
 * library-health panel, so the summary asserted "1 cases" beside a list showing
 * two until the 60 s background poll caught up.
 *
 * The cause is that the panel reads its OWN `useTestCases({page:1,size:200})`
 * roll — a different SWR key from the table — and `handleRefresh` only called
 * the table's mutate. Two rolls, one refresh.
 *
 * Found by exploratory testing: author a case, then read the panel without
 * reloading. It is invisible to a reload-based check, because a reload fixes it.
 *
 * Asserted statically rather than by rendering: the page is ~4k lines with a
 * large hook surface, and the invariant — "every cases roll this page holds is
 * revalidated by its refresh handler" — is a property of the source, not of any
 * one rendered state.
 */
import { describe, expect, it } from 'vitest'

import source from './TestManagementPage.tsx?raw'
// UX redesign P4 item 7: the library-health copy is the Insights drawer's card.
import insightsSource from './test-management/InsightsPanel.tsx?raw'

function destructuredCaseRolls(): string[] {
  return [...source.matchAll(/const\s*\{([^{}]+)\}\s*=\s*useTestCases\(/g)].map(match => match[1])
}

function capturedCaseMutations(): string[] {
  return destructuredCaseRolls()
    .map(fields => fields.match(/mutate:\s*(\w+)/)?.[1])
    .filter((alias): alias is string => alias !== undefined)
}

/**
 * W13 TCL-03 (deliberate rewrite): the panel's numbers are the server's library
 * health (`useLibraryHealth`) now, not a second `useTestCases` roll. The
 * invariant is the same — every read a write can stale is revalidated by
 * `handleRefresh` — over the cases roll(s) AND the health read.
 */
function capturedHealthMutations(): string[] {
  return [...source.matchAll(/const\s*\{([^{}]+)\}\s*=\s*useLibraryHealth\(/g)]
    .map(match => match[1].match(/mutate:\s*(\w+)/)?.[1])
    .filter((alias): alias is string => alias !== undefined)
}

describe('TestManagementPage refresh wiring', () => {
  it('captures the mutate of every useTestCases roll it holds, and of the library-health read', () => {
    // Handles both single-line and formatted multi-line destructuring.
    const rolls = [...source.matchAll(/=\s*useTestCases\(/g)]
    expect(rolls.length, 'expected the page to hold the cases roll').toBeGreaterThanOrEqual(1)
    expect(source, 'the 200-row health roll is gone').not.toMatch(/useTestCases\(\{\s*page:\s*1,\s*size:\s*200\s*\}\)/)
    const healthReads = [...source.matchAll(/=\s*useLibraryHealth\(/g)]
    expect(healthReads.length, 'expected the page to read the library health').toBeGreaterThanOrEqual(1)
    expect(capturedHealthMutations().length, "the library-health read's mutate must be captured").toBe(healthReads.length)

    const captured = capturedCaseMutations()
    const plainAliases = destructuredCaseRolls().filter(fields => /\bdata\b/.test(fields) && !/\bmutate\b/.test(fields))

    expect(
      plainAliases.length,
      'a useTestCases roll whose mutate is never captured cannot be revalidated after a write',
    ).toBe(0)
    expect(captured.length).toBeGreaterThanOrEqual(1)
  })

  it('revalidates every captured roll and the library-health read inside handleRefresh', () => {
    const captured = [...capturedCaseMutations(), ...capturedHealthMutations()]
    expect(capturedHealthMutations().length).toBeGreaterThanOrEqual(1)
    const decl = source.indexOf('const handleRefresh')
    expect(decl, 'handleRefresh not found').toBeGreaterThan(-1)

    // Require an INVOCATION, not mere presence. Checking `body.includes(alias)`
    // made this guard blind: deleting `mutateHealthRoll()` from the callback
    // leaves the alias sitting in `[mutateCases, mutateHealthRoll]`, so the
    // presence check still passed while the bug was fully reintroduced.
    const body = source.slice(decl, source.indexOf('\n\n', decl))
    const missing = captured.filter(alias => !new RegExp(`${alias}\\s*\\(`).test(body))

    expect(
      missing,
      'handleRefresh must revalidate each cases roll, or a panel goes stale after a write',
    ).toEqual([])
  })
})

describe('library health copy', () => {
  it('pluralises the case count', () => {
    // Rendered "1 cases" for a single authored case.
    expect(insightsSource).toContain("case{p.totalCases === 1 ? '' : 's'}")
    expect(insightsSource).not.toMatch(/\{p\.totalCases\}<\/strong> cases/)
    // The health banner's count is labelled singular for one case too.
    expect(source).toContain("label: authoredTotal === 1 ? 'Case' : 'Cases'")
  })
})
