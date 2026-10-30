/**
 * W23d AGT-14: the HTTP exchanges a test made, as its reporter captured them (opt-in per run) and the server
 * redacted them BEFORE storing (`GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges`).
 *
 * The constants mirror `backend/app/services/http_exchange_vocab.py` value for value (pinned by
 * `backend/tests/test_payload_metrics_vocab_parity.py`, which reads this file as TEXT — keep every constant a
 * plain literal on its own `export const` line, numbers without separators, strings in one piece, records as one
 * `key: 'value',` line per entry closed by a column-0 `} as const`). Enum-like fields are plain strings on the wire.
 *
 * Every header, URL, body and sentence is attacker-controlled or server-written text: render it as React text,
 * never as HTML, and never build a link from it.
 *
 * Absence is not health: a test without stored exchanges reads `EXCHANGE_PANEL_SENTENCES.none`, never a blank.
 */

// ── the vocabulary (http_exchange_vocab.py) ─────────────────────────────────

export const EXCHANGE_DOC_VERSION = 1
/** More in one result / one live post: the first 20 are kept, the rest counted `dropped`. */
export const EXCHANGES_PER_TEST_MAX = 20
/** Per run (JSON batch: enforced in the request model; live: counted in Mongo before the insert). */
export const EXCHANGES_PER_RUN_MAX = 1000
/** A body is parsed up to this many UTF-8 bytes; beyond → cut first (`response_schema_state: too_large`). */
export const BODY_WIRE_MAX_BYTES = 65536
/** What is stored and shown, AFTER redaction; beyond → cut + `TRUNCATION_MARKER`. */
export const BODY_STORED_MAX_BYTES = 16384
export const TRUNCATION_MARKER = '…[truncated: {omitted} bytes not stored]'
export const HEADERS_MAX = 50
export const HEADER_NAME_MAX = 128
export const HEADER_VALUE_MAX = 2048
export const URL_MAX = 2048
export const ENDPOINT_MAX = 300
/** GET items per test (== EXCHANGES_PER_TEST_MAX). */
export const LIST_PAGE_MAX = 20
/** == `redaction_service.REDACTED`: a whole header / query / body value replaced by the server. */
export const REDACTED = '[REDACTED]'

export const CAPTURE_SOURCES = ['json_batch', 'live'] as const
export type CaptureSource = (typeof CAPTURE_SOURCES)[number]

/** `too_large` = the wire body exceeded BODY_WIRE_MAX_BYTES. */
export const SCHEMA_STATES = ['parsed', 'not_json', 'empty', 'too_large'] as const
export type SchemaState = (typeof SCHEMA_STATES)[number]

/** Header names (compared lower-case) whose VALUE the server replaces with `[REDACTED]`; the name is kept. */
export const HEADER_DENYLIST = [
  'authorization', 'proxy-authorization', 'cookie', 'set-cookie', 'x-api-key', 'api-key', 'x-auth-token',
  'x-access-token', 'x-csrf-token', 'x-xsrf-token', 'x-amz-security-token', 'x-goog-api-key',
  'x-session-token', 'private-token', 'x-gitlab-token', 'x-hub-signature', 'x-hub-signature-256',
] as const

/** A header name ENDING in one of these is redacted too (`X-GitHub-Token`, `X-Client-Secret` …). */
export const HEADER_SECRET_SUFFIXES = ['token', 'secret', 'password', 'api-key', 'apikey', 'signature', 'credential'] as const

/** Query parameter names (lower-case) whose VALUE the server replaces with `[REDACTED]`. */
export const QUERY_DENYLIST = [
  'token', 'access_token', 'id_token', 'refresh_token', 'api_key', 'apikey', 'key', 'code', 'password',
  'secret', 'client_secret', 'sig', 'signature', 'x-amz-signature', 'x-amz-credential', 'x-amz-security-token', 'auth',
] as const

/** The panel's copy, rendered verbatim (`{dropped}` is filled in by the panel). */
export const EXCHANGE_PANEL_SENTENCES = {
  none: 'No HTTP exchanges were captured for this test.',
  how: 'Capture is opt-in per run: pytest --testlookup-capture-http (or TESTLOOKUP_CAPTURE_HTTP=1).',
  redacted: 'Secrets in headers, query strings and bodies are replaced with [REDACTED] before they are stored.',
  truncated: 'Body cut to 16 KB when stored.',
  dropped: '{dropped} more exchanges of this test were not stored (limit 20 per test, 1 000 per run).',
} as const
export type ExchangePanelSentence = keyof typeof EXCHANGE_PANEL_SENTENCES

/** The contract stage's summary / violation text (server-written; the UI shows the server's own sentence). */
export const CONTRACT_SENTENCES = {
  no_capture: 'No HTTP exchanges were captured for these failures; enable capture in the reporter to validate API contracts.',
  no_baseline: '{endpoint}: no earlier run of this project captured it, so there is nothing to compare with yet.',
  missing_field: 'field `{field}` missing from {method} {endpoint}; drift since build {build}',
  type_changed: 'field `{field}` in {method} {endpoint} changed from {was} to {now} since build {build}',
  body_not_checked: '{method} {endpoint}: the response was not JSON or not complete, so its fields were not compared.',
  no_drift: "No API contract drift: {checked} responses match the previous runs' fields.",
} as const
export type ContractSentence = keyof typeof CONTRACT_SENTENCES

// ── the read shapes (schemas.HttpMessageRead / HttpExchangeRead / HttpExchangeListResponse) ──────────────

/** One side of an exchange as stored: headers and body already redacted (and the body cut to 16 KB). */
export interface HttpMessage {
  headers: Record<string, string>
  /** `null` = not captured (a non-text or streamed body, or none sent). */
  body: string | null
  /** The SDK or the store cut the body (`EXCHANGE_PANEL_SENTENCES.truncated`). */
  body_truncated: boolean
  /** The body's size before the store cut. */
  body_bytes: number
}

export interface HttpExchange {
  /** 0..19, the order within the test. */
  seq: number
  method: string
  /** The TEMPLATE (drift key), e.g. `/api/products/{id}`. */
  endpoint: string
  /** The concrete URL as sent, its secret query values already `[REDACTED]`. */
  url: string
  host: string | null
  /** `null` = no response (a transport error, see `error`). */
  status: number | null
  duration_ms: number | null
  started_at: string | null
  /** The client's transport error class name, e.g. `ConnectTimeout`. */
  error: string | null
  request: HttpMessage
  response: HttpMessage
  /** One of SCHEMA_STATES (a plain string on the wire). */
  response_schema_state: SchemaState | string
  /** One of CAPTURE_SOURCES. */
  source: CaptureSource | string
  captured_at: string
  /** Any header, query or body value was redacted (the row's `Redacted` badge). */
  redacted: boolean
}

export interface HttpExchangeList {
  /** Seq order, ≤ LIST_PAGE_MAX. */
  items: HttpExchange[]
  /** Docs stored for this test — a count, never `items.length`. */
  total: number
  /** Exchanges of this test the server did not store (the per-test / per-run caps). */
  dropped: number
  /** The run holds EXCHANGES_PER_RUN_MAX docs, so "none" may mean "not stored". */
  run_cap_reached: boolean
}

// ── the reader ──────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null
}

function readMessage(raw: unknown): HttpMessage {
  const msg = isRecord(raw) ? raw : {}
  const headers: Record<string, string> = {}
  if (isRecord(msg.headers)) {
    for (const [name, value] of Object.entries(msg.headers)) {
      if (typeof value === 'string') headers[name] = value
    }
  }
  const body = textOrNull(msg.body)
  return {
    headers,
    body,
    body_truncated: msg.body_truncated === true,
    body_bytes: count(msg.body_bytes) ?? (body === null ? 0 : body.length),
  }
}

function readExchange(raw: Record<string, unknown>, index: number): HttpExchange {
  const url = text(raw.url)
  return {
    seq: count(raw.seq) ?? index,
    method: text(raw.method),
    endpoint: text(raw.endpoint) || url,
    url,
    host: textOrNull(raw.host),
    status: typeof raw.status === 'number' && Number.isFinite(raw.status) ? raw.status : null,
    duration_ms: count(raw.duration_ms),
    started_at: textOrNull(raw.started_at),
    error: textOrNull(raw.error),
    request: readMessage(raw.request),
    response: readMessage(raw.response),
    response_schema_state: text(raw.response_schema_state, 'empty'),
    source: text(raw.source),
    captured_at: text(raw.captured_at),
    redacted: raw.redacted === true,
  }
}

/**
 * The GET answer, typed. An answer without an `items` array (a proxy page, an older server, a broken body) is
 * `null` — the caller says "Could not load the HTTP exchanges.", never "none captured", and never crashes (the
 * W15 lesson). Malformed items are skipped, fields are normalised (`null` / `''` / `0` / `false` where absent);
 * reading an already-read list again yields an equal list.
 */
export function parseExchangeList(raw: unknown): HttpExchangeList | null {
  if (!isRecord(raw) || !Array.isArray(raw.items)) return null
  const items = raw.items
    .filter(isRecord)
    .map((item, index) => readExchange(item, index))
  return {
    items,
    total: count(raw.total) ?? items.length,
    dropped: count(raw.dropped) ?? 0,
    run_cap_reached: raw.run_cap_reached === true,
  }
}
