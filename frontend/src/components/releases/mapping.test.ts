/**
 * REL-01 / REL-02 / REL-08: a release card shows what the SERVER recorded.
 *
 * `deriveRelease` used to decide the gate in the browser (`decideGate`, with
 * 95/90 · 80/70 · 1.5/3 thresholds over a composite / coverage / flake triple
 * nothing supplied), paint a six-bucket phase template (Smoke … Sign & ship)
 * over the release's real phases, and show the PROJECT name as the owner.
 * These tests pin the replacement: the recorded verdict verbatim, the
 * release's own phases, the release's own owner.
 */
import { describe, expect, it } from 'vitest'

import type { Release, ReleaseGateSummary, ReleasePhase } from '@/types/releases'
import { BLOCKER_REASON_CAP, deriveRelease, initialsOf, mapVerdict, safeHttpUrl } from './mapping'

function phase(overrides: Partial<ReleasePhase> = {}): ReleasePhase {
  return {
    id: 'ph-1',
    release_id: 'rel-1',
    name: 'Phase',
    phase_type: 'custom',
    status: 'pending',
    description: null,
    order_index: 0,
    planned_start: null,
    planned_end: null,
    actual_start: null,
    actual_end: null,
    exit_criteria: null,
    notes: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function gate(overrides: Partial<ReleaseGateSummary> = {}): ReleaseGateSummary {
  return {
    verdict: 'GO',
    decided_at: '2026-10-14T08:00:00Z',
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
    project_name: 'Checkout Platform',
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

describe('the gate decision is the recorded verdict, verbatim', () => {
  it.each([
    ['GO', 'go'],
    ['NO_GO', 'no_go'],
    ['NOT_EVALUATED', 'not_evaluated'],
    ['CONDITIONAL_GO', 'conditional'],
  ] as const)('%s -> %s', (verdict, decision) => {
    expect(mapVerdict(verdict)).toBe(decision)
    expect(deriveRelease(release({ gate: gate({ verdict }) })).gate.decision).toBe(decision)
  })

  it('a word outside the vocabulary reads "not evaluated", never a pass', () => {
    for (const drifted of ['PASS', 'go', 'Go', 'APPROVED', '']) {
      expect(mapVerdict(drifted), drifted).toBe('not_evaluated')
      expect(deriveRelease(release({ gate: gate({ verdict: drifted }) })).gate.decision, drifted).toBe('not_evaluated')
    }
  })

  it('no recorded verdict (gate null or absent) is "never evaluated", with every number unknown', () => {
    for (const r of [release({ gate: null }), release()]) {
      expect(deriveRelease(r).gate).toEqual({
        decision: 'never_evaluated',
        passRate: null,
        denominator: null,
        evidence: null,
        runCount: null,
        decidedAt: null,
        measured: false,
        insufficientReason: null,
        blockingTotal: 0,
      })
      expect(deriveRelease(r).totals).toBeNull()
    }
  })

  it('a cancelled release shows cancelled whatever its verdict was', () => {
    expect(deriveRelease(release({ status: 'cancelled', gate: gate({ verdict: 'GO' }) })).gate.decision).toBe('cancelled')
    expect(deriveRelease(release({ status: 'cancelled', gate: null })).gate.decision).toBe('cancelled')
  })

  it('carries the recorded scorecard as is', () => {
    const g = deriveRelease(release({
      gate: gate({ verdict: 'NOT_EVALUATED', pass_rate: null, measured: false, insufficient_reason: '3 of 40 tests carry evidence; 5 required', evidence_count: 3, denominator: 40, run_count: 1 }),
    })).gate
    expect(g).toEqual({
      decision: 'not_evaluated',
      passRate: null,
      denominator: 40,
      evidence: 3,
      runCount: 1,
      decidedAt: '2026-10-14T08:00:00Z',
      measured: false,
      insufficientReason: '3 of 40 tests carry evidence; 5 required',
      blockingTotal: 0,
    })
  })

  it('judges nothing: a GO at 50% stays Go and a NO_GO at 100% stays No-Go (no browser threshold)', () => {
    expect(deriveRelease(release({ gate: gate({ verdict: 'GO', pass_rate: 50 }) })).gate.decision).toBe('go')
    expect(deriveRelease(release({ gate: gate({ verdict: 'NO_GO', pass_rate: 100 }) })).gate.decision).toBe('no_go')
  })
})

describe('totals come from the verdict status counts', () => {
  it('FAILED -> failed, BROKEN -> broken, SKIPPED + UNKNOWN -> skipped', () => {
    const r = deriveRelease(release({
      gate: gate({ status_counts: { PASSED: 30, FAILED: 3, BROKEN: 2, SKIPPED: 1, UNKNOWN: 2 } }),
    }))
    expect(r.totals).toEqual({ passed: 30, failed: 3, broken: 2, skipped: 3 })
  })

  it('a legacy row with no counts has no totals (not four zeros)', () => {
    expect(deriveRelease(release({ gate: gate({ status_counts: {} }) })).totals).toBeNull()
  })
})

describe('the phases are the release\'s own', () => {
  it('two phases "Build verification" and "Soak" give exactly two chips, none of the old template', () => {
    const r = deriveRelease(release({
      phases: [
        phase({ id: 'ph-soak', name: 'Soak', phase_type: 'custom', status: 'pending', order_index: 1 }),
        phase({ id: 'ph-bv', name: 'Build verification', phase_type: 'smoke', status: 'in_progress', order_index: 0 }),
      ],
    }))
    expect(r.phases).toEqual([
      { key: 'ph-bv', label: 'Build verification', phaseType: 'smoke', state: 'active' },
      { key: 'ph-soak', label: 'Soak', phaseType: 'custom', state: 'idle' },
    ])
    const labels = r.phases.map(p => p.label).join(' ')
    expect(labels).not.toMatch(/Smoke|Regression|E2E|Perf|UAT|Sign/)
  })

  it('a release with no phases has no chips (no placeholders)', () => {
    expect(deriveRelease(release({ phases: [] })).phases).toEqual([])
  })

  it('maps each phase status to its chip state', () => {
    const states = deriveRelease(release({
      phases: ['pending', 'in_progress', 'completed', 'failed', 'skipped'].map((status, i) =>
        phase({ id: `p${i}`, name: `P${i}`, status, order_index: i })),
    })).phases.map(p => p.state)
    expect(states).toEqual(['idle', 'active', 'done', 'failed', 'skipped'])
  })
})

describe('the owner is the release\'s, never the project\'s', () => {
  it('shows the owner the server resolved', () => {
    const r = deriveRelease(release({ project_name: 'Checkout Platform', owner: { id: 'u-1', name: 'Priya Nair' } }))
    expect(r.ownerName).toBe('Priya Nair')
    expect(r.ownerInitials).toBe('PN')
    expect(r.hasOwner).toBe(true)
  })

  it('no owner reads "No owner" — the project name is not an owner', () => {
    for (const r of [release({ owner: null }), release()]) {
      const d = deriveRelease(r)
      expect(d.ownerName).toBe('No owner')
      expect(d.hasOwner).toBe(false)
      expect(d.ownerInitials).toBe('–')
      expect(d.ownerName).not.toContain('Checkout')
    }
  })

  it('initials: first and last word, or the first two letters of one word', () => {
    expect(initialsOf('Priya Nair')).toBe('PN')
    expect(initialsOf('Ana Maria de Souza')).toBe('AS')
    expect(initialsOf('priya')).toBe('PR')
  })
})

describe('blockers are the server\'s reasons plus failed phases', () => {
  const reasons = Array.from({ length: 20 }, (_, i) => `test_${i} (checkout-e2e) is FAILED`)

  it('keeps the first reasons as red rows opening the release gate panel; the count is the server total', () => {
    const r = deriveRelease(release({ gate: gate({ verdict: 'NO_GO', blocking_reasons: reasons, blocking_total: 25 }) }))
    expect(r.blockers).toHaveLength(BLOCKER_REASON_CAP)
    expect(r.blockers[0]).toMatchObject({
      severity: 'red',
      title: 'test_0 (checkout-e2e) is FAILED',
      action: { label: 'Open gate →', href: '/releases/rel-1#gate' },
    })
    expect(r.gate.blockingTotal).toBe(25)
    expect(r.blockerCount).toBe(25)
    // The run-level gate page is the wrong page for a release id.
    expect(r.blockers.some(b => b.action.href.startsWith('/release-gate/'))).toBe(false)
  })

  it('adds each failed phase, linking to that phase, to the rows and to the count', () => {
    const r = deriveRelease(release({
      gate: gate({ verdict: 'NO_GO', blocking_reasons: ['a (s) is FAILED'], blocking_total: 1 }),
      phases: [phase({ id: 'ph-reg', name: 'Regression run', status: 'failed', phase_type: 'regression' })],
    }))
    expect(r.blockers.map(b => b.title)).toEqual(['a (s) is FAILED', 'Regression run failed'])
    expect(r.blockers[1].action.href).toBe('/releases/rel-1#phase-ph-reg')
    expect(r.blockerCount).toBe(2)
  })

  it('a GO release and a never-evaluated one have no blockers', () => {
    expect(deriveRelease(release({ gate: gate() })).blockers).toEqual([])
    expect(deriveRelease(release({ gate: null })).blockerCount).toBe(0)
  })
})

describe('active release and external link (REL-08)', () => {
  it('is_active is carried; absent means not active', () => {
    expect(deriveRelease(release({ is_active: true })).isActive).toBe(true)
    expect(deriveRelease(release()).isActive).toBe(false)
  })

  it('keeps only an http(s) external URL', () => {
    const ok = deriveRelease(release({ external_url: 'https://github.com/acme/shop/milestone/3', source_system: 'github' }))
    expect(ok.externalUrl).toBe('https://github.com/acme/shop/milestone/3')
    expect(ok.sourceSystem).toBe('github')
    expect(deriveRelease(release({ external_url: 'http://jira.lab/projects/SHOP/versions/1' })).externalUrl)
      .toBe('http://jira.lab/projects/SHOP/versions/1')
    for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'data:text/html,<b>x</b>', '//evil.example', ' https://x.example', 'https://exa mple.com', 'ftp://x.example']) {
      expect(safeHttpUrl(bad), bad).toBeNull()
      expect(deriveRelease(release({ external_url: bad })).externalUrl, bad).toBeNull()
    }
    expect(deriveRelease(release({ external_url: null })).externalUrl).toBeNull()
  })
})
