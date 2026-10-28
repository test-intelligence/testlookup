/**
 * The /activity live tail (ACT-02): one streamed GET of the project's activity
 * stream (server-sent events), reconnecting with `Last-Event-ID`.
 *
 * fetch + ReadableStream, not EventSource: an EventSource cannot send an
 * Authorization header, and a token in a URL lands in proxy and access logs.
 * Auth matches services/chatStream.ts — the same base URL (`backendUrl`), the
 * bearer token, one silent refresh on a 401. The frame parser is this file's
 * own: chatStream's drops the `id:` field, and the id is the resume cursor.
 *
 * Statuses (`onStatus`): `connecting` → `live` on the server's `ready` frame;
 * `reconnecting` after a network failure; `unavailable` on a 503 or a
 * `bye unavailable` (the backoff keeps trying); `refused` on a 401 the
 * refresh did not cure, a 403 or a 429 — no retry until the stream is opened
 * again. A stream that was live and ENDED (the server's 15-minute `bye`, or a
 * closed body) stays `live` through the wait: the reconnect sends
 * `Last-Event-ID`, so the rows of the gap are replayed, not lost.
 */
import { backendUrl } from './api'
import { useAuthStore } from '@/store/authStore'
import type { ActivityEvent } from '@/services/activityService'
import {
  HEARTBEAT_SECONDS,
  RETRY_MILLISECONDS,
  SSE_EVENTS,
  STREAM_ERROR_SENTENCES,
  type LiveStatus,
} from '@/types/activityLive'

/** The longest wait between two reconnects (§4.2: 5 s doubling to 60 s). */
export const MAX_RETRY_MILLISECONDS = 60_000
/** A stream that lived at least this long resets the backoff to its first step. */
export const HEALTHY_STREAM_MILLISECONDS = 60_000
/** No byte for two heartbeats and a margin: the connection is dead, reconnect. */
export const IDLE_MILLISECONDS = (HEARTBEAT_SECONDS * 2 + 5) * 1000
/** A 401 the one silent refresh did not cure. */
export const SESSION_EXPIRED_SENTENCE = 'Your session has expired. Sign in again to turn Live on.'

export interface ActivityFrame {
  event: string
  /** The frame's `id:` (a Redis stream id), or null when it carried none. */
  id: string | null
  data: unknown
}

const KNOWN_EVENTS: ReadonlySet<string> = new Set<string>(SSE_EVENTS)

/**
 * Split a server-sent-events buffer into complete frames; the incomplete tail
 * (no blank line yet) comes back as `rest` for the next chunk. Comment lines
 * (`: heartbeat`), the `retry:` line, frames without data, events this client
 * does not know and a `data` that is not JSON are all dropped.
 */
export function parseActivityFrames(buffer: string): { frames: ActivityFrame[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  const frames: ActivityFrame[] = []
  for (const block of blocks) {
    let event = 'message'
    let id: string | null = null
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      let value = colon === -1 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'id') id = value
      else if (field === 'data') data.push(value)
    }
    if (data.length === 0 || !KNOWN_EVENTS.has(event)) continue
    try {
      frames.push({ event, id, data: JSON.parse(data.join('\n')) })
    } catch {
      // Not JSON: not one of ours.
    }
  }
  return { frames, rest }
}

function isActivityEvent(data: unknown): data is ActivityEvent {
  if (!data || typeof data !== 'object') return false
  const row = data as Partial<ActivityEvent>
  return typeof row.id === 'string' && typeof row.summary === 'string'
    && typeof row.actor === 'object' && row.actor !== null
    && typeof row.entity === 'object' && row.entity !== null
}

function reasonOf(data: unknown): string {
  const reason = data && typeof data === 'object' ? (data as { reason?: unknown }).reason : undefined
  return typeof reason === 'string' ? reason : ''
}

/** The sentence a refusal shows: the server's own when it sent one. */
async function refusalOf(res: Response): Promise<string> {
  const detail: unknown = await res.json().then(
    (body: { detail?: unknown } | null) => body?.detail ?? null,
    () => null,
  )
  if (res.status === 401) return SESSION_EXPIRED_SENTENCE
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  if (res.status === 429) return STREAM_ERROR_SENTENCES.too_many_streams
  if (res.status === 403) return 'You do not have access to this project.'
  return STREAM_ERROR_SENTENCES.live_unavailable
}

export interface ActivityStreamHandlers {
  /** Resume after this stream id (sent as the `Last-Event-ID` header). */
  lastEventId?: string | null
  /** The server's `ready`; `resumed` = this connection sent a `Last-Event-ID`. */
  onReady?: (ready: { lastEventId: string | null; resumed: boolean }) => void
  /** One committed row, byte-for-byte a feed item. */
  onActivity: (item: ActivityEvent, id: string | null) => void
  /** The server asks for a re-read of the feed's first page (gap / overflow / unavailable). */
  onRefresh?: (reason: string, id: string | null) => void
  onStatus: (status: LiveStatus, message?: string) => void
}

/**
 * Open the project's live tail. `stop()` aborts the request and cancels any
 * pending reconnect; no handler is called after it.
 */
export function openActivityStream(projectId: string, handlers: ActivityStreamHandlers): { stop(): void } {
  let stopped = false
  let lastEventId: string | null = handlers.lastEventId ?? null
  let delay = RETRY_MILLISECONDS
  let controller: AbortController | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let idleTimer: ReturnType<typeof setTimeout> | null = null

  const report = (status: LiveStatus, message?: string) => {
    if (!stopped) handlers.onStatus(status, message)
  }
  const clearIdle = () => {
    if (idleTimer !== null) clearTimeout(idleTimer)
    idleTimer = null
  }
  // A proxy that drops the connection silently leaves the read pending
  // forever; without this the page would say Live over a dead tail.
  const armIdle = (ctl: AbortController) => {
    clearIdle()
    idleTimer = setTimeout(() => ctl.abort(), IDLE_MILLISECONDS)
  }
  /** Wait the backoff, then connect again (no `status`: keep the current one). */
  const retry = (status?: LiveStatus, message?: string) => {
    if (stopped) return
    if (status) report(status, message)
    const wait = delay
    delay = Math.min(delay * 2, MAX_RETRY_MILLISECONDS)
    retryTimer = setTimeout(() => {
      retryTimer = null
      void connect()
    }, wait)
  }

  const request = (token: string | null, signal: AbortSignal) =>
    fetch(backendUrl(`/api/v1/projects/${encodeURIComponent(projectId)}/activity/stream`), {
      method: 'GET',
      cache: 'no-store',
      headers: {
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
      },
      signal,
    })

  async function connect(): Promise<void> {
    if (stopped) return
    const ctl = new AbortController()
    controller = ctl
    const resumed = lastEventId !== null
    let res: Response
    try {
      res = await request(useAuthStore.getState().token, ctl.signal)
      if (res.status === 401) {
        // One silent refresh, then one retry (the chatStream rule); a second 401 is a refusal, not a loop.
        const fresh = await useAuthStore.getState().refreshAccessToken().catch(() => null)
        if (fresh) res = await request(fresh, ctl.signal)
      }
    } catch {
      return retry('reconnecting')
    }
    if (stopped) return
    if (res.status >= 500) return retry('unavailable', await refusalOf(res))
    if (!res.ok) return report('refused', await refusalOf(res))
    if (!res.body) return retry('unavailable', STREAM_ERROR_SENTENCES.live_unavailable)

    const startedAt = Date.now()
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let ready = false
    let bye: string | null = null
    const take = (text: string) => {
      const parsed = parseActivityFrames(text)
      buffer = parsed.rest
      for (const frame of parsed.frames) {
        if (stopped || bye !== null) return
        if (frame.id) lastEventId = frame.id
        if (frame.event === 'ready') {
          ready = true
          report('live')
          handlers.onReady?.({ lastEventId, resumed })
        } else if (frame.event === 'activity') {
          if (isActivityEvent(frame.data)) handlers.onActivity(frame.data, frame.id)
        } else if (frame.event === 'refresh') {
          handlers.onRefresh?.(reasonOf(frame.data), frame.id)
        } else if (frame.event === 'bye') {
          bye = reasonOf(frame.data) || 'closed'
        }
      }
    }
    let broken = false
    try {
      armIdle(ctl)
      while (bye === null && !stopped) {
        const { value, done } = await reader.read()
        if (done) break
        armIdle(ctl)
        take(buffer + decoder.decode(value, { stream: true }))
      }
      if (bye === null && !stopped) take(`${buffer}${decoder.decode()}\n\n`)
    } catch {
      broken = true
    } finally {
      clearIdle()
    }
    if (stopped) return
    if (bye !== null) ctl.abort()
    if (Date.now() - startedAt >= HEALTHY_STREAM_MILLISECONDS) delay = RETRY_MILLISECONDS
    if (bye === 'unavailable') return retry('unavailable', STREAM_ERROR_SENTENCES.live_unavailable)
    if (broken || !ready) return retry('reconnecting')
    // A live stream that ended (the server's max-age bye, a closed body):
    // still live while it waits; the reconnect resumes from Last-Event-ID.
    return retry()
  }

  report('connecting')
  void connect()
  return {
    stop() {
      if (stopped) return
      stopped = true
      if (retryTimer !== null) clearTimeout(retryTimer)
      retryTimer = null
      clearIdle()
      controller?.abort()
    },
  }
}
