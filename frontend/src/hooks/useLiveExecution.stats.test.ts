/**
 * Regression: live pass-rate dropped BROKEN from its denominator.
 *
 * Bug: both the `useLiveExecution` hook stats and the /live page's visible KPIs
 * computed `overallPassRate = passed / (passed + failed)`, ignoring BROKEN
 * (infrastructure-error) tests entirely — `totalBroken` was never even summed.
 * The rest of the codebase pins "a failure is FAILED *or* BROKEN": the backend's
 * own per-session pass_rate (`stream_service.close_session`) divides by
 * `passed + failed + broken`. So a live run of 8 passed / 2 broken showed 100%
 * on the dashboard while its own final pass_rate — and the /runs page — said 80%.
 *
 * Fix: `computeLiveStats` uses `passed / (passed + failed + broken)` (skipped
 * excluded) and returns `totalBroken` so broken is no longer erased.
 */
import { describe, expect, it } from 'vitest'
import { computeLiveStats } from './useLiveExecution'
import type { LiveSessionState } from '@/types/live-stream'

function session(p: Partial<LiveSessionState>): LiveSessionState {
  return {
    run_id: 'r',
    project_id: 'p',
    build_number: 'b',
    status: 'running',
    total: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
    pass_rate: 0,
    ...p,
  } as LiveSessionState
}

describe('computeLiveStats — pass rate counts BROKEN as a non-pass (regression)', () => {
  it('divides by passed + failed + broken, not passed + failed', () => {
    // 8 passed, 0 failed, 2 broken → 8 / (8+0+2) = 80%, NOT 8/8 = 100%.
    const stats = computeLiveStats([session({ total: 10, passed: 8, broken: 2 })])
    expect(stats.overallPassRate).toBe(80)
    expect(stats.totalBroken).toBe(2)
  })

  it('excludes skipped from the denominator', () => {
    // 9 passed, 1 broken, 5 skipped → 9 / (9+0+1) = 90%; skips do not dilute.
    const stats = computeLiveStats([session({ total: 15, passed: 9, broken: 1, skipped: 5 })])
    expect(stats.overallPassRate).toBe(90)
    expect(stats.totalSkipped).toBe(5)
  })

  it('sums each component across multiple sessions', () => {
    const stats = computeLiveStats([
      session({ total: 5, passed: 4, failed: 1 }),
      session({ total: 5, passed: 3, broken: 2 }),
    ])
    expect(stats.totalTests).toBe(10)
    expect(stats.totalPassed).toBe(7)
    expect(stats.totalFailed).toBe(1)
    expect(stats.totalBroken).toBe(2)
    // 7 / (7 + 1 + 2) = 70%
    expect(stats.overallPassRate).toBe(70)
  })

  it('reports 0% when nothing has been evaluated (all skipped / empty)', () => {
    expect(computeLiveStats([]).overallPassRate).toBe(0)
    expect(computeLiveStats([session({ total: 3, skipped: 3 })]).overallPassRate).toBe(0)
  })

  it('reports 100% only when every evaluated test passed', () => {
    const stats = computeLiveStats([session({ total: 4, passed: 4 })])
    expect(stats.overallPassRate).toBe(100)
  })
})
