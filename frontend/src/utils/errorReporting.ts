/**
 * TestLookup — Frontend error and Web Vitals reporting.
 *
 * Collects:
 *   - Uncaught JavaScript errors (window.onerror)
 *   - Unhandled promise rejections (window.onunhandledrejection)
 *   - React Error Boundary errors (via reportBoundaryError)
 *   - Core Web Vitals (via web-vitals library)
 *
 * All events are batched and sent to POST /api/v1/observability/frontend
 * using navigator.sendBeacon on page unload and a periodic flush timer.
 */

import type { Metric } from 'web-vitals'

// When VITE_API_BASE_URL is unset, use a same-origin relative URL so the
// reporter follows the page through any ingress (k8s, gcp, aws, homelab).
// VITE_API_URL is also accepted as a legacy alias.
const API_ENDPOINT = `${import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/observability/frontend`

// ── Types ─────────────────────────────────────────────────────────────────────

interface FrontendError {
  type: 'error' | 'unhandledrejection' | 'boundary'
  message: string
  stack?: string
  component_stack?: string
  url: string
  user_agent: string
  timestamp: string
  context?: Record<string, unknown>
}

interface WebVitalReport {
  name: string
  value: number
  rating: string
  url: string
  delta?: number
}

interface TelemetryBatch {
  errors: FrontendError[]
  vitals: WebVitalReport[]
}

// ── Batch buffer ──────────────────────────────────────────────────────────────

/**
 * Per-request ceiling, kept under the user agent's beacon quota.
 *
 * `navigator.sendBeacon` returns `false` — it does not throw, and it does not
 * partially send — once a body exceeds the quota (64 KB in Chromium). Measured
 * against the live deployment with error-boundary reports carrying a 4 KB stack
 * and a 2 KB component stack: 5 events (31 KB) were accepted, 10 (63 KB) were
 * refused, and the same refused body was accepted by the server over `fetch`
 * with a 202. So the payload is fine; only the transport has the limit.
 *
 * Ten boundary errors in one 5 s window is not exotic — a component throwing on
 * every render produces them in a fraction of a second, which is exactly the
 * incident the report exists for. Batches are therefore split to fit.
 */
const BEACON_LIMIT_BYTES = 60_000

/**
 * Per-event truncation, applied at capture.
 *
 * Bounds a single event to roughly 6 KB so one enormous stack cannot produce a
 * chunk that nothing can send. The backend truncates `stack` at 5000 and
 * `message` at 2000 anyway, so this discards only what would have been dropped
 * server-side — except that here it is discarded before it can cost us the
 * whole batch.
 */
const MAX_STACK_CHARS = 4000
const MAX_COMPONENT_STACK_CHARS = 2000

/** Bound the buffer so a render loop cannot grow it without limit. */
const MAX_BUFFERED_ERRORS = 50
const MAX_BUFFERED_VITALS = 50

/** Stop rescheduling after this many consecutive failed flushes. */
const MAX_FLUSH_RETRIES = 3

const _batch: TelemetryBatch = { errors: [], vitals: [] }
let _flushTimer: ReturnType<typeof setTimeout> | null = null
let _droppedEvents = 0
let _consecutiveFailures = 0

function _scheduleFlush(): void {
  if (_flushTimer) return
  _flushTimer = setTimeout(() => {
    _flushTimer = null
    flush()
  }, 5000) // batch window: 5 s
}

/** Serialized size of a batch, measured the way the transport measures it. */
function _sizeOf(batch: TelemetryBatch): number {
  return JSON.stringify(batch).length
}

/**
 * Split a batch into pieces that each fit under {@link BEACON_LIMIT_BYTES}.
 *
 * Greedy: start a new piece when the next event would push the current one over.
 * Per-event truncation at capture guarantees no single event can exceed the
 * limit on its own, so every event lands in some piece.
 */
function _partition(batch: TelemetryBatch): TelemetryBatch[] {
  if (_sizeOf(batch) <= BEACON_LIMIT_BYTES) return [batch]

  const pieces: TelemetryBatch[] = []
  let current: TelemetryBatch = { errors: [], vitals: [] }
  let currentSize = _sizeOf(current)

  const add = (item: FrontendError | WebVitalReport, into: 'errors' | 'vitals') => {
    const itemSize = JSON.stringify(item).length + 1 // + comma
    const isEmpty = current.errors.length === 0 && current.vitals.length === 0
    if (!isEmpty && currentSize + itemSize > BEACON_LIMIT_BYTES) {
      pieces.push(current)
      current = { errors: [], vitals: [] }
      currentSize = _sizeOf(current)
    }
    ;(current[into] as unknown[]).push(item)
    currentSize += itemSize
  }

  for (const err of batch.errors) add(err, 'errors')
  for (const vital of batch.vitals) add(vital, 'vitals')
  if (current.errors.length || current.vitals.length) pieces.push(current)
  return pieces
}

/**
 * Hand one payload to the transport.
 *
 * Returns whether the user agent took ownership of it. A `false` from
 * `sendBeacon` means the request was **not** queued — the caller must keep the
 * events, not assume they were sent.
 */
function _transmit(payload: string): boolean {
  try {
    if (navigator.sendBeacon) {
      // sendBeacon is the only transport that survives page unload.
      const blob = new Blob([payload], { type: 'application/json' })
      return navigator.sendBeacon(API_ENDPOINT, blob)
    }
    // No sendBeacon at all (not a real browser we ship to, but keep the path).
    fetch(API_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {
      /* best-effort — ignore network failures */
    })
    return true
  } catch {
    /* never throw from error reporting */
    return false
  }
}

/**
 * Send the current batch to the backend.
 *
 * Events are cleared only for the pieces the transport actually accepted;
 * anything refused goes back into the buffer for the next flush. Previously the
 * buffer was emptied up front and the beacon's return value ignored, so a
 * refused send destroyed the batch silently.
 */
export function flush(): void {
  if (_batch.errors.length === 0 && _batch.vitals.length === 0 && _droppedEvents === 0) return

  const errors = [..._batch.errors]
  const vitals = [..._batch.vitals]
  _batch.errors.length = 0
  _batch.vitals.length = 0

  // Tell the backend the report is incomplete. Silent truncation is
  // indistinguishable from silence, and this path only fires during a storm.
  if (_droppedEvents > 0) {
    errors.push({
      type: 'error',
      message: `telemetry buffer overflow: ${_droppedEvents} events dropped before send`,
      url: typeof window !== 'undefined' ? window.location.href : '',
      user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      timestamp: new Date().toISOString(),
      context: { dropped: _droppedEvents },
    })
    _droppedEvents = 0
  }

  const refused: TelemetryBatch = { errors: [], vitals: [] }
  for (const piece of _partition({ errors, vitals })) {
    let payload: string
    try {
      payload = JSON.stringify(piece)
    } catch {
      continue // unserializable — dropping it is the only option
    }
    if (!_transmit(payload)) {
      refused.errors.push(...piece.errors)
      refused.vitals.push(...piece.vitals)
    }
  }

  if (refused.errors.length || refused.vitals.length) {
    // Put them back at the front — oldest events are the ones with context.
    _batch.errors.unshift(...refused.errors)
    _batch.vitals.unshift(...refused.vitals)
    _enforceBufferBounds()
    if (++_consecutiveFailures < MAX_FLUSH_RETRIES) _scheduleFlush()
  } else {
    _consecutiveFailures = 0
  }
}

/** Trim the buffer to its bounds, recording how much was discarded. */
function _enforceBufferBounds(): void {
  if (_batch.errors.length > MAX_BUFFERED_ERRORS) {
    _droppedEvents += _batch.errors.length - MAX_BUFFERED_ERRORS
    _batch.errors.splice(MAX_BUFFERED_ERRORS)
  }
  if (_batch.vitals.length > MAX_BUFFERED_VITALS) {
    _droppedEvents += _batch.vitals.length - MAX_BUFFERED_VITALS
    _batch.vitals.splice(MAX_BUFFERED_VITALS)
  }
}

// ── Privacy sanitization (PR-5) ──────────────────────────────────────────────

/** Strip full file paths from stack traces, keeping only filename + line number. */
function _sanitizeStack(stack: string | undefined): string | undefined {
  if (!stack) return stack
  // Replace full paths like /home/user/project/src/file.ts:42:10 → file.ts:42:10
  return stack.replace(/(?:\/[\w./-]+\/|[A-Z]:\\[\w.\\-]+\\)([\w.-]+:\d+)/g, '$1')
}

/** Redact potential PII patterns from error messages. */
function _sanitizeMessage(msg: string): string {
  return msg
    // Email addresses
    .replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, '[REDACTED_EMAIL]')
    // Phone numbers
    .replace(/\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/g, '[REDACTED_PHONE]')
    // Bearer tokens — lowered floor from 20 → 8 to catch short opaque tokens
    // (e.g., session IDs) while still avoiding obvious false positives like
    // "Bearer token".
    .replace(/Bearer\s+[A-Za-z0-9\-_.=]{8,}/gi, 'Bearer [REDACTED]')
    // API keys — accept shorter keys (8+) because some integrations ship
    // 10-12 character keys and would otherwise leak.
    .replace(/api[_-]?key\s*[:=]\s*['"]?[A-Za-z0-9\-_]{8,}['"]?/gi, 'api_key=[REDACTED]')
    // Known TestLookup API key prefixes (tl_, qai_, scim_) — catches keys
    // that appear bare in a message without the "api_key=" antecedent.
    .replace(/\b(?:tl|qai|scim)_[A-Za-z0-9\-_]{16,}/g, '[REDACTED_KEY]')
    // JWT tokens (three base64url-ish sections separated by dots)
    .replace(/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, '[REDACTED_JWT]')
    // Connection strings with passwords
    .replace(/(\/\/[^:]+:)[^@]{4,}(@)/g, '$1[REDACTED]$2')
}

// ── Error capture ─────────────────────────────────────────────────────────────

function _capture(error: FrontendError): void {
  // Sanitize before buffering (PR-5: never send raw PII to backend)
  error.message = _sanitizeMessage(error.message)
  error.stack = _sanitizeStack(error.stack)?.slice(0, MAX_STACK_CHARS)
  if (error.component_stack) {
    error.component_stack = _sanitizeStack(error.component_stack)?.slice(
      0,
      MAX_COMPONENT_STACK_CHARS,
    )
  }
  _batch.errors.push(error)
  _enforceBufferBounds()
  _scheduleFlush()
}

/** Report an error caught by a React Error Boundary. */
export function reportBoundaryError(error: Error, componentStack: string): void {
  _capture({
    type: 'boundary',
    message: error.message,
    stack: error.stack,
    component_stack: componentStack,
    url: window.location.href,
    user_agent: navigator.userAgent,
    timestamp: new Date().toISOString(),
  })
}

// ── Global error listeners ────────────────────────────────────────────────────

/** Install window-level error and unhandledrejection handlers. Call once from main.tsx. */
export function installGlobalErrorHandlers(): void {
  window.addEventListener('error', (event) => {
    _capture({
      type: 'error',
      message: event.message || String(event.error),
      stack: event.error?.stack,
      url: event.filename || window.location.href,
      user_agent: navigator.userAgent,
      timestamp: new Date().toISOString(),
      context: {
        lineno: event.lineno,
        colno: event.colno,
      },
    })
  })

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason
    _capture({
      type: 'unhandledrejection',
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
      url: window.location.href,
      user_agent: navigator.userAgent,
      timestamp: new Date().toISOString(),
    })
  })

  // Flush on page unload
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
  window.addEventListener('pagehide', flush)
}

// ── Web Vitals reporting ──────────────────────────────────────────────────────

/** Called by useWebVitals hook with each Core Web Vital measurement. */
export function reportWebVital(metric: Metric): void {
  _batch.vitals.push({
    name: metric.name,
    value: metric.value,
    rating: metric.rating,
    url: window.location.href,
    delta: metric.delta,
  })
  _enforceBufferBounds()
  _scheduleFlush()
}
