import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  token: 'old-token' as string | null,
  refreshAccessToken: vi.fn<() => Promise<string | null>>(),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => auth },
}))
vi.mock('./api', () => ({
  backendUrl: (path: string) => `http://app.test${path}`,
}))

import { ChatStreamError, parseSseFrames, streamChatTurn } from './chatStream'
import type { ChatStreamEvent } from '@/types/chat'

/** A fetch Response whose body arrives in the given pieces. */
function streamed(pieces: string[], status = 200): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece))
      controller.close()
    },
  })
  return new Response(body, { status, headers: { 'Content-Type': 'text/event-stream' } })
}

function json(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('parseSseFrames', () => {
  it('splits complete events and keeps the incomplete tail', () => {
    const { events, rest } = parseSseFrames(
      'event: start\ndata: {"user_message_id":"q1","retry":false}\n\nevent: delta\ndata: {"text":"Hel',
    )
    expect(events).toEqual([{ event: 'start', data: { user_message_id: 'q1', retry: false } }])
    expect(rest).toBe('event: delta\ndata: {"text":"Hel')
  })

  it('skips pings and malformed frames, joins multi-line data, accepts CRLF', () => {
    const { events } = parseSseFrames(
      ': ping\n\n'
      + 'event: delta\r\ndata: {"text":\r\ndata: "a"}\r\n\r\n'
      + 'event: delta\ndata: not json\n\n',
    )
    expect(events).toEqual([{ event: 'delta', data: { text: 'a' } }])
  })
})

describe('streamChatTurn', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    auth.token = 'old-token'
    auth.refreshAccessToken.mockReset()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts the question with the bearer token and delivers events split across chunks', async () => {
    fetchMock.mockResolvedValue(streamed([
      'event: start\ndata: {"user_message_id":"q1","retry":false}\n\nevent: del',
      'ta\ndata: {"text":"Build 105 "}\n\n: ping\n\nevent: delta\ndata: {"text":"failed."}\n',
      '\n',
    ]))
    const events: ChatStreamEvent[] = []
    await streamChatTurn('s-1', { message: 'What failed?' }, {
      signal: new AbortController().signal,
      onEvent: (e) => events.push(e),
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://app.test/api/v1/chat/sessions/s-1/messages/stream')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer old-token')
    expect(JSON.parse(String(init?.body))).toEqual({ message: 'What failed?' })
    expect(events.map((e) => e.event)).toEqual(['start', 'delta', 'delta'])
    expect(events.filter((e) => e.event === 'delta').map((e) => (e.data as { text: string }).text).join(''))
      .toBe('Build 105 failed.')
  })

  it('refreshes an expired token once and retries', async () => {
    auth.refreshAccessToken.mockResolvedValue('new-token')
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: 'expired' }))
      .mockResolvedValueOnce(streamed(['event: done\ndata: {"message":{}}\n\n']))
    const events: ChatStreamEvent[] = []
    await streamChatTurn('s-1', { retry: true }, { signal: new AbortController().signal, onEvent: (e) => events.push(e) })

    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1)
    expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>).Authorization).toBe('Bearer new-token')
    expect(events.map((e) => e.event)).toEqual(['done'])
  })

  it('turns a refused turn into a typed error, keeping the server vocabulary', async () => {
    fetchMock.mockResolvedValue(json(504, { detail: { code: 'timeout', message: 'Too slow.', retryable: true } }))
    const err = await streamChatTurn('s-1', { message: 'x' }, { signal: new AbortController().signal, onEvent: () => {} })
      .catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ChatStreamError)
    expect((err as ChatStreamError).failure).toEqual({ code: 'timeout', message: 'Too slow.', retryable: true })
    expect((err as ChatStreamError).status).toBe(504)
  })

  it('says what a plain HTTP failure means', async () => {
    fetchMock.mockResolvedValue(json(404, { detail: 'Not Found' }))
    const err = (await streamChatTurn('gone', { message: 'x' }, { signal: new AbortController().signal, onEvent: () => {} })
      .catch((e: unknown) => e)) as ChatStreamError
    expect(err.failure.code).toBe('not_found')
    expect(err.failure.retryable).toBe(false)
  })
})
