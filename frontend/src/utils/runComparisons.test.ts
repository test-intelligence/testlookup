/**
 * Tests for ``findPreviousRunOfSuite`` and ``buildCompareWithPreviousHref``
 * — the two pure helpers behind the "Compare with previous run" CTAs
 * on /runs (per-row icon) and /runs/<id> (header action).
 *
 * The same helper pair powers both surfaces, so pinning the URL +
 * lookup contract here lets the page-level tests stay focused on
 * wiring instead of duplicating the logic in two places.
 */
import { describe, expect, it } from 'vitest'

import {
  buildCompareWithPreviousHref,
  findPreviousRunOfSuite,
} from './runComparisons'

function _run(overrides: Partial<{
  id: string
  created_at: string
  primary_suite_name: string | null
}>) {
  return {
    id: '00000000-0000-0000-0000-000000000000',
    created_at: '2026-05-16T00:00:00Z',
    primary_suite_name: 'payments-e2e',
    ...overrides,
  }
}

describe('findPreviousRunOfSuite', () => {
  it('returns null when the current run has no suite attribution', () => {
    // Without a suite we can't filter — partial-overlap suite_names is
    // intentionally not consulted (see helper docstring).
    const current = _run({ id: 'cur', primary_suite_name: null })
    const candidates = [
      _run({ id: 'a', primary_suite_name: 'payments-e2e', created_at: '2026-05-15T00:00:00Z' }),
    ]
    expect(findPreviousRunOfSuite(current, candidates)).toBeNull()
  })

  it('returns null when no candidate is older than the current run', () => {
    const current = _run({ id: 'cur', created_at: '2026-05-10T00:00:00Z' })
    const candidates = [
      _run({ id: 'a', created_at: '2026-05-11T00:00:00Z' }), // newer
      _run({ id: 'b', created_at: '2026-05-12T00:00:00Z' }), // newer
    ]
    expect(findPreviousRunOfSuite(current, candidates)).toBeNull()
  })

  it('returns null when older candidates exist but none match the suite', () => {
    const current = _run({ id: 'cur', primary_suite_name: 'auth-api' })
    const candidates = [
      _run({ id: 'a', primary_suite_name: 'payments-e2e', created_at: '2026-05-15T00:00:00Z' }),
    ]
    expect(findPreviousRunOfSuite(current, candidates)).toBeNull()
  })

  it('picks the chronologically immediately preceding same-suite run', () => {
    const current = _run({ id: 'cur', created_at: '2026-05-16T12:00:00Z' })
    const candidates = [
      _run({ id: 'older',   created_at: '2026-05-14T12:00:00Z' }),
      _run({ id: 'newest_before', created_at: '2026-05-16T08:00:00Z' }),
      _run({ id: 'oldest',  created_at: '2026-05-10T12:00:00Z' }),
    ]
    expect(findPreviousRunOfSuite(current, candidates)?.id).toBe('newest_before')
  })

  it('excludes the current run itself even if it appears in the candidate pool', () => {
    // Pages routinely pass the full ``runs`` array which includes the
    // current row; the helper must not return the row to itself.
    const current = _run({ id: 'cur', created_at: '2026-05-16T12:00:00Z' })
    const candidates = [
      current,
      _run({ id: 'older', created_at: '2026-05-15T12:00:00Z' }),
    ]
    expect(findPreviousRunOfSuite(current, candidates)?.id).toBe('older')
  })

  it('breaks same-timestamp ties deterministically by id', () => {
    // Two runs at exactly the same created_at would otherwise produce
    // a non-deterministic order across renders / re-fetches. Tie-break
    // by id (lexicographic) so unit tests + user behaviour line up.
    const current = _run({ id: 'cur', created_at: '2026-05-16T12:00:00Z' })
    const candidates = [
      _run({ id: 'zzz', created_at: '2026-05-15T12:00:00Z' }),
      _run({ id: 'aaa', created_at: '2026-05-15T12:00:00Z' }),
    ]
    expect(findPreviousRunOfSuite(current, candidates)?.id).toBe('aaa')
  })
})

describe('buildCompareWithPreviousHref', () => {
  it('returns null when there is no previous run', () => {
    expect(buildCompareWithPreviousHref(_run({ id: 'cur' }), null)).toBeNull()
  })

  it('builds /runs/compare with left=previous, right=current, and the suite filter', () => {
    const href = buildCompareWithPreviousHref(
      _run({ id: 'cur', primary_suite_name: 'payments-e2e' }),
      _run({ id: 'prev', primary_suite_name: 'payments-e2e' }),
    )
    expect(href).toContain('/runs/compare?')
    expect(href).toContain('mode=manual')
    // left = the older baseline, right = what the user is investigating.
    expect(href).toContain('left=prev')
    expect(href).toContain('right=cur')
    expect(href).toContain('suite=payments-e2e')
  })

  it('falls back to the previous run\'s suite when current has none', () => {
    // Rare but possible — a partial-ingest current run might be missing
    // primary_suite_name while the previous run has it. Use whichever
    // is present so the compare page's suite filter still anchors.
    const href = buildCompareWithPreviousHref(
      _run({ id: 'cur', primary_suite_name: null }),
      _run({ id: 'prev', primary_suite_name: 'payments-e2e' }),
    )
    expect(href).toContain('suite=payments-e2e')
  })

  it('omits the suite param when neither side has a suite name', () => {
    // Adding ``&suite=`` (empty) would land on the compare page with a
    // stale empty filter. Cleaner to leave it off so the compare page
    // falls back to its default behaviour.
    const href = buildCompareWithPreviousHref(
      _run({ id: 'cur', primary_suite_name: null }),
      _run({ id: 'prev', primary_suite_name: null }),
    )
    expect(href).not.toContain('suite=')
  })

  it('URL-encodes special characters in the suite name', () => {
    const href = buildCompareWithPreviousHref(
      _run({ id: 'cur', primary_suite_name: 'Realistic TestNG client examples' }),
      _run({ id: 'prev', primary_suite_name: 'Realistic TestNG client examples' }),
    )
    // Both sides carry a suite name, so a compare href must be produced.
    // Assert non-null and narrow with a throw (instead of a `!`) so the
    // subsequent match runs against a `string`, not `string | null`.
    expect(href).not.toBeNull()
    if (href === null) throw new Error('buildCompareWithPreviousHref returned null')
    expect(href).toMatch(/suite=Realistic[+%20]TestNG[+%20]client[+%20]examples/)
  })
})
