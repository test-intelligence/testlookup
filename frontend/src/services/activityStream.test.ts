/**
 * W21d ACT-02: the live tail's stream client. fetch is mocked with the real
 * Response / ReadableStream; timers are fake so a backoff is a number, not a
 * wait. Every test stops its stream (afterEach), so no timer outlives it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  token: 'live-token' as string | null,
  refreshAccessToken: vi.fn<() => Promise<string | null>>(),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => auth },
}))
vi.mock('./api', () => ({
  backendUrl: (path: string) => `http://app.test${path}`,
}))

import {
  HEALTHY_STREAM_MILLISECONDS,
  IDLE_MILLISECONDS,
  MAX_RETRY_MILLISECONDS,
  SESSION_EXPIRED_SENTENCE,
  openActivityStream,
  parseActivityFrames,
} from './activityStream'
import { encodeSseFrame, makeActivityEvent, mockStreamResponse, type SseFrame } from '@/test/activityLiveMocks'
import { RETRY_MILLISECONDS, STREAM_ERROR_SENTENCES } from '@/types/activityLive'

const PROJECT = '00000000-0000-4000-8000-0000000000aa'
const STREAM_URL = `http://app.test/api/v1/projects/${PROJECT}/activity/stream`

const lastOf = <T,>(items: T[]): T | undefined => items[items.length - 1]

function readyFrame(id = '1730000000000-0'): SseFrame {
  return { event: 'ready', id, data: { project_id: PROJECT, last_event_id: id, heartbeat_seconds: 15, max_seconds: 900 } }
}

describe('parseActivityFrames', () => {
  it('keeps the id of each frame and returns the incomplete tail', () => {
    const row = makeActivityEvent()
    const { frames, rest } = parseActivityFrames(
      `${encodeSseFrame({ event: 'activity', id: '1730000000001-0', data: row })}event: refresh\nid: 17300`,
    )
    expect(frames).toEqual([{ event: 'activity', id: '1730000000001-0', data: row }])
    expect(rest).toBe('event: refresh\nid: 17300')
  })

  it('drops heartbeats, the retry line, unknown events and non-JSON data; accepts CRLF', () => {
    const { frames } = parseActivityFrames(
      'retry: 5000\n\n'
      + ': heartbeat\n\n'
      + 'event: message\ndata: {"a":1}\n\n'
      + 'event: activity\nid: 1-0\ndata: not json\n\n'
      + 'event: refresh\r\nid: 2-0\r\ndata: {"reason":\r\ndata: "gap"}\r\n\r\n',
    )
    expect(frames).toEqual([{ event: 'refresh', id: '2-0', data: { reason: 'gap' } }])
  })
})

describe('openActivityStream', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const stops: Array<() => void> = []

  beforeEach(() => {
    vi.useFakeTimers()
    auth.token = 'live-token'
    auth.refreshAccessToken.mockReset()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    stops.splice(0).forEach((stop) => stop())
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  /** Let the fetch, the body reads and the JSON parses run. */
  async function settle() {
    for (let i = 0; i < 20; i += 1) await vi.advanceTimersByTimeAsync(0)
  }

  function open() {
    const seen = {
      statuses: [] as Array<[string, string | undefined]>,
      rows: [] as Array<[string, string | null]>,
      refreshes: [] as Array<[string, string | null]>,
      ready: [] as Array<{ lastEventId: string | null; resumed: boolean }>,
    }
    const stream = openActivityStream(PROJECT, {
      onStatus: (status, message) => seen.statuses.push([status, message]),
      onActivity: (row, id) => seen.rows.push([row.id, id]),
      onRefresh: (reason, id) => seen.refreshes.push([reason, id]),
      onReady: (ready) => seen.ready.push(ready),
    })
    stops.push(stream.stop)
    return { stream, seen }
  }

  const headersOf = (call: number) => new Headers(fetchMock.mock.calls[call]?.[1]?.headers)

  /** A 200 body the test feeds frame by frame; it errors when the request is aborted. */
  function heldBody() {
    const encoder = new TextEncoder()
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined
    const impl = (_url: string | URL | Request, init?: RequestInit) => {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          controller = c
          init?.signal?.addEventListener('abort', () => c.error(new DOMException('Aborted', 'AbortError')))
        },
      })
      return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    }
    return {
      impl,
      send: (frame: SseFrame) => controller?.enqueue(encoder.encode(encodeSseFrame(frame))),
      close: () => controller?.close(),
    }
  }

  it('opens ONE GET with the bearer header (no token in the URL) and delivers frames split across chunks', async () => {
    const row = makeActivityEvent()
    fetchMock.mockResolvedValueOnce(mockStreamResponse({
      chunkSize: 7,
      frames: [
        { retry: 5000 },
        readyFrame('1730000000000-0'),
        { comment: 'heartbeat' },
        { event: 'activity', id: '1730000000001-0', data: row },
        { event: 'refresh', id: '1730000000002-0', data: { reason: 'gap' } },
      ],
    }))
    const { seen } = open()
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(STREAM_URL)
    expect(init?.method).toBe('GET')
    expect(headersOf(0).get('Authorization')).toBe('Bearer live-token')
    expect(headersOf(0).get('Accept')).toBe('text/event-stream')
    expect(headersOf(0).has('Last-Event-ID')).toBe(false)
    expect(seen.ready).toEqual([{ lastEventId: '1730000000000-0', resumed: false }])
    expect(seen.rows).toEqual([[row.id, '1730000000001-0']])
    expect(seen.refreshes).toEqual([['gap', '1730000000002-0']])
    // The body ended after a ready: still live while it waits to resume.
    expect(seen.statuses).toEqual([['connecting', undefined], ['live', undefined]])
  })

  it('reconnects after RETRY_MILLISECONDS with Last-Event-ID = the last frame id', async () => {
    const row = makeActivityEvent()
    fetchMock
      .mockResolvedValueOnce(mockStreamResponse({
        frames: [readyFrame('1730000000000-0'), { event: 'activity', id: '1730000000007-0', data: row }],
      }))
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame('1730000000007-0')] }))
    const { seen } = open()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS - 1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1][0]).toBe(STREAM_URL)
    expect(headersOf(1).get('Last-Event-ID')).toBe('1730000000007-0')
    expect(seen.ready[1]).toEqual({ lastEventId: '1730000000007-0', resumed: true })
  })

  it('a 401 refreshes the token ONCE and retries with the fresh one', async () => {
    fetchMock
      .mockResolvedValueOnce(mockStreamResponse({ status: 401, detail: 'Could not validate credentials' }))
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    auth.refreshAccessToken.mockResolvedValueOnce('fresh-token')
    const { seen } = open()
    await settle()

    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(headersOf(1).get('Authorization')).toBe('Bearer fresh-token')
    expect(lastOf(seen.statuses)).toEqual(['live', undefined])
  })

  it('a 401 the refresh does not cure is REFUSED, with no retry loop', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(
      mockStreamResponse({ status: 401, detail: 'Could not validate credentials' }),
    ))
    auth.refreshAccessToken.mockResolvedValue('fresh-token')
    const { seen } = open()
    await settle()
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MILLISECONDS)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1)
    expect(lastOf(seen.statuses)).toEqual(['refused', SESSION_EXPIRED_SENTENCE])
  })

  it.each([
    [403, { detail: 'You do not have access to this project' }, 'You do not have access to this project'],
    [429, { code: 'too_many_streams' as const }, STREAM_ERROR_SENTENCES.too_many_streams],
    [403, { code: 'interactive_login_required' as const }, STREAM_ERROR_SENTENCES.interactive_login_required],
  ])('a %i is REFUSED with the server sentence and never retried', async (status, body, sentence) => {
    fetchMock.mockImplementation(() => Promise.resolve(mockStreamResponse({ status, ...body })))
    const { seen } = open()
    await settle()
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MILLISECONDS)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(seen.statuses).toEqual([['connecting', undefined], ['refused', sentence]])
  })

  it('a 503 is UNAVAILABLE and retries on the backoff: 5 s, then 10 s, then live', async () => {
    fetchMock
      .mockResolvedValueOnce(mockStreamResponse({ status: 503, code: 'live_unavailable' }))
      .mockResolvedValueOnce(mockStreamResponse({ status: 503, code: 'live_unavailable' }))
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    const { seen } = open()
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['unavailable', STREAM_ERROR_SENTENCES.live_unavailable])

    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2 * RETRY_MILLISECONDS - 1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(lastOf(seen.statuses)).toEqual(['live', undefined])
  })

  it('a bye "unavailable" says so and reconnects on the backoff', async () => {
    fetchMock
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame(), { event: 'bye', data: { reason: 'unavailable' } }] }))
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    const { seen } = open()
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['unavailable', STREAM_ERROR_SENTENCES.live_unavailable])
    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a network failure is RECONNECTING, then retries', async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    const { seen } = open()
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['reconnecting', undefined])
    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS)
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['live', undefined])
  })

  it('a silent connection (no byte for two heartbeats) is dropped and reopened', async () => {
    const held = heldBody()
    fetchMock.mockImplementationOnce(held.impl).mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    const { seen } = open()
    await settle()
    held.send(readyFrame())
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['live', undefined])

    await vi.advanceTimersByTimeAsync(IDLE_MILLISECONDS)
    await settle()
    expect(lastOf(seen.statuses)).toEqual(['reconnecting', undefined])
    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a stream that lived a minute resets the backoff to its first step', async () => {
    const held = heldBody()
    fetchMock
      .mockResolvedValueOnce(mockStreamResponse({ status: 503, code: 'live_unavailable' }))
      .mockImplementationOnce(held.impl)
      .mockResolvedValueOnce(mockStreamResponse({ frames: [readyFrame()] }))
    open()
    await settle()
    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS) // the 503's wait; the next step is 10 s
    await settle()
    held.send(readyFrame())
    for (let s = 0; s < HEALTHY_STREAM_MILLISECONDS; s += 15_000) {
      await vi.advanceTimersByTimeAsync(15_000)
      held.send({ comment: 'heartbeat' })
    }
    held.close()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(RETRY_MILLISECONDS)
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('stop() aborts the open request; no handler and no timer runs after it', async () => {
    const held = heldBody()
    fetchMock.mockImplementationOnce(held.impl)
    const { stream, seen } = open()
    await settle()
    held.send(readyFrame())
    await settle()
    const signal = fetchMock.mock.calls[0][1]?.signal
    const before = seen.statuses.length

    stream.stop()
    await settle()
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MILLISECONDS)
    expect(signal?.aborted).toBe(true)
    expect(seen.statuses).toHaveLength(before)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stop() during a backoff cancels the pending reconnect', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(mockStreamResponse({ status: 503, code: 'live_unavailable' })))
    const { stream } = open()
    await settle()
    stream.stop()
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MILLISECONDS)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})
