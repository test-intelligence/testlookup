/**
 * REL-01: the release-health words and counts follow the RECORDED verdict.
 *
 * "Ready to ship" counted in-progress releases at gate `go`, and the gate was
 * always `not_evaluated` (the browser decided it from numbers nothing
 * supplied), so the count was 0 on every project. The lede compared a
 * composite against "the 95% gate". These tests drive the real
 * `deriveRelease` from the server's `gate`, so a mapping that drops it fails
 * here too.
 */
import { describe, expect, it } from 'vitest'

import type { Release, ReleaseGateSummary } from '@/types/releases'
import { deriveRelease } from './mapping'
import {
  NEVER_EVALUATED_LEDE,
  countReleases,
  openBlockerCount,
  releaseHeadline,
  releaseHealthFacts,
  releaseLede,
  verdictAge,
} from './health'

const NOW = Date.parse('2026-10-15T12:00:00Z')
const TWO_HOURS_AGO = new Date(NOW - 2 * 3600 * 1000).toISOString()

function gate(overrides: Partial<ReleaseGateSummary> = {}): ReleaseGateSummary {
  return {
    verdict: 'GO',
    decided_at: TWO_HOURS_AGO,
    pass_rate: 97.5,
    denominator: 42,
    evidence_count: 40,
    run_count: 3,
    status_counts: { PASSED: 39, FAILED: 1 },
    measured: true,
    insufficient_reason: null,
    blocking_reasons: [],
    blocking_total: 0,
    ...overrides,
  }
}

function release(overrides: Partial<Release> = {}): Release {
  return {
    id: 'rel-1',
    project_id: 'proj-1',
    name: '2026.11',
    version: '2026.11',
    description: null,
    status: 'in_progress',
    planned_date: null,
    released_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    phases: [],
    ...overrides,
  }
}

describe('Ready to ship counts an in-progress release whose recorded verdict is GO', () => {
  it('a GO release is ready; a NO_GO, a never-evaluated and a released GO one are not', () => {
    const list = [
      release({ id: 'go', gate: gate() }),
      release({ id: 'nogo', gate: gate({ verdict: 'NO_GO', blocking_reasons: ['x (s) is FAILED'], blocking_total: 1 }) }),
      release({ id: 'never', gate: null }),
      release({ id: 'shipped', status: 'released', released_at: new Date().toISOString(), gate: gate() }),
    ].map(deriveRelease)
    const counts = countReleases(list)
    expect(counts.readyToShip).toBe(1)
    expect(counts.blocked).toBe(1)
    expect(counts.inProgress).toBe(3)
  })

  it('the banner states the same Ready to ship', () => {
    const go = deriveRelease(release({ gate: gate() }))
    const facts = releaseHealthFacts(go, [go], [go])
    expect(facts.find(f => f.label === 'Ready to ship')?.value).toBe(1)
  })
})

describe('the lede says what the recorded verdict said, and when', () => {
  it('never evaluated', () => {
    expect(releaseLede(deriveRelease(release({ gate: null })), NOW)).toBe(NEVER_EVALUATED_LEDE)
    expect(NEVER_EVALUATED_LEDE).toBe('Not evaluated yet — a QA lead can record a verdict from the linked runs with Evaluate gate.')
  })

  it('GO', () => {
    expect(releaseLede(deriveRelease(release({ gate: gate() })), NOW))
      .toBe('2026.11 was Go 2h ago: pass rate 97.5% over 40 tests in 3 runs.')
    expect(releaseLede(deriveRelease(release({ gate: gate({ evidence_count: 1, run_count: 1, pass_rate: 100 }) })), NOW))
      .toBe('2026.11 was Go 2h ago: pass rate 100.0% over 1 test in 1 run.')
  })

  it('NO_GO counts every blocking item the server stored', () => {
    const nogo = gate({
      verdict: 'NO_GO',
      pass_rate: 81.25,
      blocking_reasons: Array.from({ length: 20 }, (_, i) => `t${i} (s) is FAILED`),
      blocking_total: 25,
    })
    expect(releaseLede(deriveRelease(release({ gate: nogo })), NOW))
      .toBe('2026.11 was No-Go 2h ago: 25 blocking items; pass rate 81.3% over 40 tests.')
  })

  it('NOT_EVALUATED gives the server\'s reason', () => {
    const ne = gate({ verdict: 'NOT_EVALUATED', pass_rate: null, measured: false, insufficient_reason: '3 of 40 tests carry evidence; 5 required' })
    expect(releaseLede(deriveRelease(release({ gate: ne })), NOW))
      .toBe('2026.11 was not evaluated 2h ago: 3 of 40 tests carry evidence; 5 required')
  })

  it('names no threshold of its own', () => {
    for (const g of [gate(), gate({ verdict: 'NO_GO', pass_rate: 60 }), null]) {
      expect(releaseLede(deriveRelease(release({ gate: g })), NOW)).not.toMatch(/95|composite|coverage|flake/i)
    }
  })
})

describe('the Pass rate fact appears only for a measured verdict', () => {
  it('measured GO: Pass rate first', () => {
    const r = deriveRelease(release({ gate: gate() }))
    expect(releaseHealthFacts(r, [r], [r])[0]).toEqual({ label: 'Pass rate', value: '97.5%' })
  })

  it('below the evidence floor, or never evaluated: no Pass rate fact', () => {
    for (const g of [gate({ verdict: 'NOT_EVALUATED', measured: false, pass_rate: 40 }), null]) {
      const r = deriveRelease(release({ gate: g }))
      const facts = releaseHealthFacts(r, [r], [r])
      expect(facts.map(f => f.label)).toEqual(['Open blockers', 'Ready to ship', 'Blocked', 'Released · 30d'])
    }
  })
})

describe('blocker counts are the server\'s totals', () => {
  it('openBlockerCount sums every release\'s blockerCount, not the rows it keeps', () => {
    const a = deriveRelease(release({ id: 'a', gate: gate({ verdict: 'NO_GO', blocking_reasons: Array.from({ length: 20 }, (_, i) => `r${i}`), blocking_total: 25 }) }))
    const b = deriveRelease(release({ id: 'b', gate: gate({ verdict: 'NO_GO', blocking_reasons: ['x'], blocking_total: 1 }) }))
    expect(a.blockers.length).toBeLessThan(25)
    expect(openBlockerCount([a, b])).toBe(26)
  })

  it('a Conditional Go headline counts its items from blockerCount', () => {
    const c = deriveRelease(release({ name: 'Checkout', version: 'Checkout', gate: gate({ verdict: 'CONDITIONAL_GO', blocking_reasons: ['a', 'b'], blocking_total: 7 }) }))
    expect(releaseHeadline(c)).toBe('Checkout is on track — 7 items to clear')
  })
})

describe('verdictAge', () => {
  it('is terse and never negative', () => {
    expect(verdictAge(new Date(NOW - 20_000).toISOString(), NOW)).toBe('just now')
    expect(verdictAge(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe('5m ago')
    expect(verdictAge(TWO_HOURS_AGO, NOW)).toBe('2h ago')
    expect(verdictAge(new Date(NOW - 3 * 86_400_000).toISOString(), NOW)).toBe('3d ago')
    expect(verdictAge(new Date(NOW + 60_000).toISOString(), NOW)).toBe('just now')
    expect(verdictAge('not a date', NOW)).toBe('just now')
    expect(verdictAge(null, NOW)).toBe('just now')
  })
})
