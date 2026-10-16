/**
 * FAIL-03: the window's failed builds grouped by the error most of their
 * failing tests share (`GET /runs/failure-signatures` gives each run's top
 * signatures, the dominant one first), never by the `(passed, failed, total)`
 * tuple the page used to hash.
 */
import { describe, expect, it } from 'vitest'

import type { RunSignatureBatch, RunSignatureEntry, RunSignatureItem, TestRun } from '@/types/runs'
import {
  buildSignatureModel, chipLabelOf, MAX_SIGNATURE_RUNS, NO_FAILING_TESTS, NO_MESSAGE, signatureCellOf, signatureRunIds,
} from './runSignatures'

function run(i: number, over: Partial<TestRun> = {}): TestRun {
  return {
    id: `run-${String(i).padStart(3, '0')}`, build_number: 100 + i, status: 'FAILED',
    passed_tests: 8, failed_tests: 2, skipped_tests: 0, total_tests: 10, pass_rate: 80,
    created_at: new Date(Date.UTC(2026, 8, 1) + i * 3_600_000).toISOString(),
    ...over,
  }
}

const entry = (signature: string, label: string, tests: string[] = [], count = 2): RunSignatureEntry =>
  ({ signature, label, count, test_count: tests.length, tests })

function item(runId: string, signatures: RunSignatureEntry[], over: Partial<RunSignatureItem> = {}): RunSignatureItem {
  const failing = signatures.reduce((s, e) => s + e.count, 0)
  return {
    run_id: runId, failing_tests: failing, read: failing, capped: false,
    dominant: signatures[0]?.signature ?? null, signatures, ...over,
  }
}

const batchOf = (items: RunSignatureItem[]): RunSignatureBatch =>
  ({ items, unavailable: [], max_ids: 50, rows_per_run_cap: 500 })

const ASSERT = entry('assertionerror: expected # but was #', 'AssertionError: expected 3 but was 4', ['test_cart', 'test_pay'])
const NPE = entry('nullpointerexception at foo', 'NullPointerException at Foo', ['test_pay'])
const TIMEOUT = entry('timeout after #s', 'Timeout after 30s', ['test_slow'], 1)

describe('signatureRunIds — the visible failed builds first, then the newest, at most 50, sorted', () => {
  it('puts the visible page\'s failed runs in before older ones, de-duplicated, cut at 50, then sorted', () => {
    const window = Array.from({ length: 80 }, (_, i) => run(i))  // run-079 is the newest
    const visible = [run(2), run(3, { status: 'PASSED' }), run(5)]
    const ids = signatureRunIds(visible, window)
    expect(ids).toHaveLength(MAX_SIGNATURE_RUNS)
    expect(ids).toEqual([...ids].sort())
    // The two visible failed builds are in although they are the oldest.
    expect(ids).toContain('run-002')
    expect(ids).toContain('run-005')
    expect(ids).not.toContain('run-003')
    // Then the newest 48 of the rest: 079 … 032; 031 is the first one cut.
    expect(ids).toContain('run-079')
    expect(ids).toContain('run-032')
    expect(ids).not.toContain('run-031')
  })

  it('asks only for failed builds', () => {
    expect(signatureRunIds([], [run(1, { status: 'PASSED' }), run(2, { status: 'IN_PROGRESS' }), run(3, { status: 'BROKEN' })]))
      .toEqual(['run-003'])
  })
})

describe('buildSignatureModel — groups by the dominant error', () => {
  it('a batch with no items array reads as not loaded, never a crash', () => {
    const a = run(1)
    // An older API or a proxy's empty JSON: the table keeps rendering ("—" for diversity).
    const model = buildSignatureModel([a], {} as unknown as Parameters<typeof buildSignatureModel>[1])
    expect(model.groups).toEqual([])
    expect(model.diversityScore).toBeNull()
  })

  it('two builds with identical pass / fail / total counts but different errors are two groups', () => {
    const a = run(1)
    const b = run(2)  // the same 8 / 2 / 10 as a
    const model = buildSignatureModel([b, a], batchOf([item(a.id, [ASSERT, TIMEOUT]), item(b.id, [NPE])]))
    expect(model.groups).toHaveLength(2)
    expect(model.groupOf.get(a.id)).not.toBe(model.groupOf.get(b.id))
    expect(model.groups.map((g) => g.title).sort()).toEqual(['AssertionError: expected 3 but was 4', 'NullPointerException at Foo'])
  })

  it('builds sharing their dominant error are one group even when a minor one differs; the biggest group is primary', () => {
    const runs = [run(1), run(2), run(3)]
    const model = buildSignatureModel(runs, batchOf([
      item(runs[0].id, [ASSERT, TIMEOUT]),
      item(runs[1].id, [ASSERT]),
      item(runs[2].id, [NPE]),
    ]))
    expect(model.groups.map((g) => g.members.length)).toEqual([2, 1])
    expect(model.groups[0].key).toBe(ASSERT.signature)
    expect(model.groups[0].title).toBe('AssertionError: expected 3 but was 4')
    expect(model.failedRuns).toBe(3)
    expect(model.groupedRuns).toBe(3)
  })

  it('sharedTests: only the tests EVERY member names; none for a one-member group', () => {
    const runs = [run(1), run(2), run(3)]
    const shared = buildSignatureModel(runs.slice(0, 2), batchOf([
      item(runs[0].id, [entry(ASSERT.signature, ASSERT.label, ['test_cart', 'test_pay'])]),
      item(runs[1].id, [entry(ASSERT.signature, ASSERT.label, ['test_pay', 'test_zip'])]),
    ]))
    expect(shared.groups[0].sharedTests).toEqual(['test_pay'])

    const disjoint = buildSignatureModel(runs.slice(0, 2), batchOf([
      item(runs[0].id, [entry(ASSERT.signature, ASSERT.label, ['test_cart'])]),
      item(runs[1].id, [entry(ASSERT.signature, ASSERT.label, ['test_zip'])]),
    ]))
    expect(disjoint.groups[0].sharedTests).toEqual([])

    const single = buildSignatureModel([runs[2]], batchOf([item(runs[2].id, [NPE])]))
    expect(single.groups[0].sharedTests).toEqual([])
  })

  it('a blank message and a build with no failing rows have their own titles', () => {
    const runs = [run(1), run(2)]
    const model = buildSignatureModel(runs, batchOf([
      item(runs[0].id, [entry(NO_MESSAGE, '', ['test_a'])]),
      item(runs[1].id, [], { failing_tests: 0, read: 0, dominant: null }),
    ]))
    const byKey = new Map(model.groups.map((g) => [g.key, g]))
    expect(byKey.get(NO_MESSAGE)?.title).toBe('No message recorded')
    expect(byKey.get(NO_FAILING_TESTS)?.title).toBe('No failing tests recorded')
    expect(byKey.get(NO_MESSAGE)?.chipLabel).toBe('no message')
    expect(byKey.get(NO_FAILING_TESTS)?.chipLabel).toBe('no tests')
  })

  it('diversity: groups / grouped builds (3 over 6 → 50); not measured while loading; 100 with nothing failed', () => {
    const runs = Array.from({ length: 6 }, (_, i) => run(i))
    const sigs = [ASSERT, ASSERT, ASSERT, NPE, NPE, TIMEOUT]
    const model = buildSignatureModel(runs, batchOf(runs.map((r, i) => item(r.id, [sigs[i]]))))
    expect(model.groups).toHaveLength(3)
    expect(model.diversityScore).toBe(50)

    expect(buildSignatureModel(runs, undefined).diversityScore).toBeNull()
    expect(buildSignatureModel([run(1, { status: 'PASSED' })], undefined).diversityScore).toBe(100)
    // Builds failed but none could be grouped (all unavailable): not measured, never 0.
    expect(buildSignatureModel(runs, batchOf([])).diversityScore).toBeNull()
  })

  it('a failed build the batch did not answer is counted as failed, not grouped', () => {
    const runs = [run(1), run(2), run(3)]
    const model = buildSignatureModel(runs, batchOf([item(runs[0].id, [ASSERT])]))
    expect(model.failedRuns).toBe(3)
    expect(model.groupedRuns).toBe(1)
    expect(model.groupOf.has(runs[1].id)).toBe(false)
  })
})

describe('chipLabelOf — the table chip keeps its width', () => {
  it('is at most 11 characters and stable per key', () => {
    for (const key of [ASSERT.signature, NPE.signature, 'x', NO_MESSAGE, NO_FAILING_TESTS]) {
      expect(chipLabelOf(key).length).toBeLessThanOrEqual(11)
      expect(chipLabelOf(key)).toBe(chipLabelOf(key))
    }
    expect(chipLabelOf(ASSERT.signature)).toMatch(/^[0-9a-f]{3}-cluster$/)
    expect(chipLabelOf(ASSERT.signature)).not.toBe(chipLabelOf(NPE.signature))
  })
})

describe("signatureCellOf — what a table row's Signature cell shows", () => {
  const runs = [run(1), run(2), run(3), run(4, { status: 'PASSED' })]
  const model = buildSignatureModel(runs, batchOf([item(runs[0].id, [ASSERT]), item(runs[1].id, [NPE]), item(runs[2].id, [ASSERT])]))
  const asked = { requested: new Set([runs[0].id, runs[1].id, runs[2].id]), loading: false, failed: false }

  it("a grouped failed build: its group's chip, tinted when the group is not the primary", () => {
    const primary = signatureCellOf(runs[0], model, asked)
    expect(primary).toMatchObject({ kind: 'chip', isOutlier: false })
    expect(signatureCellOf(runs[1], model, asked)).toMatchObject({ kind: 'chip', isOutlier: true })
  })

  it('a passed build: a plain "—" with no title', () => {
    expect(signatureCellOf(runs[3], model, asked)).toEqual({ kind: 'none' })
  })

  it('a failed build beyond the newest 50 (never asked): "not loaded"', () => {
    const old = run(9)
    expect(signatureCellOf(old, model, asked)).toEqual({ kind: 'none', title: 'Signature not loaded: older than the newest 50 failed builds' })
  })

  it('asked but not answered: loading, the request failed, or not available to the reader', () => {
    const pending = run(8)
    const requested = new Set([...asked.requested, pending.id])
    expect(signatureCellOf(pending, model, { requested, loading: true, failed: false })).toEqual({ kind: 'none', title: 'Loading the signature…' })
    expect(signatureCellOf(pending, model, { requested, loading: false, failed: true })).toEqual({ kind: 'none', title: 'Signature unavailable: the request failed' })
    expect(signatureCellOf(pending, model, { requested, loading: false, failed: false })).toEqual({ kind: 'none', title: 'Signature unavailable: the run is not available to you' })
  })
})
