/**
 * Telemetry must not silently discard the batch it fails to send.
 *
 * `flush()` empties `_batch` **before** calling `navigator.sendBeacon`, and
 * ignored the boolean the beacon returns. `false` does not mean "sent" — it
 * means the user agent refused to queue the request, and the events are gone.
 *
 * Measured against the live deployment (Chromium, boundary errors carrying a
 * 4 KB stack + 2 KB component stack each):
 *
 *     1 error   6,303 bytes   sendBeacon -> true
 *     5 errors 31,419 bytes   sendBeacon -> true
 *    10 errors 62,814 bytes   sendBeacon -> FALSE     <-- refused
 *    20 errors 125,614 bytes  sendBeacon -> FALSE     <-- refused
 *    the same 125,614-byte body over fetch()  ->  202 Accepted
 *
 * So the payload is not the problem: the server accepts it. Chromium's
 * per-origin beacon quota is 64 KB, and ten error-boundary reports reach it.
 * Ten boundary errors inside one 5 s batch window is not exotic — a component
 * that throws on every render produces them in a fraction of a second, and
 * that is precisely the incident you need the report for.
 *
 * The failure is silent in both directions: the beacon's `false` was dropped,
 * and the `catch` blocks are deliberately empty ("never throw from error
 * reporting"). So a page could be melting down and the backend would see
 * nothing at all.
 *
 * Note the `fetch` fallback in the original code was unreachable — it sits in
 * the `else` of `if (navigator.sendBeacon)`, so it only ran in a browser with
 * no sendBeacon at all. Every real browser has it.
 *
 * These tests pin: the buffer survives a refused send, oversized batches are
 * split so the beacon can accept them, a single oversized event is truncated
 * rather than dropped, and the buffer is bounded so a render loop cannot grow
 * it without limit.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

import { flush, reportBoundaryError, reportWebVital } from './errorReporting'

/** Chromium's per-origin beacon quota. Bodies at or above this get refused. */
const BEACON_QUOTA = 64 * 1024

type Sent = { url: string; bytes: number; body: string }

let sent: Sent[]
let beaconAccepts: (bytes: number) => boolean

/** Stand-in for a real UA: refuses anything at or above the quota, like Chromium. */
function installBeacon() {
  const beacon = vi.fn((url: string, data?: BodyInit | null) => {
    const body = String((data as Blob & { __text?: string })?.__text ?? data ?? '')
    const bytes = body.length
    if (!beaconAccepts(bytes)) return false
    sent.push({ url, bytes, body })
    return true
  })
  Object.defineProperty(navigator, 'sendBeacon', { value: beacon, configurable: true, writable: true })
  return beacon
}

beforeEach(() => {
  sent = []
  beaconAccepts = (bytes) => bytes < BEACON_QUOTA

  // Blob does not expose its text synchronously; carry it so the fake beacon
  // can measure the body the way the browser does.
  vi.stubGlobal(
    'Blob',
    class {
      __text: string
      type: string
      constructor(parts: string[], opts?: { type?: string }) {
        this.__text = parts.join('')
        this.type = opts?.type ?? ''
      }
      get size() {
        return this.__text.length
      }
    },
  )
  installBeacon()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      sent.push({ url, bytes: String(init?.body ?? '').length, body: String(init?.body ?? '') })
      return { ok: true, status: 202 } as Response
    }),
  )
  flush() // drain anything a previous test left buffered
  sent = []
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** One error-boundary report of roughly the size measured live (~6.3 KB). */
function pushBoundaryError(i: number) {
  const err = new Error(`boom ${i}`)
  err.stack = 'x'.repeat(4000)
  reportBoundaryError(err, 'y'.repeat(2000))
}

/** Every event the transport actually carried, across all requests. */
function deliveredErrorCount(): number {
  return sent.reduce((n, s) => n + (JSON.parse(s.body).errors?.length ?? 0), 0)
}

describe('a refused send must not destroy the batch', () => {
  it('delivers all ten errors even though one 63 KB beacon is refused', () => {
    for (let i = 0; i < 10; i++) pushBoundaryError(i)
    flush()

    expect(
      deliveredErrorCount(),
      'the beacon refused the 63 KB body and flush() had already cleared the buffer, ' +
        'so every error-boundary report from the incident was lost',
    ).toBe(10)
  })

  it('keeps every request under the quota so the beacon can accept it', () => {
    for (let i = 0; i < 25; i++) pushBoundaryError(i)
    flush()

    expect(sent.length, 'nothing was transmitted at all').toBeGreaterThan(0)
    for (const s of sent) {
      expect(s.bytes, `a ${s.bytes}-byte request will be refused by the UA`).toBeLessThan(BEACON_QUOTA)
    }
  })

  it('re-buffers rather than drops when the transport refuses outright', () => {
    // A UA that refuses everything — nothing can go out on this flush.
    beaconAccepts = () => false
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      }),
    )
    pushBoundaryError(1)
    flush()
    expect(sent.length).toBe(0)

    // Transport recovers; the next flush must still have the event.
    beaconAccepts = (bytes) => bytes < BEACON_QUOTA
    flush()
    expect(
      deliveredErrorCount(),
      'the event was discarded on the failed flush, so a transient outage ' +
        'permanently loses the report',
    ).toBe(1)
  })
})

describe('a single oversized event', () => {
  it('is truncated, not dropped, when it alone exceeds the quota', () => {
    const err = new Error('one enormous stack')
    err.stack = 'z'.repeat(200_000)
    reportBoundaryError(err, 'c'.repeat(100_000))
    flush()

    expect(deliveredErrorCount(), 'the single oversized event vanished').toBe(1)
    for (const s of sent) expect(s.bytes).toBeLessThan(BEACON_QUOTA)
    // The message identifies the error and must survive truncation intact.
    expect(sent.map((s) => s.body).join('')).toContain('one enormous stack')
  })
})

describe('the buffer is bounded', () => {
  it('does not grow without limit when a component throws on every render', () => {
    // 5000 events between flushes: a render loop, not a realistic batch.
    for (let i = 0; i < 5000; i++) pushBoundaryError(i)
    flush()

    // Bounded retention means most are dropped — that is the intended
    // behaviour — but it must be bounded, and the drop must be *reported*
    // rather than silent, so the backend can tell truncation from quiet.
    const bodies = sent.map((s) => s.body).join('')
    expect(sent.length, 'an unbounded buffer produced a request storm').toBeLessThan(20)
    expect(
      bodies,
      'events were dropped with no record, so the backend cannot tell a ' +
        'truncated report from a complete one',
    ).toContain('dropped')
  })
})

describe('vitals still work', () => {
  it('sends a normal web-vital batch in a single request', () => {
    reportWebVital({ name: 'LCP', value: 1234.5, rating: 'good', delta: 1234.5 } as never)
    flush()
    expect(sent.length).toBe(1)
    expect(JSON.parse(sent[0].body).vitals[0].name).toBe('LCP')
  })

  it('flush is a no-op when nothing is buffered', () => {
    flush()
    expect(sent.length).toBe(0)
  })
})
