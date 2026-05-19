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

export interface SystemHealth {
  data: HealthDetails | null
  unavailable: string[]   // names of checks whose status !== "ok"
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
        .filter(([, c]) => c?.status !== 'ok')
        .map(([name]) => name)
    : []

  return {
    data: data ?? null,
    unavailable,
    isDegraded: unavailable.length > 0,
  }
}
