/**
 * W23d AGT-14 — the `HTTP exchanges` tab's words and shapes. Pure: no React,
 * no request. The panel renders what these return; its tests import the same
 * strings, so a copy change is one edit.
 *
 * Absence is not health: an answer with no stored exchange says so in a
 * sentence (and why it may be empty), never a blank panel; a read that failed
 * says it could not load, never "none captured".
 */
import {
  EXCHANGE_PANEL_SENTENCES,
  REDACTED,
  type HttpExchange,
  type HttpExchangeList,
  type HttpMessage,
} from '@/types/httpExchanges'

export { EXCHANGE_PANEL_SENTENCES, REDACTED }

/** A failed read (404 / 503 / network): the one load-error phrasing. */
export const EXCHANGES_LOAD_ERROR = 'Could not load the HTTP exchanges.'
export const EXCHANGES_LOADING = 'Loading the HTTP exchanges…'
/** `run_cap_reached` with nothing stored for this test: "none" may mean "not stored". */
export const RUN_CAP_SENTENCE = "This run reached the 1 000-exchange limit before this test's exchanges were stored."
export const BODY_NOT_CAPTURED = 'Body not captured.'
export const REDACTED_BADGE = 'Redacted'
export const NO_RESPONSE = 'No response'

/** One exchange's collapsed line: `GET` · `/api/products/{id}` · `200` · `184 ms` · `Redacted`. */
export interface ExchangeSummary {
  method: string
  /** The endpoint template (the drift key); the URL when the server sent none. */
  endpoint: string
  /** The concrete URL as sent (query values already redacted) — the endpoint's `title`. */
  url: string
  /** The HTTP status, or the transport error's class name, or "No response". */
  status: string
  /** "184 ms" / "1.2 s"; empty when the reporter sent no duration. */
  duration: string
  redacted: boolean
}

/** "184 ms" under a second, "1.2 s" from one; `null` / a negative is not measured (""). */
export function formatExchangeDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function summaryLine(ex: HttpExchange): ExchangeSummary {
  const status = typeof ex.status === 'number'
    ? String(ex.status)
    : (typeof ex.error === 'string' && ex.error.trim() ? ex.error : NO_RESPONSE)
  return {
    method: ex.method,
    endpoint: ex.endpoint || ex.url,
    url: ex.url,
    status,
    duration: formatExchangeDuration(ex.duration_ms),
    redacted: Boolean(ex.redacted),
  }
}

/** A header value the server replaced whole: rendered as a neutral `[REDACTED]` badge. */
export function isRedactedValue(value: unknown): boolean {
  return value === REDACTED
}

/** One side's headers, in the server's order (it sorted them by name when it cut them). */
export function headerRows(headers: Record<string, string> | null | undefined): Array<[string, string]> {
  if (!headers || typeof headers !== 'object') return []
  return Object.entries(headers).map(([name, value]) => [name, String(value)])
}

/**
 * A JSON object / array body pretty-printed with two spaces; anything else
 * (text, a cut body that no longer parses, a JSON scalar) as it came. Never
 * throws. `null` stays `null` (the body was not captured).
 */
export function prettyBody(text: string | null | undefined): string | null {
  if (text === null || text === undefined) return null
  try {
    const value: unknown = JSON.parse(text)
    if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2)
  } catch {
    // Not JSON: shown as it came.
  }
  return text
}

/** The note under one side's body: not captured, or cut to 16 KB when stored. */
export function bodyNote(msg: HttpMessage | null | undefined): string | null {
  if (!msg || msg.body === null || msg.body === undefined) return BODY_NOT_CAPTURED
  if (msg.body_truncated) return EXCHANGE_PANEL_SENTENCES.truncated
  return null
}

export interface PanelSentences {
  /** `total === 0`: the "none" and "how" sentences (+ the run-cap note) — never a blank panel. */
  empty: string[] | null
  /** The list's header: "3 exchanges · Secrets in headers … are replaced with [REDACTED] …". */
  header: string | null
  /** `dropped > 0`: what the server did not store for this test, at the list's end. */
  dropped: string | null
}

export function panelSentences(list: HttpExchangeList): PanelSentences {
  const total = typeof list.total === 'number' ? list.total : list.items.length
  const dropped = typeof list.dropped === 'number' && list.dropped > 0
    ? EXCHANGE_PANEL_SENTENCES.dropped.replace('{dropped}', String(list.dropped))
    : null
  if (total === 0 || list.items.length === 0) {
    const empty: string[] = [EXCHANGE_PANEL_SENTENCES.none, EXCHANGE_PANEL_SENTENCES.how]
    if (list.run_cap_reached) empty.push(RUN_CAP_SENTENCE)
    return { empty, header: null, dropped }
  }
  return {
    empty: null,
    header: `${total} ${total === 1 ? 'exchange' : 'exchanges'} · ${EXCHANGE_PANEL_SENTENCES.redacted}`,
    dropped,
  }
}
