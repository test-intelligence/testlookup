import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import toast from 'react-hot-toast'
import { useAuthStore } from '../store/authStore'
import { extractErrorMessage, shouldToastError } from './apiErrors'

// When VITE_API_BASE_URL is unset, use same-origin relative URLs. This makes
// the production bundle deploy-target agnostic — it works behind any ingress
// (k8s/homelab/gcp/aws) over both http and https without mixed-content or CORS
// issues. In Vite dev mode, vite.config.ts proxies /api to localhost:8000.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

// Resolve the backend origin once so the request interceptor can cheaply
// check whether an outbound call is same-origin before attaching the JWT.
// Empty BASE_URL → same-origin as the page.
const BACKEND_ORIGIN: string = (() => {
  if (!BASE_URL) return window.location.origin
  try { return new URL(BASE_URL, window.location.origin).origin } catch { return window.location.origin }
})()

/**
 * Absolute URL for a backend path, resolved exactly the way the axios client
 * resolves its requests: `VITE_API_BASE_URL` when set, otherwise same-origin as
 * the page. Copy-paste command snippets (e.g. the first-run guide's ingest
 * `curl`) use this so they target the same backend the running UI already
 * reaches — not a hardcoded `localhost:8000` that only works in local dev and
 * is unreachable on any self-host served behind an ingress.
 */
export function backendUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  try {
    return new URL(`${BASE_URL}${p}`, window.location.origin).href
  } catch {
    return `${window.location.origin}${p}`
  }
}

function isSameOriginRequest(config: InternalAxiosRequestConfig): boolean {
  const url = config.url ?? ''
  // Relative path → always same-origin with BASE_URL
  if (!/^https?:\/\//i.test(url)) return true
  try {
    return new URL(url).origin === BACKEND_ORIGIN
  } catch {
    return false
  }
}

export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 120_000,
  headers: { 'Content-Type': 'application/json' },
  // Serialize array params as `?a=1&a=2`, NOT axios's default `?a[]=1&a[]=2`.
  //
  // Every endpoint in this app is FastAPI, and FastAPI reads a
  // `list[str] = Query(None)` from REPEATED bare keys. It does not recognise
  // the bracketed form, so it silently drops the parameter — no error, no 422,
  // the filter simply does nothing. Reported on /activity: choosing a category
  // changed neither the URL's effect nor the rows.
  //
  // Fixed on the shared instance rather than at one call site because the
  // default is wrong for every endpoint here, and the failure is invisible:
  // the next person to pass an array would hit exactly the same silence.
  // `searchService` already dodges it by joining to a comma string, which is
  // why nothing else has surfaced it.
  paramsSerializer: { indexes: null },
})

// Request interceptor: attach the access token only for requests bound for
// the TestLookup backend. Any call to a foreign origin (e.g. a misconfigured
// integration URL) must NOT receive the Authorization header — that would
// leak the JWT to third parties.
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token && isSameOriginRequest(config)) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── 401-refresh exclusions ───────────────────────────────────────────────────
// A 401 normally means "the access token expired" — refresh once and retry.
// On the authentication endpoints a 401 means something completely different:
// bad credentials, a wrong TOTP code, or an expired/already-spent MFA
// challenge. Those must be handed straight back to the caller.
//
// The MFA endpoints are the sharp edge. `/auth/mfa/verify` answers a wrong or
// stale code with 401, and it is called from the LOGIN screen where there is
// no valid session at all — so the refresh would fail, `refreshAccessToken`
// would call `logout()`, and a simple typo would look like a broken feature.
// The whole `/auth/mfa/` prefix is excluded rather than just `verify`:
// `enroll/start` and `enroll/confirm` are equally reachable pre-session (they
// authenticate with an `enrollment_token`, not a Bearer).
//
// Trade-off worth stating: `/auth/mfa/status` is a plain authenticated read,
// so excluding it means a genuinely expired session there surfaces as an SWR
// error instead of silently refreshing. Every other read on the page still
// drives the refresh, so the session recovers anyway — and keeping the rule a
// single unambiguous prefix is worth more than that edge.
const NO_REFRESH_PATHS = new Set([
  '/api/v1/auth/login',
  '/api/v1/auth/refresh',
  '/api/v1/auth/register',
  '/api/v1/auth/dev-login',
  '/api/v1/auth/logout',
])

const NO_REFRESH_PREFIXES = ['/api/v1/auth/mfa/']

/**
 * Whether a 401 from `url` should trigger the silent refresh-and-retry.
 * Exported so the policy is unit-testable without driving axios.
 */
export function shouldAttemptTokenRefresh(url: string | undefined): boolean {
  const raw = url ?? ''
  let path = raw
  if (/^https?:\/\//i.test(raw)) {
    try {
      path = new URL(raw).pathname
    } catch {
      path = raw
    }
  }
  path = path.split('?')[0].split('#')[0]
  if (NO_REFRESH_PATHS.has(path)) return false
  return !NO_REFRESH_PREFIXES.some((prefix) => path.startsWith(prefix))
}

// Track whether a refresh is already in flight to prevent parallel refresh calls
let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (err: unknown) => void
}> = []

function processQueue(error: unknown, token: string | null) {
  failedQueue.forEach((p) => (token ? p.resolve(token) : p.reject(error)))
  failedQueue = []
}

// Response interceptor: on 401 try to refresh once, then retry original request
api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError<{ detail?: unknown }>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      shouldAttemptTokenRefresh(originalRequest.url)
    ) {
      if (isRefreshing) {
        // Queue additional requests until the in-flight refresh resolves
        return new Promise((resolve, reject) => {
          failedQueue.push({
            resolve: (token) => {
              originalRequest.headers.Authorization = `Bearer ${token}`
              resolve(api(originalRequest))
            },
            reject,
          })
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        const newToken = await useAuthStore.getState().refreshAccessToken()
        if (!newToken) {
          processQueue(error, null)
          return Promise.reject(error)
        }
        processQueue(null, newToken)
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return api(originalRequest)
      } catch (refreshError) {
        processQueue(refreshError, null)
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    // For non-401 errors or exhausted retries, surface a toast. 422 is now
    // surfaced (was silently swallowed — the "empty page, no error" footgun);
    // 401/404 stay quiet. See services/apiErrors.
    if (shouldToastError(error.response?.status)) {
      toast.error(extractErrorMessage(error.response?.data?.detail, error.message))
    }
    return Promise.reject(error)
  }
)
