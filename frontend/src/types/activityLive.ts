/**
 * The activity live tail's, Inbox › Activity's wire vocabulary (W21d ACT-02).
 *
 * Mirrors `backend/app/services/activity/live_vocab.py`. The backend parity
 * test (`backend/tests/test_activity_live_vocab_frontend_parity.py`) reads this
 * file as TEXT, so every mirrored value stays a literal (`as const` array or
 * object, a plain number): change both files together.
 */

// ── the live tail ────────────────────────────────────────────────────────────

/** The SSE `event:` names the stream sends; any other event is ignored. */
export const SSE_EVENTS = ['ready', 'activity', 'refresh', 'bye'] as const
export type SseEvent = (typeof SSE_EVENTS)[number]

/** `refresh`: the client re-reads page 0 of the feed. */
export const REFRESH_REASONS = ['gap', 'overflow', 'unavailable'] as const
export type RefreshReason = (typeof REFRESH_REASONS)[number]

/** `bye`: the server ends the stream; the client reconnects. */
export const BYE_REASONS = ['max_age', 'token_expired', 'unavailable'] as const
export type ByeReason = (typeof BYE_REASONS)[number]

/** A `: heartbeat` comment arrives after this many seconds without a frame. */
export const HEARTBEAT_SECONDS = 15

/** The stream's `retry:` line: the client's first reconnect backoff step. */
export const RETRY_MILLISECONDS = 5_000

/**
 * The stream route's own refusals answer `{detail: {code, message}}` with one
 * of these sentences (403 / 400 / 429 / 503). The guard's own 403 is a plain
 * string detail and is not listed here.
 */
export const STREAM_ERROR_SENTENCES = {
  'interactive_login_required': "The live activity stream needs a signed-in session; API keys read GET /api/v1/projects/{project_id}/activity.",
  'invalid_last_event_id': "Last-Event-ID must be a stream id such as 1730000000000-0.",
  'too_many_streams': "You already have 3 live activity views open; close one to open another.",
  'server_busy': "The server has too many live views open; try again in a minute.",
  'live_unavailable': "Live updates are unavailable right now; the feed still refreshes every 30 seconds.",
} as const
export type StreamErrorCode = keyof typeof STREAM_ERROR_SENTENCES

/** The client's view of one live stream (client-side only, not on the wire). */
export type LiveStatus = 'off' | 'connecting' | 'live' | 'reconnecting' | 'unavailable' | 'refused'

/**
 * What the toggle / status line says per status. `refused` shows the
 * refusal's own sentence (the server's `detail.message`); this text is only
 * the fallback when the refusal carried none. `off` says nothing.
 */
export const LIVE_STATUS_TEXT: Record<LiveStatus, string> = {
  'off': '',
  'connecting': 'Connecting…',
  'live': 'Live',
  'reconnecting': 'Reconnecting…',
  'unavailable': 'Live updates unavailable',
  'refused': 'Live updates were refused.',
}

// ── /activity/mine ───────────────────────────────────────────────────────────

/** `all` = `mine` ∪ `involving`. */
export const MINE_VIEWS = ['all', 'mine', 'involving'] as const
export type MineView = (typeof MINE_VIEWS)[number]

/** Why a row is in the caller's feed; also the server's priority order. */
export const INVOLVEMENTS = ['actor', 'mentioned', 'assigned', 'authored'] as const
export type Involvement = (typeof INVOLVEMENTS)[number]

export const INVOLVEMENT_LABELS: Record<Involvement, string> = {
  'actor': 'You',
  'mentioned': 'Mentioned you',
  'assigned': 'Assigned to you',
  'authored': 'Your test case',
}

/** The server's default window when `since` is not sent. */
export const MINE_DEFAULT_DAYS = 7
