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
  message?: string
  timestamp: number
}

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

// ── Active sessions SWR hook ───────────────────────────────────────────────

export function useActiveSessions(projectId?: string) {
  return useSWR(
    ['live-active', projectId],
    () => liveStreamService.getActiveSessions(projectId),
    { refreshInterval: 5_000, revalidateOnFocus: false },
  )
}

// ── Full live execution hook (sessions + WebSocket) ────────────────────────

export function useLiveExecution(projectId?: string) {
  const [sessions, setSessions] = useState<LiveSessionState[]>([])
  const [recentEvents, setRecentEvents] = useState<LiveEvent[]>([])
  const [wsStatus, setWsStatus] = useState<WsStatus>('closed')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  const token = useAuthStore(s => s.token)

  // ── SWR polling (initial load + fallback when WS is down) ──────────────
  const { data, mutate } = useSWR(
    ['live-active', projectId],
    () => liveStreamService.getActiveSessions(projectId),
    {
      refreshInterval: wsStatus === 'open' ? 10_000 : 5_000,
      revalidateOnFocus: false,
      onSuccess: (d) => {
        if (!mountedRef.current) return
        // Merge: keep any locally-tracked completed sessions not yet in the API response
        // (race window between WS push and SWR re-fetch)
        setSessions(prev => {
          const apiRunIds = new Set(d.sessions.map((s: LiveSessionState) => s.run_id))
          const localOnly = prev.filter(
            s => s.status === 'completed' && !apiRunIds.has(s.run_id),
          )
          return [...d.sessions, ...localOnly]
        })

        // Seed the event feed from session data so the Workflow Event Feed
        // widget is populated on initial load (before any WebSocket events).
        setRecentEvents(prev => {
          if (prev.length > 0) return prev // already have real-time events
          const seeded: LiveEvent[] = d.sessions.map((s: LiveSessionState) => ({
            type: s.status === 'running' ? 'live_run_started' : 'live_run_complete',
            run_id: s.run_id,
            build_number: s.build_number,
            project_id: s.project_id,
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
            ? { ...s, status: 'completed', pass_rate: event.pass_rate ?? s.pass_rate }
            : s,
        ),
      )
      // Refresh polling data so completed run fades out
      mutate()
    }
  }, [mutate])

  // ── WebSocket connection ────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!projectId || !token) return
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
      // Send JWT for auth after connection
      ws.send(JSON.stringify({ type: 'auth', token }))
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
      // Reconnect after 5s
      reconnectTimer.current = setTimeout(connect, 5_000)
    }
  }, [projectId, token, handleWsMessage])

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

  // ── Derived stats ───────────────────────────────────────────────────────
  const runningSessions = sessions.filter(s => s.status === 'running')
  const totalTests   = runningSessions.reduce((a, s) => a + (s.total  || 0), 0)
  const totalPassed  = runningSessions.reduce((a, s) => a + (s.passed || 0), 0)
  const totalFailed  = runningSessions.reduce((a, s) => a + (s.failed || 0), 0)
  const totalSkipped = runningSessions.reduce((a, s) => a + (s.skipped || 0), 0)
  const overallPassRate = totalTests > 0
    ? Math.round(((totalPassed) / (totalPassed + totalFailed) || 0) * 100)
    : 0

  return {
    sessions,
    runningSessions,
    recentEvents,
    wsStatus,
    stats: { totalTests, totalPassed, totalFailed, totalSkipped, overallPassRate },
    isLoading: !data && !sessions.length,
    mutate,
  }

}
