/**
 * useLiveExecution
 *
 * Combines:
 * 1. SWR polling (/api/v1/stream/active) for initial load and fallback
 * 2. WebSocket subscription (/ws/live/{projectId}) for real-time push updates
 *
 * The WebSocket receives events from the live consumer (run_started,
 * live_test_result, live_warning, live_run_complete) and merges them into
 * the local state so the dashboard updates without polling.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import liveStreamService from '@/services/liveStreamService'
import { useAuthStore } from '@/store/authStore'
import type { LiveSessionState } from '@/types/live-stream'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

// ── Types ──────────────────────────────────────────────────────────────────

export interface LiveEvent {
  type: string
  run_id?: string
  build_number?: string
  project_id?: string
  last_test?: string
  last_status?: string
  passed?: number
  failed?: number
  skipped?: number
  broken?: number
  total?: number
  pass_rate?: number
  status?: string
  suite_name?: string | null
  message?: string
  timestamp: number
}

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

// ── Aggregate live stats ────────────────────────────────────────────────────

export interface LiveStats {
  totalTests: number
  totalPassed: number
  totalFailed: number
  totalBroken: number
  totalSkipped: number
  overallPassRate: number
}

/**
 * Aggregate live KPIs across a set of sessions.
 *
 * Pass rate is ``passed / (passed + failed + broken)`` — the canonical
 * *evaluated* denominator with skipped excluded, matching the backend's own
 * per-session ``pass_rate`` (``stream_service.close_session``: "passed /
 * passed+failed+broken") and the ingestion aggregates. An earlier version
 * divided by ``passed + failed`` alone, which dropped BROKEN and overstated
 * the rate on any run carrying infrastructure errors (e.g. 8 passed / 2 broken
 * read as 100%, not 80%) — the same "a failure is FAILED *or* BROKEN" rule the
 * rest of the codebase pins. ``totalBroken`` is returned so broken tests are
 * not silently erased from the live breakdown either.
 */
export function computeLiveStats(
  sessions: Pick<LiveSessionState, 'total' | 'passed' | 'failed' | 'broken' | 'skipped'>[],
): LiveStats {
  const totalTests   = sessions.reduce((a, s) => a + (s.total   || 0), 0)
  const totalPassed  = sessions.reduce((a, s) => a + (s.passed  || 0), 0)
  const totalFailed  = sessions.reduce((a, s) => a + (s.failed  || 0), 0)
  const totalBroken  = sessions.reduce((a, s) => a + (s.broken  || 0), 0)
  const totalSkipped = sessions.reduce((a, s) => a + (s.skipped || 0), 0)
  const evaluated = totalPassed + totalFailed + totalBroken
  const overallPassRate = evaluated > 0
    ? Math.round((totalPassed / evaluated) * 100)
    : 0
  return { totalTests, totalPassed, totalFailed, totalBroken, totalSkipped, overallPassRate }
}

// ── Active sessions SWR hook ───────────────────────────────────────────────

export function useActiveSessions(projectId?: string, suiteName?: string | null, days?: number) {
  return useSWR(
    ['live-active', projectId, suiteName, days],
    () => liveStreamService.getActiveSessions(projectId, suiteName, days),
    { refreshInterval: REFRESH_INTERVALS.REALTIME, revalidateOnFocus: false },
  )
}

// How long a locally-completed session is bridged across the WS-push → next
// SWR poll race before the API response becomes authoritative for it. Must be
// comfortably larger than the poll interval (≤10s) and small relative to the
// shortest time window (24h) so narrowing the window still drops stale rows.
export const SESSION_RACE_GRACE_MS = 90_000

/**
 * Merge the authoritative API session list with locally-tracked sessions.
 *
 * Only *just-completed* sessions (status `completed`, `completed_at` within
 * {@link SESSION_RACE_GRACE_MS}) that the API hasn't returned yet are bridged —
 * this covers the gap between a WebSocket `live_run_complete` push and the next
 * poll. It is deliberately time-bounded: an unbounded merge re-adds every
 * completed session ever seen in the component, so narrowing the time window
 * (e.g. 30d → 24h) would never drop the now out-of-window rows and the /live
 * table would appear not to filter (regression fixed 2026-06-05).
 */
export function mergeBridgedSessions(
  prev: LiveSessionState[],
  apiSessions: LiveSessionState[],
  nowMs: number,
): LiveSessionState[] {
  const apiRunIds = new Set(apiSessions.map(s => s.run_id))
  const localOnly = prev.filter(s => {
    if (s.status !== 'completed' || apiRunIds.has(s.run_id)) return false
    const completedAt = s.completed_at ? new Date(s.completed_at).getTime() : 0
    return completedAt > 0 && nowMs - completedAt < SESSION_RACE_GRACE_MS
  })
  return [...apiSessions, ...localOnly]
}

// ── Full live execution hook (sessions + WebSocket) ────────────────────────

export function useLiveExecution(projectId?: string, suiteName?: string | null, days?: number) {
  const [sessions, setSessions] = useState<LiveSessionState[]>([])
  const [recentEvents, setRecentEvents] = useState<LiveEvent[]>([])
  const [wsStatus, setWsStatus] = useState<WsStatus>('closed')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  // Holds the latest `connect` so the reconnect timer can call it without a
  // forward self-reference (which would close over a stale `connect`).
  const connectRef = useRef<() => void>(() => {})

  const token = useAuthStore(s => s.token)
  // Hold the latest token in a ref so the WebSocket can refresh in-place when
  // the token rotates, without tearing down and reconnecting the socket.
  const tokenRef = useRef(token)
  useEffect(() => { tokenRef.current = token }, [token])

  // ── SWR polling (initial load + fallback when WS is down) ──────────────
  const { data, mutate } = useSWR(
    ['live-active', projectId, suiteName, days],
    () => liveStreamService.getActiveSessions(projectId, suiteName, days),
    {
      refreshInterval: wsStatus === 'open' ? 10_000 : 5_000,
      revalidateOnFocus: false,
      onSuccess: (d) => {
        if (!mountedRef.current) return
        // Merge: keep any locally-tracked completed sessions not yet in the API response
        // (race window between WS push and SWR re-fetch)
        setSessions(prev => mergeBridgedSessions(prev, d.sessions, Date.now()))

        // Seed the event feed from session data so the Workflow Event Feed
        // widget is populated on initial load (before any WebSocket events).
        setRecentEvents(prev => {
          if (prev.length > 0) return prev // already have real-time events
          const seeded: LiveEvent[] = d.sessions.map((s: LiveSessionState) => ({
            type: s.status === 'running' ? 'live_run_started' : 'live_run_complete',
            run_id: s.run_id,
            build_number: s.build_number,
            project_id: s.project_id,
            suite_name: s.suite_name,
            passed: s.passed,
            failed: s.failed,
            skipped: s.skipped,
            broken: s.broken,
            total: s.total,
            pass_rate: s.pass_rate,
            last_test: s.current_test,
            status: s.status,
            timestamp: s.completed_at
              ? new Date(s.completed_at).getTime()
              : s.started_at
                ? new Date(s.started_at).getTime()
                : Date.now(),
          }))
          return seeded.slice(0, 200)
        })
      },
    },
  )

  // ── WebSocket message handler ───────────────────────────────────────────
  const handleWsMessage = useCallback((msg: Record<string, unknown>) => {
    const type = msg.type as string
    if (type === 'ping') {
      wsRef.current?.send(JSON.stringify({ type: 'pong' }))
      return
    }
    // Control-plane acks from the server — not user-visible events.
    if (type === 'connected' || type === 'refreshed' || type === 'pong') {
      return
    }

    const event: LiveEvent = { ...(msg as unknown as LiveEvent), timestamp: Date.now() }

    // Append to recent events feed (keep last 200)
    setRecentEvents(prev => [event, ...prev].slice(0, 200))

    // Merge live state updates into sessions list
    if (type === 'live_run_started') {
      setSessions(prev => {
        const exists = prev.some(s => s.run_id === event.run_id)
        if (exists) return prev
        return [
          {
            run_id: event.run_id ?? '',
            project_id: event.project_id ?? '',
            build_number: event.build_number ?? '',
            status: 'running',
            total: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
            pass_rate: 0,
          },
          ...prev,
        ]
      })
    } else if (type === 'live_test_result' && event.run_id) {
      setSessions(prev =>
        prev.map(s =>
          s.run_id === event.run_id
            ? {
                ...s,
                passed:     event.passed  ?? s.passed,
                failed:     event.failed  ?? s.failed,
                skipped:    event.skipped ?? s.skipped,
                broken:     event.broken  ?? s.broken,
                total:      event.total   ?? s.total,
                pass_rate:  event.pass_rate ?? s.pass_rate,
                current_test: event.last_test ?? s.current_test,
                status: 'running',
              }
            : s,
        ),
      )
    } else if (type === 'live_run_complete' && event.run_id) {
      setSessions(prev =>
        prev.map(s =>
          s.run_id === event.run_id
            ? {
                ...s,
                status: 'completed',
                // Stamp the completion time so the SWR-merge race-window bridge
                // can time-box this row (see onSuccess). Without it the bridge
                // can't tell a just-completed run from a stale one.
                completed_at: s.completed_at ?? new Date().toISOString(),
                pass_rate: event.pass_rate ?? s.pass_rate,
              }
            : s,
        ),
      )
      // Refresh polling data so completed run fades out
      mutate()
    }
  }, [mutate])

  // ── WebSocket connection ────────────────────────────────────────────────
  // `connect` deliberately omits `token` from its deps — it reads the latest
  // token from `tokenRef`. Token rotation is handled by sending a `refresh`
  // frame on the existing socket (effect below), avoiding a full reconnect.
  const connect = useCallback(() => {
    const currentToken = tokenRef.current
    if (!projectId || !currentToken) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    // Mirror the page protocol (https → wss, http → ws) so production over HTTPS
    // never downgrades to an insecure WebSocket. Explicit env overrides still win.
    const pageWsScheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const wsBase = (import.meta.env.VITE_WS_URL as string | undefined) ||
      (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/^https?/, pageWsScheme) ||
      `${pageWsScheme}://${window.location.host}`

    const url = `${wsBase}/ws/live/${projectId}`
    const ws = new WebSocket(url)
    wsRef.current = ws
    setWsStatus('connecting')

    ws.onopen = () => {
      if (!mountedRef.current) return
      setWsStatus('open')
      // Send JWT for auth after connection — always pull the freshest token
      ws.send(JSON.stringify({ type: 'auth', token: tokenRef.current }))
    }

    ws.onmessage = (evt) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(evt.data) as Record<string, unknown>
        handleWsMessage(msg)
      } catch { /* ignore malformed frames */ }
    }

    ws.onerror = () => {
      if (mountedRef.current) setWsStatus('error')
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setWsStatus('closed')
      // Reconnect after 5s (auth token will be re-read from tokenRef on the next open).
      // Go through connectRef so we always invoke the latest `connect` and avoid
      // referencing `connect` before its own declaration completes.
      reconnectTimer.current = setTimeout(() => connectRef.current(), 5_000)
    }
  }, [projectId, handleWsMessage])

  // Keep connectRef pointed at the latest `connect` for the reconnect timer.
  useEffect(() => { connectRef.current = connect }, [connect])

  const disconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    wsRef.current?.close()
    wsRef.current = null
    setWsStatus('closed')
  }, [])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      disconnect()
    }
  }, [connect, disconnect])

  // Send a `refresh` frame to the server when the access token rotates while
  // the socket is open — keeps the long-lived connection alive across token
  // renewals (avoids the 5s reconnect gap).
  useEffect(() => {
    if (!token) return
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    try {
      ws.send(JSON.stringify({ type: 'refresh', token }))
    } catch { /* socket may have closed between the check and the send */ }
  }, [token])

  // ── Derived stats ───────────────────────────────────────────────────────
  const runningSessions = sessions.filter(s => s.status === 'running')
  const stats = computeLiveStats(runningSessions)

  return {
    sessions,
    runningSessions,
    recentEvents,
    wsStatus,
    stats,
    isLoading: !data && !sessions.length,
    mutate,
  }

}
