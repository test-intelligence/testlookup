/**
 * W25a HYG-06 (a) — the sentence beside a `ws_session` access row on /settings/audit. The row is append-only (the
 * orchestrator's ruling): written ONCE, at the first disconnect where the session's connected time reaches 5 s, with
 * `routers/live.py` `_ws_session_detail`'s keys. The sentence states only what the row knows — never a reconnect
 * count or a total duration — and leaves out a missing key (never "undefined", "—" or "0 s"). The unit suite runs in
 * America/Chicago: 09:00 UTC on 31 Oct 2026 is 04:00 local (CDT).
 */
import { describe, expect, it } from 'vitest'

import { auditRowNote, formatSessionDuration, WS_RECONNECT_POLICY, wsSessionLine } from './auditDashboardModel'

/** A `ws_session` detail in `_ws_session_detail`'s exact keys, built inside the test. */
function liveDetail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const session = new Date('2026-10-31T09:00:00Z').getTime()
  return {
    channel: 'live',
    reason: 'client_disconnect',
    started_at: new Date(session + 10_000).toISOString(),
    ended_at: new Date(session + 52_000).toISOString(),
    session_started_at: new Date(session).toISOString(),
    connected_s_at_write: 47,
    reconnect_policy: 'reconnects within 60 s are merged into this session',
    ...overrides,
  }
}

const SAMPLE =
  'Live session from Oct 31, 04:00; this connection 04:00:10–04:00:52 (client_disconnect); 47 s connected when ' +
  'recorded; reconnects within 60 s are merged into this session.'

describe('formatSessionDuration', () => {
  it('seconds under a minute, minutes under an hour, hours and minutes beyond', () => {
    expect(formatSessionDuration(42)).toBe('42 s')
    expect(formatSessionDuration(12 * 60 + 30)).toBe('12 min')
    expect(formatSessionDuration(2 * 3600 + 5 * 60 + 9)).toBe('2 h 5 min')
    expect(formatSessionDuration(3 * 3600)).toBe('3 h')
  })

  it('nothing for a value that is not a duration', () => {
    expect(formatSessionDuration(Number.NaN)).toBeNull()
    expect(formatSessionDuration(Number.POSITIVE_INFINITY)).toBeNull()
    expect(formatSessionDuration(-3)).toBeNull()
  })
})

describe('wsSessionLine', () => {
  it('a full row: the session start, this connection (span + close reason), the connected time, the row’s policy', () => {
    expect(wsSessionLine(liveDetail())).toBe(SAMPLE)
    expect(wsSessionLine(liveDetail({ connected_s_at_write: 725, reason: 'token_expired' })))
      .toBe('Live session from Oct 31, 04:00; this connection 04:00:10–04:00:52 (token_expired); 12 min connected when recorded; reconnects within 60 s are merged into this session.')
  })

  it('the policy is read from the row; the literal stands in when the key is missing', () => {
    expect(wsSessionLine(liveDetail({ reconnect_policy: 'reconnects within 90 s are merged into this session' })))
      .toMatch(/; reconnects within 90 s are merged into this session\.$/)
    expect(wsSessionLine(liveDetail({ reconnect_policy: undefined }))).toBe(SAMPLE)
    expect(WS_RECONNECT_POLICY).toBe('reconnects within 60 s are merged into this session')
  })

  it('a missing or unreadable key is left out — never "undefined", "—" or "0 s"', () => {
    const bare = wsSessionLine({ channel: 'live' })
    expect(bare).toBe('Live session; reconnects within 60 s are merged into this session.')
    expect(wsSessionLine(liveDetail({ session_started_at: undefined })))
      .toBe('Live session; this connection 04:00:10–04:00:52 (client_disconnect); 47 s connected when recorded; reconnects within 60 s are merged into this session.')
    expect(wsSessionLine(liveDetail({ ended_at: 'not a time' })))
      .toBe('Live session from Oct 31, 04:00; this connection closed (client_disconnect); 47 s connected when recorded; reconnects within 60 s are merged into this session.')
    expect(wsSessionLine(liveDetail({ reason: '' }))).toContain('this connection 04:00:10–04:00:52;')
    for (const connected of [0, null, '47', Number.NaN]) {
      const line = wsSessionLine(liveDetail({ connected_s_at_write: connected })) ?? ''
      expect(line, String(connected)).not.toContain('connected when recorded')
    }
    for (const line of [bare, wsSessionLine(liveDetail({ started_at: null, ended_at: null, reason: null }))]) {
      expect(line).not.toMatch(/undefined|null|—|\b0 s\b/)
    }
  })

  it('never a reconnect count or a total duration, even from a row that still carries the old keys', () => {
    const line = wsSessionLine(liveDetail({ reconnects: 3, duration_s: 600 })) ?? ''
    expect(line).toBe(SAMPLE)
    expect(line).not.toMatch(/\d+ reconnects?\b|10 min/)
  })

  it('nothing at all for a detail that is not a live session (old ws_connect rows render as today)', () => {
    expect(wsSessionLine(null)).toBeNull()
    expect(wsSessionLine(undefined)).toBeNull()
    expect(wsSessionLine({})).toBeNull()
    expect(wsSessionLine({ channel: 'email', started_at: '2026-10-31T09:00:00Z' })).toBeNull()
  })
})

describe('auditRowNote', () => {
  it('only an ACCESS row whose action is ws_session has a note', () => {
    const detail = liveDetail()
    expect(auditRowNote({ source: 'access', action: 'ws_session', detail })).toBe(SAMPLE)
    expect(auditRowNote({ source: 'access', action: 'ws_connect', detail })).toBeNull()
    expect(auditRowNote({ source: 'access', action: 'ws_disconnect', detail })).toBeNull()
    expect(auditRowNote({ source: 'settings', action: 'ws_session', detail })).toBeNull()
  })
})
