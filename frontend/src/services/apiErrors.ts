// Pure helpers for the shared Axios error interceptor (services/api.ts).
// Extracted so the toast-visibility policy + message extraction are unit-tested
// without wrangling the module-level axios instance / auth store.

/** A FastAPI/Pydantic 422 validation-error item: ``{ loc, msg, type }``. */
interface ValidationErrorItem {
  msg?: unknown
  loc?: unknown
}

/**
 * Whether the response interceptor should surface a toast for this status.
 *
 * Suppresses only:
 *   - 401 — handled by the single-flight token-refresh flow (a toast here would
 *     be noise during a normal expiry/refresh).
 *   - 404 — frequently an EXPECTED "no data yet" from optional reads.
 *
 * Notably 422 is now surfaced. A silent 422 (typically a strict Pydantic enum
 * over a ``String(N)`` column) was the documented "empty page, no error toast"
 * footgun — the request failed, SWR yielded ``undefined``, and the page rendered
 * a blank state with zero feedback. Surfacing it makes the failure diagnosable.
 *
 * ``request.suppressToast`` (the axios request config, see ``services/api.ts``)
 * silences the toast for that one request — its caller owns the message.
 */
export function shouldToastError(
  status: number | undefined,
  request?: { suppressToast?: boolean } | null,
): boolean {
  // A caller that renders the failure itself (a chart frame: VIZ-107) opts out
  // per request, so six failing charts do not raise six toasts. Every request
  // that does not set the flag keeps exactly the policy below.
  if (request?.suppressToast === true) return false
  return status !== 401 && status !== 404
}

/**
 * Best-effort human-readable message from a FastAPI error ``detail``.
 *
 * ``detail`` may be a plain string (``HTTPException(detail="…")``) OR, for a 422,
 * an ARRAY of ``{ loc, msg, type }`` validation items — in which case naively
 * doing ``detail || message`` renders "[object Object]". Joins the item ``msg``s
 * (with their field path) so a validation error reads like
 * "Validation error: body.status — value is not a valid enumeration member".
 */
export function extractErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationErrorItem[])
      .map((item) => {
        if (!item || typeof item !== 'object') return ''
        const msg = 'msg' in item && item.msg != null ? String(item.msg) : ''
        if (!msg) return ''
        const loc = Array.isArray(item.loc)
          ? item.loc.filter((p) => p !== 'body').join('.')
          : ''
        return loc ? `${loc} — ${msg}` : msg
      })
      .filter(Boolean)
    if (parts.length) return `Validation error: ${parts.join('; ')}`
  }
  if (detail && typeof detail === 'object') {
    const report = detail as Record<string, unknown>
    const preferred = ['message', 'reason', 'verdict', 'candidate_tier', 'gate_run_id']
      .flatMap((key) => {
        const value = report[key]
        return value == null || value === '' ? [] : [`${key}: ${String(value)}`]
      })
    if (preferred.length) return preferred.join('; ')
    const bounded = Object.entries(report)
      .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
      .slice(0, 6)
      .map(([key, value]) => `${key}: ${String(value)}`)
    if (bounded.length) return bounded.join('; ')
  }
  return fallback || 'Request failed'
}
