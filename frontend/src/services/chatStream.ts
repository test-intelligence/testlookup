/**
 * Ask-AI chat: one streamed turn (`POST /chat/sessions/{id}/messages/stream`,
 * answered as server-sent events).
 *
 * fetch, not axios: axios in the browser cannot read a response body as it
 * arrives, and the whole point is to show the answer as it is written. Auth
 * matches the axios client in services/api.ts — the same base URL
 * (`backendUrl`), the same bearer token, and one silent refresh on a 401.
 */
import { backendUrl } from './api'
import { useAuthStore } from '@/store/authStore'
import type { ChatStreamEvent, ChatTurnFailure } from '@/types/chat'

/** A turn the server refused before streaming (or a non-2xx answer). */
export class ChatStreamError extends Error {
  readonly failure: ChatTurnFailure
  readonly status: number

  constructor(failure: ChatTurnFailure, status: number) {
    super(failure.message)
    this.failure = failure
    this.status = status
  }
}

/**
 * Split a server-sent-events buffer into complete events. The incomplete
 * tail (no blank line yet) is returned as `rest` for the next chunk.
 * Comment lines (`: ping`) and malformed frames are skipped.
 */
export function parseSseFrames(buffer: string): { events: ChatStreamEvent[]; rest: string } {
  const frames = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = frames.pop() ?? ''
  const events: ChatStreamEvent[] = []
  for (const frame of frames) {
    let event = 'message'
    const data: string[] = []
    for (const line of frame.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      let value = colon === -1 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length === 0) continue
    try {
      events.push({ event, data: JSON.parse(data.join('\n')) } as ChatStreamEvent)
    } catch {
      // A frame that is not JSON is not one of ours; ignore it.
    }
  }
  return { events, rest }
}

async function failureOf(res: Response): Promise<ChatTurnFailure> {
  const detail: unknown = await res.json().then(
    (body: { detail?: unknown } | null) => body?.detail ?? null,
    () => null,
  )
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail) {
    return detail as ChatTurnFailure
  }
  const code =
    res.status === 401 ? 'unauthorized'
      : res.status === 403 ? 'forbidden'
        : res.status === 404 ? 'not_found'
          : res.status === 422 ? 'invalid'
            : 'http_error'
  const message =
    typeof detail === 'string' ? detail
      : res.status === 401 ? 'Your session has expired. Sign in again.'
        : res.status === 403 ? 'You do not have access to this conversation.'
          : res.status === 404 ? 'This conversation no longer exists.'
            : `The chat service answered with an error (HTTP ${res.status}).`
  return { code, message, retryable: res.status >= 500 || res.status === 429 }
}

function post(sessionId: string, body: { message?: string; retry?: boolean }, signal: AbortSignal, token: string | null) {
  return fetch(backendUrl(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/stream`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
}

/**
 * Stream one turn, calling `onEvent` for each event as it arrives. Resolves
 * when the stream ends; rejects with `ChatStreamError` when the server refuses
 * the turn, and with an `AbortError` when `signal` is aborted (Stop).
 */
export async function streamChatTurn(
  sessionId: string,
  body: { message?: string; retry?: boolean },
  { signal, onEvent }: { signal: AbortSignal; onEvent: (event: ChatStreamEvent) => void },
): Promise<void> {
  let res = await post(sessionId, body, signal, useAuthStore.getState().token)
  if (res.status === 401) {
    const fresh = await useAuthStore.getState().refreshAccessToken().catch(() => null)
    if (fresh) res = await post(sessionId, body, signal, fresh)
  }
  if (!res.ok || !res.body) throw new ChatStreamError(await failureOf(res), res.status)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = parseSseFrames(buffer)
    buffer = rest
    events.forEach(onEvent)
  }
  buffer += decoder.decode()
  if (buffer.trim()) parseSseFrames(`${buffer}\n\n`).events.forEach(onEvent)
}
