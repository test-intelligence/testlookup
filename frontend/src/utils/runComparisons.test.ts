/**
 * Tests for ``buildCompareWithPreviousHref``, the pure helper behind the
 * "Compare to previous run" CTAs on /runs (per-row icon) and /runs/<id>
 * (⋯ menu) and the Analysis tab's "vs previous run" card.
 *
 * W03 (D42): which run is "previous" is the server's rule now
 * (`GET /runs/{id}/previous`: same suite AND branch, by finish time), so the
 * client-side pick (``findPreviousRunOfSuite``) and its tests are gone; the
 * pages' tests pin that they ask the server.
 */
import { describe, expect, it } from 'vitest'

import { buildCompareWithPreviousHref } from './runComparisons'

function _run(overrides: Partial<{
  id: string
  primary_suite_name: string | null
}>) {
  return {
    id: '00000000-0000-0000-0000-000000000000',
    primary_suite_name: 'payments-e2e',
    ...overrides,
  }
}

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

  it('takes the server\'s run brief mapped to { id, primary_suite_name }', () => {
    // `GET /runs/{id}/previous` answers a RunBrief keyed `run_id`; the callers map it.
    const brief = { run_id: 'prev', primary_suite_name: 'payments-e2e' }
    const href = buildCompareWithPreviousHref(
      _run({ id: 'cur' }),
      { id: brief.run_id, primary_suite_name: brief.primary_suite_name },
    )
    expect(href).toBe('/runs/compare?mode=manual&left=prev&right=cur&suite=payments-e2e')
  })
})
