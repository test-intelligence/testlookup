import useSWR from 'swr'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export type CheckStatus = 'ok' | 'error' | 'timeout' | 'unavailable' | string

export interface HealthCheck {
  status: CheckStatus
  // Additional fields may be present per check (latency_ms, models, etc.)
  // and are passed through as-is; we only branch on ``status``.
  [key: string]: unknown
}

export interface HealthDetails {
  status: 'healthy' | 'degraded'
  version: string
  env: string
  uptime_seconds: number
  timestamp: string
  checks: Record<string, HealthCheck>
}

// /health/details lives at the platform root (no /api/v1 prefix). It runs
// every probe concurrently with a per-probe budget so this poll is cheap.
// 60s is generous: a transient outage longer than that is real news the
// banner should surface; shorter blips don't deserve a UI flash.
const REFRESH_INTERVAL_MS = 60_000

async function fetchSystemHealth(): Promise<HealthDetails | null> {
  try {
    const { data } = await axios.get<HealthDetails>(`${API_BASE}/health/details`, {
      timeout: 8000,
    })
    return data
  } catch {
    // Don't blow up the UI if the health endpoint itself is unreachable —
    // the banner just won't render. The rest of the SPA continues to use
    // its own per-endpoint error handling.
    return null
  }
}

// Statuses the backend emits that do NOT mean something is wrong.
//
// `skipped` is a deliberate non-check, not a failure: the Ollama probe reports
// it when AI_OFFLINE_MODE=false, because a cloud-LLM deployment has no local
// Ollama worth probing. Treating anything !== 'ok' as broken made a healthy
// OpenRouter deployment show "Degraded: ollama unreachable" permanently
// (homelab, 2026-08-16) — the backend's own top-level status said "healthy"
// at the same moment.
//
// Unknown statuses still count as unavailable, deliberately: a false alarm is
// safer than silence for a health banner. `backend/tests/regression/
// test_health_status_vocabulary_is_shared.py` pins this set against the
// statuses the backend actually emits, so a new one forces a decision here
// rather than silently landing in either bucket.
const BENIGN_STATUSES: ReadonlySet<string> = new Set(['ok', 'skipped'])

export interface SystemHealth {
  data: HealthDetails | null
  unavailable: string[]   // names of checks reporting a genuine problem
  isDegraded: boolean
}

export function useSystemHealth(): SystemHealth {
  const { data } = useSWR<HealthDetails | null>('system-health', fetchSystemHealth, {
    refreshInterval: REFRESH_INTERVAL_MS,
    revalidateOnFocus: false,
    revalidateOnReconnect: true,
    shouldRetryOnError: false,
  })

  const unavailable = data?.checks
    ? Object.entries(data.checks)
        .filter(([, c]) => !BENIGN_STATUSES.has(c?.status))
        .map(([name]) => name)
    : []

  return {
    data: data ?? null,
    unavailable,
    isDegraded: unavailable.length > 0,
  }
}
