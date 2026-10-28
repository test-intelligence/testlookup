/**
 * Fixtures for the activity live tail and Inbox › Activity vitests (W21d ACT-02).
 *
 * - `makeActivityEvent` / `makeMyActivityItem` / `makeMyActivityPage`: the
 *   REAL server shapes (`GET /projects/{id}/activity` items; the
 *   `/activity/mine` page WITH `total`), every field present.
 * - `sseFrames(frames)`: a `ReadableStream<Uint8Array>` of encoded SSE frames,
 *   optionally split into arbitrary chunks (a frame split across reads).
 * - `mockStreamResponse({ status, frames, detail })`: a `fetch` `Response`: a
 *   200 `text/event-stream` body, or a JSON refusal `{detail}`.
 *
 * Timestamps are built at CALL time (never a module constant), so a fixture
 * made inside a test always falls inside that test's window.
 */
import type { ActivityEvent, MyActivityItem, MyActivityPage } from '@/services/activityService'
import { STREAM_ERROR_SENTENCES, type StreamErrorCode } from '@/types/activityLive'

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
export function fixtureUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(sequence).padStart(12, '0')}`
}

export function makeActivityEvent(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  const id = overrides.id ?? fixtureUuid()
  const entityId = overrides.entity?.id ?? fixtureUuid()
  return {
    id,
    project_id: '00000000-0000-4000-8000-0000000000aa',
    release_id: null,
    occurred_at: new Date().toISOString(),
    category: 'test_management',
    event_type: 'test_case.commented',
    actor: { type: 'user', id: '00000000-0000-4000-8000-0000000000bb', name: 'Ana Lead', ref: null },
    entity: {
      type: 'test_case',
      id: entityId,
      label: 'Login works',
      href: `/test-management?case=${entityId}`,
    },
    target: null,
    summary: 'Ana Lead commented on test case Login works',
    context: null,
    has_diff: false,
    source: { table: 'test_case_comments', id: null },
    group_key: null,
    ...overrides,
  }
}

export function makeMyActivityItem(overrides: Partial<MyActivityItem> = {}): MyActivityItem {
  const { involvement = 'actor', ...rest } = overrides
  return { ...makeActivityEvent(rest), involvement }
}

/** The `/activity/mine` answer; `total` defaults to the number of items. */
export function makeMyActivityPage(overrides: Partial<MyActivityPage> = {}): MyActivityPage {
  const items = overrides.items ?? [makeMyActivityItem()]
  return {
    items,
    total: items.length,
    next_cursor: null,
    window: { since: new Date(Date.now() - 7 * 86_400_000).toISOString(), until: null },
    view: 'all',
    projects: 1,
    ...overrides,
  }
}

/**
 * One SSE frame. `data` that is not a string is JSON-encoded; a string is sent
 * verbatim (so a test can send a non-JSON frame). `comment` writes a `:` line
 * (a heartbeat) and nothing else.
 */
export interface SseFrame {
  event?: string
  id?: string
  data?: unknown
  retry?: number
  comment?: string
}

export function encodeSseFrame(frame: SseFrame): string {
  if (frame.comment !== undefined) return `: ${frame.comment}\n\n`
  const lines: string[] = []
  if (frame.retry !== undefined) lines.push(`retry: ${frame.retry}`)
  if (frame.event !== undefined) lines.push(`event: ${frame.event}`)
  if (frame.id !== undefined) lines.push(`id: ${frame.id}`)
  if (frame.data !== undefined) {
    const text = typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data)
    for (const line of text.split('\n')) lines.push(`data: ${line}`)
  }
  return `${lines.join('\n')}\n\n`
}

/**
 * The frames as one byte stream that then ENDS. `chunkSize` re-splits the
 * whole text into pieces of that many characters, so frames straddle reads.
 */
export function sseFrames(frames: SseFrame[], options: { chunkSize?: number } = {}): ReadableStream<Uint8Array> {
  const text = frames.map(encodeSseFrame).join('')
  const size = options.chunkSize && options.chunkSize > 0 ? options.chunkSize : Math.max(text.length, 1)
  const pieces: string[] = []
  for (let start = 0; start < text.length; start += size) pieces.push(text.slice(start, start + size))
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece))
      controller.close()
    },
  })
}

export interface MockStreamResponseOptions {
  status?: number
  frames?: SseFrame[]
  chunkSize?: number
  /** A refusal's body: a string (the guard's own 403) or `{code, message}`. */
  detail?: string | { code: string; message: string }
  /** Shorthand for `detail: {code, message: STREAM_ERROR_SENTENCES[code]}`. */
  code?: StreamErrorCode
}

/** A `fetch` Response for the stream route: 200 = the frames; anything else = JSON `{detail}`. */
export function mockStreamResponse(options: MockStreamResponseOptions = {}): Response {
  const { status = 200, frames = [], chunkSize, code } = options
  if (status === 200) {
    return new Response(sseFrames(frames, { chunkSize }), {
      status,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
    })
  }
  const detail = options.detail ?? (code ? { code, message: STREAM_ERROR_SENTENCES[code] } : 'Request failed')
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
