import { describe, it, expect } from 'vitest'

import { deriveTestManagementTotals, describeStatBasis } from './testManagementTotals'

describe('deriveTestManagementTotals', () => {
  it('pins the regression that produced "Cases 25 of 0"', () => {
    // The recurring scenario: managed_test_cases is empty for this
    // project so ``healthRoll`` (fetched WITHOUT include_automation)
    // returns total=0. The table query (``data``) IS fetched with
    // include_automation=true and returns total=25 because automation
    // rows merged. Before the split, both UI sites read the same
    // ``totalCases = healthRoll.total ?? data.total ?? ...`` chain,
    // and the table header ended up showing "Cases 25 of 0" because
    // ``??`` returned 0 from healthRoll (defined-and-falsy ≠ unset).
    const totals = deriveTestManagementTotals({
      healthRoll: { total: 0 },
      data: { total: 25 },
      casesLength: 25,
    })
    expect(totals.casesTotal).toBe(25)
    expect(totals.authoredTotal).toBe(0)
  })

  it('uses data.total for the table header (managed + automation)', () => {
    // Project with both authored + automation rows. ``data.total`` is
    // the merged count from the cases endpoint.
    const totals = deriveTestManagementTotals({
      healthRoll: { total: 7 },
      data: { total: 50 },
      casesLength: 25,
    })
    expect(totals.casesTotal).toBe(50)
    expect(totals.authoredTotal).toBe(7)
  })

  it('falls back to rendered row count when ``data`` has not loaded yet', () => {
    // SWR is in-flight — ``data`` is undefined. The header should show
    // the currently-rendered row count instead of zero so it doesn't
    // flash "of 0" during the first render.
    const totals = deriveTestManagementTotals({
      healthRoll: undefined,
      data: undefined,
      casesLength: 0,
    })
    expect(totals.casesTotal).toBe(0)
    expect(totals.authoredTotal).toBe(0)
  })

  it('authoredTotal is 0 when healthRoll has not loaded yet', () => {
    // Whereas the table header gracefully falls back to ``casesLength``,
    // the Library Health panel intentionally treats "haven't loaded" as
    // "no authored cases" and renders the empty-catalog copy. That
    // matches the user's eventual end state — no work pretending the
    // catalog might be larger.
    const totals = deriveTestManagementTotals({
      healthRoll: undefined,
      data: { total: 25 },
      casesLength: 25,
    })
    expect(totals.authoredTotal).toBe(0)
    expect(totals.casesTotal).toBe(25)
  })

  it('preserves a non-zero managed total even when automation rows merge', () => {
    // A project with 12 authored cases + many automation rows. The
    // Library Health panel should show 12, not the merged 200.
    const totals = deriveTestManagementTotals({
      healthRoll: { total: 12 },
      data: { total: 200 },
      casesLength: 25,
    })
    expect(totals.authoredTotal).toBe(12)
    expect(totals.casesTotal).toBe(200)
  })

  it('honours null inputs the same as undefined', () => {
    // Defensive: some SWR call paths can return null on error.
    const totals = deriveTestManagementTotals({
      healthRoll: null,
      data: null,
      casesLength: 3,
    })
    expect(totals.casesTotal).toBe(3)
    expect(totals.authoredTotal).toBe(0)
  })
})

describe('describeStatBasis', () => {
  // The Library Health panel computes every rate from `healthRoll.items`, a
  // page capped at size: 200, while printing `healthRoll.total` beside them.
  // Past 200 authored cases those rates describe a slice, not the catalog.
  it('says nothing when the sample IS the catalog', () => {
    expect(describeStatBasis(42, 42)).toBeNull()
  })

  it('says nothing when the server reports fewer than we hold', () => {
    // Can happen mid-delete; a note claiming a sample would be wrong.
    expect(describeStatBasis(200, 12)).toBeNull()
  })

  it('names both numbers when the roll is truncated', () => {
    const note = describeStatBasis(200, 1340)
    expect(note).toContain('200 of 1340')
    // It must say the rates are NOT the whole catalog -- that is the point.
    expect(note).toMatch(/not the whole catalog/i)
  })

  it('says nothing for an empty or nonsensical sample', () => {
    expect(describeStatBasis(0, 1340)).toBeNull()
    expect(describeStatBasis(Number.NaN, 10)).toBeNull()
    expect(describeStatBasis(10, Number.NaN)).toBeNull()
  })
})
