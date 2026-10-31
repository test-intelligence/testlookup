/**
 * The words of /settings/audit that are more than a field (W25a HYG-06 (a), contract §4.1 (b) as ruled by the
 * orchestrator: the `ws_session` row is append-only).
 *
 * A live WebSocket session writes AT MOST ONE `access_audit_logs` row, `ws_session`, INSERTed once at the first
 * disconnect where the session's accumulated connected time reaches 5 s; reconnects within 60 s never write, a
 * session under 5 s writes nothing, and the row is never updated. Its detail (`routers/live.py`
 * `_ws_session_detail`) is: `channel: "live"`, `reason` (THIS connection's close reason), `started_at` / `ended_at`
 * (THIS connection, ISO UTC), `session_started_at` (the session's first connect), `connected_s_at_write` (connected
 * seconds accumulated when the row was written) and `reconnect_policy` (a fixed sentence). The row holds no
 * reconnect count and no total duration, so the sentence claims neither — and a key the row lacks is left out,
 * never shown as "undefined", "—" or "0 s". Times are local, as the row's own time beside it.
 */
import { formatClock, formatCompactDateTime } from '@/utils/formatters'

/** The rule the row states, used when a row carries no `reconnect_policy` (`ws_audit_policy.RECONNECT_POLICY`). */
export const WS_RECONNECT_POLICY = 'reconnects within 60 s are merged into this session'
export const WS_SESSION_LABEL = 'Live session'

/** "42 s" under a minute, "12 min" under an hour, "2 h 5 min" (or "2 h") beyond; null for a non-finite / negative value. */
export function formatSessionDuration(seconds: number): string | null {
  if (!Number.isFinite(seconds) || seconds < 0) return null
  const s = Math.round(seconds)
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`
}

/** A non-empty string value of the detail, trimmed; null otherwise. */
function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** An ISO timestamp of the detail that parses; null otherwise. */
function instant(value: unknown): string | null {
  const iso = text(value)
  return iso && Number.isFinite(Date.parse(iso)) ? iso : null
}

/**
 * The sentence beside a `ws_session` access row, from ONLY what the row knows:
 * "Live session from Oct 31, 04:00; this connection 04:00:10–04:00:52 (client_disconnect); 47 s connected when
 * recorded; reconnects within 60 s are merged into this session."
 * Each clause appears only when its keys are present and readable; the policy is the row's own sentence (the
 * literal when the key is missing). Null for a detail that is not a live session's (`channel: "live"`), so any
 * other row renders exactly as before. A key the append-only row never writes (`reconnects`, `duration_s`) is
 * never read.
 */
export function wsSessionLine(detail: Record<string, unknown> | null | undefined): string | null {
  if (!detail || typeof detail !== 'object' || detail.channel !== 'live') return null
  const sessionStart = instant(detail.session_started_at)
  const started = instant(detail.started_at)
  const ended = instant(detail.ended_at)
  const reason = text(detail.reason)
  const connected = typeof detail.connected_s_at_write === 'number' && detail.connected_s_at_write > 0
    ? formatSessionDuration(detail.connected_s_at_write)
    : null
  const policy = (text(detail.reconnect_policy) ?? WS_RECONNECT_POLICY).replace(/\.+$/, '')

  const clauses = [sessionStart ? `${WS_SESSION_LABEL} from ${formatCompactDateTime(sessionStart)}` : WS_SESSION_LABEL]
  if (started && ended) clauses.push(`this connection ${formatClock(started)}–${formatClock(ended)}${reason ? ` (${reason})` : ''}`)
  else if (reason) clauses.push(`this connection closed (${reason})`)
  if (connected) clauses.push(`${connected} connected when recorded`)
  clauses.push(policy)
  return `${clauses.join('; ')}.`
}

/** The muted note beside an audit row: only an ACCESS row whose action is `ws_session` has one. */
export function auditRowNote(row: { source: string; action: string; detail: Record<string, unknown> | null }): string | null {
  return row.source === 'access' && row.action === 'ws_session' ? wsSessionLine(row.detail) : null
}
