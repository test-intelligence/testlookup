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

const API_ENDPOINT = `${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}/api/v1/observability/frontend`

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

const _batch: TelemetryBatch = { errors: [], vitals: [] }
let _flushTimer: ReturnType<typeof setTimeout> | null = null

function _scheduleFlush(): void {
  if (_flushTimer) return
  _flushTimer = setTimeout(() => {
    _flushTimer = null
    flush()
  }, 5000) // batch window: 5 s
}

/** Send the current batch to the backend. Clears the buffer on success. */
export function flush(): void {
  if (_batch.errors.length === 0 && _batch.vitals.length === 0) return

  const payload = JSON.stringify({ errors: [..._batch.errors], vitals: [..._batch.vitals] })
  _batch.errors.length = 0
  _batch.vitals.length = 0

  try {
    // sendBeacon works even during page unload
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' })
      navigator.sendBeacon(API_ENDPOINT, blob)
    } else {
      // Fallback: fire-and-forget fetch
      fetch(API_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(() => {
        /* best-effort — ignore network failures */
      })
    }
  } catch {
    /* never throw from error reporting */
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
    // Bearer tokens
    .replace(/Bearer\s+[A-Za-z0-9\-_.]{20,}/gi, 'Bearer [REDACTED]')
    // API keys
    .replace(/api[_-]?key\s*[:=]\s*['"]?[A-Za-z0-9\-_]{16,}['"]?/gi, 'api_key=[REDACTED]')
    // Connection strings with passwords
    .replace(/(\/\/[^:]+:)[^@]{4,}(@)/g, '$1[REDACTED]$2')
}

// ── Error capture ─────────────────────────────────────────────────────────────

function _capture(error: FrontendError): void {
  // Sanitize before buffering (PR-5: never send raw PII to backend)
  error.message = _sanitizeMessage(error.message)
  error.stack = _sanitizeStack(error.stack)
  if (error.component_stack) {
    error.component_stack = _sanitizeStack(error.component_stack)
  }
  _batch.errors.push(error)
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
  _scheduleFlush()
}
