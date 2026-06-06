/**
 * Regression: /live time-window picker (24h / 7d / 14d / 30d) did not filter
 * the Sessions table.
 *
 * Bug (2026-06-05): `useLiveExecution`'s SWR `onSuccess` merged the API
 * response with `prev` completed sessions using an UNBOUNDED filter
 * (`status === 'completed' && !apiRunIds.has(run_id)`). When the window
 * narrowed (e.g. 30d → 24h) the refetch returned fewer completed sessions, but
 * the merge re-added the now out-of-window rows from `prev`, so the table never
 * shrank. Widening worked, which masked the bug.
 *
 * Fix: bridge ONLY just-completed sessions (within SESSION_RACE_GRACE_MS) so the
 * API response stays authoritative for everything older.
 */
import { describe, expect, it } from 'vitest'
import {
  SESSION_RACE_GRACE_MS,
  mergeBridgedSessions,
} from './useLiveExecution'
import type { LiveSessionState } from '@/types/live-stream'

const NOW = 1_780_000_000_000 // fixed "now" in ms

function session(p: Partial<LiveSessionState>): LiveSessionState {
  return {
    run_id: 'r',
    project_id: 'p',
    build_number: 'b',
    status: 'completed',
    total: 1, passed: 1, failed: 0, skipped: 0, broken: 0,
    pass_rate: 100,
    ...p,
  } as LiveSessionState
}

describe('mergeBridgedSessions — /live window filtering (regression)', () => {
  it('drops stale completed sessions that fell outside the narrowed window', () => {
    // prev held a 30d session completed 10 days ago; the 24h refetch omits it.
    const prev = [session({
      run_id: 'old',
      completed_at: new Date(NOW - 10 * 24 * 3600_000).toISOString(),
    })]
    const api = [session({ run_id: 'fresh', completed_at: new Date(NOW - 60_000).toISOString() })]

    const merged = mergeBridgedSessions(prev, api, NOW)

    expect(merged.map(s => s.run_id)).toEqual(['fresh'])
    expect(merged.some(s => s.run_id === 'old')).toBe(false)
  })

  it('bridges a just-completed session the API has not returned yet', () => {
    // WS flipped 'justdone' to completed seconds ago; next poll lags.
    const prev = [session({
      run_id: 'justdone',
      completed_at: new Date(NOW - 5_000).toISOString(),
    })]
    const api = [session({ run_id: 'fresh', completed_at: new Date(NOW - 1_000).toISOString() })]

    const merged = mergeBridgedSessions(prev, api, NOW)

    expect(merged.map(s => s.run_id).sort()).toEqual(['fresh', 'justdone'])
  })

  it('never bridges a session the API already returned (no duplicates)', () => {
    const prev = [session({ run_id: 'dup', completed_at: new Date(NOW - 2_000).toISOString() })]
    const api = [session({ run_id: 'dup', completed_at: new Date(NOW - 1_000).toISOString() })]

    const merged = mergeBridgedSessions(prev, api, NOW)

    expect(merged.filter(s => s.run_id === 'dup')).toHaveLength(1)
  })

  it('drops a completed session with no completed_at (cannot be time-boxed)', () => {
    const prev = [session({ run_id: 'nodate', completed_at: undefined })]
    const merged = mergeBridgedSessions(prev, [], NOW)
    expect(merged).toHaveLength(0)
  })

  it('honours the grace boundary exactly', () => {
    const justInside = session({
      run_id: 'inside',
      completed_at: new Date(NOW - (SESSION_RACE_GRACE_MS - 1)).toISOString(),
    })
    const justOutside = session({
      run_id: 'outside',
      completed_at: new Date(NOW - (SESSION_RACE_GRACE_MS + 1)).toISOString(),
    })
    const merged = mergeBridgedSessions([justInside, justOutside], [], NOW)
    expect(merged.map(s => s.run_id)).toEqual(['inside'])
  })
})
