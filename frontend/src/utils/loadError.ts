/**
 * Turning a failed page-data fetch into honest user-facing copy.
 *
 * The rule this file exists to enforce, borrowed from {@link
 * ./mfaErrors.ts}: never let an infrastructure failure read as a fact about
 * the user's data. "No test runs yet" and "we could not reach the backend"
 * are different sentences, and a page that prints the first when the second
 * is true sends people to re-ingest data that was never missing.
 *
 * Every one of these hooks is a plain SWR fetch (see `useProjectScopedSWR`),
 * so `error` is already populated on failure — the pages simply never read
 * it. This module gives them one vocabulary to read it with.
 */

/** What kind of failure this was, for callers that need to branch on it. */
export type LoadErrorKind =
  | 'offline' // no HTTP response at all — backend down, DNS, timeout, CORS
  | 'unauthorized' // 401/403 — session expired or no access to this project
  | 'not_found' // 404 — the project or resource is gone
  | 'rate_limited' // 429 — too many requests, backing off will clear it
  | 'server' // 5xx — the backend answered, and it answered badly
  | 'unknown'

export interface LoadErrorInfo {
  kind: LoadErrorKind
  status?: number
  /** Short headline, e.g. "Can't reach the server". */
  title: string
  /** Ready-to-render sentence explaining what is and isn't known. */
  message: string
  /** False when retrying cannot plausibly help (401/403/404). */
  retryable: boolean
}

interface HttpErrorLike {
  response?: { status?: number; data?: { detail?: unknown } }
  code?: string
  message?: string
}

function detailOf(err: unknown): string {
  const detail = (err as HttpErrorLike)?.response?.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail.trim() : ''
}

/**
 * An axios failure with no `response` never reached the backend. That is the
 * case the empty states were mis-reporting, so it gets the most explicit copy:
 * the point is to say we could not look, not to guess what we would have seen.
 */
export function describeLoadError(err: unknown): LoadErrorInfo {
  const status = (err as HttpErrorLike)?.response?.status
  const detail = detailOf(err)

  if (status === undefined) {
    return {
      kind: 'offline',
      title: "Can't reach the server",
      message:
        'This page could not load its data, so nothing below is a statement ' +
        'about your test results. Check that the backend is running, then retry.',
      retryable: true,
    }
  }

  if (status === 401 || status === 403) {
    return {
      kind: 'unauthorized',
      status,
      title: 'Not authorized to view this',
      message:
        detail ||
        'Your session may have expired, or your account has no access to this ' +
          'project. Sign in again, or ask an admin for access.',
      retryable: false,
    }
  }

  if (status === 404) {
    return {
      kind: 'not_found',
      status,
      title: 'Not found',
      message: detail || 'This project or resource no longer exists.',
      retryable: false,
    }
  }

  if (status === 429) {
    // A rate limit is the one 4xx that is neither the user's fault nor a claim
    // about their data: the backend throttles auth, MFA and ingest (see
    // main.rate_limit_auth and ingestion_rate_limit), and a page-data fetch can
    // trip the same limiter under load. The generic 4xx copy ("nothing below
    // reflects your data") reads as a data error and hides the one fact that
    // matters — waiting clears it — so give it honest, retryable copy. The
    // backend's detail carries the Retry-After context when it sent one.
    return {
      kind: 'rate_limited',
      status,
      title: 'Too many requests',
      message:
        detail ||
        'You are being rate limited, so this page held off rather than adding ' +
          'to the load. Wait a moment, then retry — your data is unaffected.',
      retryable: true,
    }
  }

  if (status >= 500) {
    return {
      kind: 'server',
      status,
      title: 'The server failed to answer',
      message:
        detail ||
        `The backend returned ${status}. This is a server-side failure, not a ` +
          'gap in your data — retrying may work, and the backend logs will say why.',
      retryable: true,
    }
  }

  return {
    kind: 'unknown',
    status,
    title: 'Could not load this page',
    message:
      detail || `The request failed with HTTP ${status}. Nothing below reflects your data.`,
    retryable: true,
  }
}
