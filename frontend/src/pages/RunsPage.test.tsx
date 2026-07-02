/**
 * Tests for ``buildBisectHref`` — the pure helper that powers every
 * "Bisect from last green" / "Start bisect" / "Bisect from green" CTA
 * on the /runs page.
 *
 * The CTAs used to toast ``"Bisect modal — coming in Phase 2"``; they
 * now navigate to /runs/compare with pre-filled left / right / suite
 * params. Pinning the URL construction here is a cheap regression
 * guard against:
 *
 *   - Stripping the ``suite`` filter when one side has it and the
 *     other doesn't (would silently widen the compare beyond the
 *     suite the user is investigating).
 *   - Reordering left/right (the compare page treats ``left`` as the
 *     baseline; swapping flips every delta's classification).
 *   - Silently returning a partial URL when one side is missing
 *     (would land the user on a half-populated compare page instead
 *     of toasting a clear "no green run found" reason).
 */
import { describe, expect, it } from 'vitest'

import { buildBisectHref } from './RunsPage'

// Build the href and assert it is non-null, returning the narrowed
// string so the URL-introspection cases can read it without a `!`
// non-null assertion (which the lint ratchet forbids). The explicit
// throw narrows the union for TypeScript; the `expect` keeps the
// failure message readable when the helper unexpectedly returns null.
function expectHref(model: Parameters<typeof buildBisectHref>[0]): string {
  const href = buildBisectHref(model)
  expect(href).not.toBeNull()
  if (href === null) throw new Error('buildBisectHref returned null')
  return href
}

function _testRun(overrides: Partial<{
  id: string
  status: string
  primary_suite_name: string | null
  build_number: string
  created_at: string
}> = {}) {
  // Cast to any so the helper sees only the fields it actually reads —
  // mirrors how it's invoked from the page (where the full TestRun has
  // ~20 fields most of which the helper ignores).
  return {
    id: '00000000-0000-0000-0000-000000000001',
    status: 'PASSED',
    primary_suite_name: null,
    build_number: '1',
    created_at: '2026-05-16T00:00:00Z',
    ...overrides,
  } as never
}

describe('buildBisectHref', () => {
  it('returns null when there is no green run to bisect against', () => {
    const href = buildBisectHref({
      lastGreen: null,
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED' }),
    })
    expect(href).toBeNull()
  })

  it('returns null when there is no failing run to bisect', () => {
    const href = buildBisectHref({
      lastGreen: _testRun({ id: 'green-1', status: 'PASSED' }),
      latestFailedRun: null,
    })
    expect(href).toBeNull()
  })

  it('builds a /runs/compare URL with left=lastGreen and right=latestFailed', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'payments-e2e' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: 'payments-e2e' }),
    })
    expect(href).toContain('/runs/compare?')
    expect(href).toContain('mode=manual')
    expect(href).toContain('left=green-1')
    expect(href).toContain('right=failed-1')
    expect(href).toContain('suite=payments-e2e')
  })

  it('prefers the failing run\'s suite when both sides have a suite name', () => {
    // The user is investigating the failing run, so the compare view
    // should default to the failing run's suite filter — not the
    // green run's, even if they differ.
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'old-suite' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: 'new-suite' }),
    })
    expect(href).toContain('suite=new-suite')
    expect(href).not.toContain('suite=old-suite')
  })

  it('falls back to the green run\'s suite when the failing run has none', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'payments-e2e' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: null }),
    })
    expect(href).toContain('suite=payments-e2e')
  })

  it('omits the suite param entirely when neither side has a suite name', () => {
    // Adding ``&suite=`` (empty) would land on the compare page with
    // a stale empty filter. Cleaner to leave it off so the compare
    // page falls back to its default behaviour.
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: null }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: null }),
    })
    expect(href).not.toContain('suite=')
  })

  it('URL-encodes special characters in the suite name', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'g', primary_suite_name: 'Realistic TestNG client examples' }),
      latestFailedRun: _testRun({ id: 'f', status: 'FAILED', primary_suite_name: 'Realistic TestNG client examples' }),
    })
    // Either ``+`` (form-style) or ``%20`` (path-style) are both valid
    // URL-encodings of a space; URLSearchParams uses ``+``.
    expect(href).toMatch(/suite=Realistic[+%20]TestNG[+%20]client[+%20]examples/)
  })
})
