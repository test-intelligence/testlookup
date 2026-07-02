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
      originalRequest.url !== '/api/v1/auth/login' &&
      originalRequest.url !== '/api/v1/auth/refresh'
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
