/**
 * Tests for the live-session freshness predicates.
 *
 * Pin the contract that drives the Live page's "N active runs" headline:
 * a session is "actively running" iff its most recent event is within
 * ``ACTIVE_FRESHNESS_MS``. Stale-but-still-running rows are flagged so
 * they can be excluded from the count and badged in the table.
 *
 * Without this, the page faithfully reports whatever the DB says — and
 * the DB only learns a run died when the 10-minute reaper kicks in.
 * That's how /live ended up reporting "5 active runs" when only 2 were
 * really emitting telemetry.
 */
import { describe, expect, it } from 'vitest'
import {
  ACTIVE_FRESHNESS_MS,
  isActivelyRunning,
  isStaleRunning,
} from './liveSessionFreshness'

const NOW = Date.parse('2026-05-16T04:20:00.000Z')

function iso(offsetSec: number): string {
  return new Date(NOW + offsetSec * 1000).toISOString()
}

describe('isActivelyRunning', () => {
  it('returns false for non-running statuses regardless of recency', () => {
    expect(isActivelyRunning(
      { status: 'completed', last_event_at: iso(-5) },
      NOW,
    )).toBe(false)
    expect(isActivelyRunning(
      { status: 'failed', last_event_at: iso(-5) },
      NOW,
    )).toBe(false)
  })

  it('treats a running session with a recent event as active', () => {
    expect(isActivelyRunning(
      { status: 'running', last_event_at: iso(-10) },
      NOW,
    )).toBe(true)
  })

  it('treats a brand-new running session with no events yet as active', () => {
    // started_at fallback covers the very-first-second case.
    expect(isActivelyRunning(
      { status: 'running', started_at: iso(-2) },
      NOW,
    )).toBe(true)
  })

  it('treats a running session past the freshness threshold as inactive', () => {
    const justOver = -(ACTIVE_FRESHNESS_MS / 1000 + 1)  // 61s
    expect(isActivelyRunning(
      { status: 'running', last_event_at: iso(justOver) },
      NOW,
    )).toBe(false)
  })

  it('treats a running session with no timestamps at all as inactive', () => {
    // Defensive: nothing to compare against → don't claim it's live.
    expect(isActivelyRunning(
      { status: 'running' },
      NOW,
    )).toBe(false)
  })

  it('treats a future-timestamped event (clock drift) as inactive', () => {
    // If a session reports a timestamp in the future we treat it as
    // not-live rather than crash or falsely count it.
    expect(isActivelyRunning(
      { status: 'running', last_event_at: iso(120) },  // 2 min in the future
      NOW,
    )).toBe(false)
  })
})

describe('isStaleRunning', () => {
  it('is true for a running session past the freshness threshold', () => {
    const stale = -(ACTIVE_FRESHNESS_MS / 1000 + 30)  // 90s ago
    expect(isStaleRunning(
      { status: 'running', last_event_at: iso(stale) },
      NOW,
    )).toBe(true)
  })

  it('is false for a running session still emitting events', () => {
    expect(isStaleRunning(
      { status: 'running', last_event_at: iso(-5) },
      NOW,
    )).toBe(false)
  })

  it('is false for completed sessions even when old', () => {
    expect(isStaleRunning(
      { status: 'completed', last_event_at: iso(-3600) },
      NOW,
    )).toBe(false)
  })
})

describe('exact 5-active-runs-but-only-2-running repro', () => {
  /**
   * Models the homelab observation: API returns 5 sessions with
   * status='running' but only 2 are still emitting telemetry. The
   * predicate must split them into active=2 / stale=3.
   */
  it('classifies 5 running sessions into 2 active + 3 stale', () => {
    const sessions = [
      { status: 'running', last_event_at: iso(-3),    started_at: iso(-120) },  // active
      { status: 'running', last_event_at: iso(-10),   started_at: iso(-120) },  // active
      { status: 'running', last_event_at: iso(-70),   started_at: iso(-120) },  // stale
      { status: 'running', last_event_at: iso(-120),  started_at: iso(-120) },  // stale
      { status: 'running', last_event_at: iso(-300),  started_at: iso(-300) },  // stale
    ]
    const active = sessions.filter(s => isActivelyRunning(s, NOW))
    const stale  = sessions.filter(s => isStaleRunning(s, NOW))
    expect(active.length).toBe(2)
    expect(stale.length).toBe(3)
    // No overlap: each running session is exactly one of {active, stale}.
    expect(active.length + stale.length).toBe(sessions.length)
  })
})
