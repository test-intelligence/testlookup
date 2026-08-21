import { describe, expect, it } from 'vitest'

// The REAL helper the card uses. A mirrored copy here would pass while
// the page computed something else entirely.
import { computeRunOutcome as outcome } from '@/utils/runOutcome'

/**
 * Regression guard: the run-intelligence "Test outcome" card must count
 * BROKEN and must not put skips in the pass-rate denominator.
 *
 * The defect
 * ----------
 * The card computed:
 *
 *     const passRate = (passed / total) * 100     // skips in the denominator
 *     const failed   = run.failed_tests ?? 0      // BROKEN dropped
 *
 * On a ground-truth run of 10 tests — 4 passed, 4 FAILED, 1 BROKEN,
 * 1 skipped — that rendered:
 *
 *     Pass rate 40.0%   "4 / 10 passed"
 *     4 passed · 4 failed · 1 skipped        -> 9 of 10
 *
 * while the SAME payload carried `pass_rate: 44.44`, and `/runs` showed
 * 44.4% with the broken test visible. One run, two surfaces, two answers.
 *
 * The root cause was in the API — the intelligence `run` block omitted
 * `broken_tests` — but the page's arithmetic was independently wrong: it
 * divided by `total` rather than by the evaluated set. Fixing only the
 * payload would have left the pass rate at 4/10.
 *
 * The card's arithmetic is exported as `computeRunOutcome` and imported
 * here, so mutating the page fails these tests. Re-declaring it locally
 * would guard nothing.
 */

// The exact run the defect was measured on.
const GT6 = {
  total_tests: 10,
  passed_tests: 4,
  failed_tests: 4,
  broken_tests: 1,
  skipped_tests: 1,
  pass_rate: 44.44,
}

describe('run intelligence — test outcome card', () => {
  it('counts BROKEN as a failure', () => {
    const o = outcome(GT6)
    expect(o.failed).toBe(5)
  })

  it('accounts for every test in the run', () => {
    // The distribution strip renders passed / failed / skipped. Those three
    // plus nothing else must equal the total, or a test is invisible.
    const o = outcome(GT6)
    expect(o.passed + o.failed + o.skipped).toBe(o.total)
  })

  it('excludes skips from the pass-rate denominator', () => {
    const o = outcome(GT6)
    expect(o.evaluated).toBe(9)
    expect(o.passRate).toBeCloseTo(44.44, 1)
    // The old behaviour, pinned so it cannot come back.
    expect(o.passRate).not.toBeCloseTo(40.0, 1)
  })

  it('agrees with the pass_rate the payload already carries', () => {
    const o = outcome(GT6)
    expect(o.passRate).toBe(GT6.pass_rate)
  })

  it('still computes a pass rate when the payload omits one', () => {
    const o = outcome({ ...GT6, pass_rate: null })
    expect(o.passRate).toBeCloseTo(44.44, 1)
  })

  it('survives a payload with no broken_tests field at all', () => {
    // Defensive: the field was missing for the entire life of this bug, and
    // an older backend may still omit it. The page must not produce NaN.
    const { broken_tests: _omitted, ...withoutBroken } = GT6
    const o = outcome(withoutBroken)
    expect(o.failed).toBe(4)
    expect(Number.isNaN(o.passRate)).toBe(false)
  })

  it('reports 0% rather than NaN when nothing was evaluated', () => {
    const o = outcome({ total_tests: 3, passed_tests: 0, failed_tests: 0, skipped_tests: 3, pass_rate: null })
    expect(o.evaluated).toBe(0)
    expect(o.passRate).toBe(0)
  })
})
