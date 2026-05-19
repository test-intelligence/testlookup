/**
 * Live-session freshness — distinguishes "actively running" from
 * "running-in-DB-but-silent".
 *
 * The backend marks a session ``running`` when ``run_started`` arrives
 * and ``completed`` when ``run_complete`` arrives. If a client crashes
 * mid-run (or NAT drops the stream, or the host loses power), the
 * session sits as ``running`` until the Celery reaper hits its idle
 * threshold (currently **10 minutes**) and marks it complete.
 *
 * That's an eternity on the Live page. A run that hasn't emitted an
 * event in ~60s is almost certainly not actually running anymore; the
 * "N active runs" headline must reflect what is *currently* reporting
 * telemetry, not what the DB has yet to garbage-collect.
 *
 * Stale-but-still-``running`` sessions stay visible in the table — they
 * just get an "Idle" badge and are excluded from the active count.
 */

/**
 * A session is treated as actively running if its most recent event (or
 * start time, for brand-new sessions) is within this many milliseconds.
 */
export const ACTIVE_FRESHNESS_MS = 60_000

/** Minimal shape of a live session needed for the freshness check. */
export interface FreshnessSession {
  status: string
  started_at?: string | null
  last_event_at?: string | null
}

/**
 * Returns true when the session is ``running`` *and* still emitting
 * events recently. Used by the Live page hero ("N active runs") so the
 * headline matches user intuition.
 *
 * ``nowMs`` is injectable for testability — tests freeze time; callers
 * pass ``Date.now()`` or omit for the same result.
 */
export function isActivelyRunning(
  session: FreshnessSession,
  nowMs: number = Date.now(),
): boolean {
  if (session.status !== 'running') return false
  const ts = session.last_event_at || session.started_at
  if (!ts) {
    // No timestamp at all → treat as not-actively-running. The DB row
    // will get reaped or completed eventually; surfacing it as "active"
    // is a false positive.
    return false
  }
  const age = nowMs - new Date(ts).getTime()
  if (Number.isNaN(age) || age < 0) return false
  return age <= ACTIVE_FRESHNESS_MS
}

/**
 * Returns true when the session is ``running`` but has stopped emitting
 * telemetry. These are the rows that produce the "5 active runs but only
 * 2 are actually running" surprise — they're queued for the reaper but
 * shouldn't count as live.
 */
export function isStaleRunning(
  session: FreshnessSession,
  nowMs: number = Date.now(),
): boolean {
  return session.status === 'running' && !isActivelyRunning(session, nowMs)
}
