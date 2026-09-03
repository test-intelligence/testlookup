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

// Self-reported build provenance for the running image, injected at
// image-build time (BUILD_REVISION / BUILD_DATE → Dockerfile ARGs). Present on
// every /health/details and /health/version response; both fields fall back to
// the literal "unknown" in local/dev runs where the build env is unset, so a
// consumer must treat "unknown" as "no provenance" rather than a real value.
export interface BuildProvenance {
  revision: string
  built_at: string
}

export interface HealthDetails {
  status: 'healthy' | 'degraded'
  version: string
  build?: BuildProvenance
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

async function fetchSystemHealth(): Promise<HealthDetails> {
  // This used to swallow the failure and return null, so `checks` was absent,
  // `unavailable` was [] and `isDegraded` was false — the degraded banner went
  // SILENT in the one situation it exists for: the backend itself unreachable,
  // which is strictly worse than any single dependency being down. Silence and
  // "everything is fine" looked identical. Let it throw; SWR records the error
  // and `isUnreachable` below turns it into a banner that says so.
  const { data } = await axios.get<HealthDetails>(`${API_BASE}/health/details`, {
    timeout: 8000,
  })
  return data
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

// The stores whose outage loses the DATA, not merely a feature: the three
// dependencies the backend wraps in `_critical(...)` in
// `backend/app/routers/health.py` (postgres / mongo / redis — the ones
// `/health/ready` fails closed on). An optional dependency degrading (MinIO,
// Ollama, ChromaDB) costs a capability; one of these being unreachable costs
// the pages themselves. The degraded banner keys off this to stop offering its
// "we'll fall back to PostgreSQL where possible" reassurance during a critical
// outage — a promise that is misleading in general and self-contradictory when
// PostgreSQL is the store that is down.
//
// Keyed on the dependency NAME, not the `error` status, on purpose: a probe
// can report `error` for a non-critical service too (an optional check that
// raises), and criticality is a property of which store it is, not of how it
// failed. Pinned name-for-name against health.py by
// `backend/tests/regression/test_health_critical_deps_are_shared.py`, so a new
// critical probe on the backend forces this set to be updated rather than the
// outage silently rendering as an optional-only degradation.
const CRITICAL_DEPS: ReadonlySet<string> = new Set(['postgres', 'mongo', 'redis'])

export interface SystemHealth {
  data: HealthDetails | null
  unavailable: string[]   // names of checks reporting a genuine problem
  /** Names of unavailable checks that are CRITICAL stores (a subset of `unavailable`). */
  criticalUnavailable: string[]
  isDegraded: boolean
  /** The health poll itself failed — we know nothing, rather than nothing being wrong. */
  isUnreachable: boolean
}

export function useSystemHealth(): SystemHealth {
  const { data, error } = useSWR<HealthDetails>('system-health', fetchSystemHealth, {
    refreshInterval: REFRESH_INTERVAL_MS,
    revalidateOnFocus: false,
    revalidateOnReconnect: true,
    // No retry storm on error: `refreshInterval` still re-polls every 60s, so
    // the banner clears itself one poll after the backend comes back.
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
    criticalUnavailable: unavailable.filter((name) => CRITICAL_DEPS.has(name)),
    isDegraded: unavailable.length > 0,
    isUnreachable: Boolean(error),
  }
}
