/**
 * W21d ACT-02: the Live hook (opens / stops the stream on project + enabled
 * only; frames judged against the CURRENT filters) and its pure helpers.
 * The stream client is mocked: its own behaviour is activityStream.test.ts.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ActivityStreamHandlers } from '@/services/activityStream'

const stream = vi.hoisted(() => ({
  open: vi.fn(),
  stops: [] as Array<ReturnType<typeof vi.fn>>,
  handlers: [] as ActivityStreamHandlers[],
}))
vi.mock('@/services/activityStream', () => ({
  openActivityStream: stream.open,
}))

import {
  LIVE_PREFERENCE_KEY,
  matchesActivityQuery,
  prependLive,
  readLivePreference,
  useActivityLive,
  writeLivePreference,
  type UseActivityLiveOptions,
} from './useActivityLive'
import { makeActivityEvent } from '@/test/activityLiveMocks'
import type { ActivityPage } from '@/services/activityService'

const P1 = '00000000-0000-4000-8000-0000000000a1'
const P2 = '00000000-0000-4000-8000-0000000000a2'

function feedPage(items: ActivityPage['items'], next: string | null = null): ActivityPage {
  return { items, next_cursor: next, ledger_started_at: '2026-09-01T00:00:00Z', window: { since: null, until: null } }
}

describe('useActivityLive', () => {
  beforeEach(() => {
    stream.open.mockReset()
    stream.stops.length = 0
    stream.handlers.length = 0
    stream.open.mockImplementation((_projectId: string, handlers: ActivityStreamHandlers) => {
      const stop = vi.fn()
      stream.stops.push(stop)
      stream.handlers.push(handlers)
      return { stop }
    })
  })

  const render = (initial: UseActivityLiveOptions) =>
    renderHook((props: UseActivityLiveOptions) => useActivityLive(props), { initialProps: initial })

  it('opens nothing and says off while disabled', () => {
    const { result } = render({ projectId: P1, enabled: false, query: {} })
    expect(stream.open).not.toHaveBeenCalled()
    expect(result.current).toEqual({ status: 'off', message: null })
  })

  it('opens ONE stream for the project, follows its status, and stops on toggle-off, project change and unmount', () => {
    const { result, rerender, unmount } = render({ projectId: P1, enabled: true, query: {} })
    expect(stream.open).toHaveBeenCalledTimes(1)
    expect(stream.open.mock.calls[0][0]).toBe(P1)
    expect(result.current.status).toBe('connecting')

    act(() => stream.handlers[0].onStatus('live'))
    expect(result.current).toEqual({ status: 'live', message: null })

    // A filter change does not reconnect.
    rerender({ projectId: P1, enabled: true, query: { q: 'checkout' } })
    expect(stream.open).toHaveBeenCalledTimes(1)

    rerender({ projectId: P2, enabled: true, query: {} })
    expect(stream.stops[0]).toHaveBeenCalledTimes(1)
    expect(stream.open).toHaveBeenCalledTimes(2)
    expect(stream.open.mock.calls[1][0]).toBe(P2)
    expect(result.current.status).toBe('connecting')

    rerender({ projectId: P2, enabled: false, query: {} })
    expect(stream.stops[1]).toHaveBeenCalledTimes(1)
    expect(result.current.status).toBe('off')

    rerender({ projectId: P2, enabled: true, query: {} })
    unmount()
    expect(stream.stops[2]).toHaveBeenCalledTimes(1)
  })

  it('passes the refusal sentence through and starts again at Connecting when re-enabled', () => {
    const { result, rerender } = render({ projectId: P1, enabled: true, query: {} })
    act(() => stream.handlers[0].onStatus('refused', 'You already have 3 live activity views open; close one to open another.'))
    expect(result.current).toEqual({
      status: 'refused',
      message: 'You already have 3 live activity views open; close one to open another.',
    })
    rerender({ projectId: P1, enabled: false, query: {} })
    rerender({ projectId: P1, enabled: true, query: {} })
    expect(result.current).toEqual({ status: 'connecting', message: null })
  })

  it('inserts only the rows that match the CURRENT filters', () => {
    const onInsert = vi.fn()
    const { rerender } = render({ projectId: P1, enabled: true, query: { category: ['runs'] }, onInsert })
    const run = makeActivityEvent({ category: 'runs', summary: 'Build 42 completed' })
    const flag = makeActivityEvent({ category: 'configuration', summary: 'Flag turned on' })
    act(() => {
      stream.handlers[0].onActivity(run, '1-0')
      stream.handlers[0].onActivity(flag, '2-0')
    })
    expect(onInsert.mock.calls.map(([row]) => row.id)).toEqual([run.id])

    rerender({ projectId: P1, enabled: true, query: { category: ['configuration'] }, onInsert })
    act(() => stream.handlers[0].onActivity(flag, '3-0'))
    expect(onInsert.mock.calls.map(([row]) => row.id)).toEqual([run.id, flag.id])
  })

  it('re-reads page 0 on a fresh ready and on every refresh frame, not on a resumed ready', () => {
    const onRefresh = vi.fn()
    render({ projectId: P1, enabled: true, query: {}, onRefresh })
    act(() => stream.handlers[0].onReady?.({ lastEventId: '5-0', resumed: false }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    act(() => stream.handlers[0].onReady?.({ lastEventId: '6-0', resumed: true }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    act(() => stream.handlers[0].onRefresh?.('gap', '7-0'))
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })

  it('tolerates missing callbacks (a mocked feed hook)', () => {
    render({ projectId: P1, enabled: true, query: {} })
    expect(() => act(() => {
      stream.handlers[0].onActivity(makeActivityEvent(), '1-0')
      stream.handlers[0].onRefresh?.('overflow', '2-0')
      stream.handlers[0].onReady?.({ lastEventId: '2-0', resumed: false })
    })).not.toThrow()
  })
})

describe('matchesActivityQuery', () => {
  const row = makeActivityEvent({
    category: 'release',
    event_type: 'release.activated',
    summary: 'Release 2026.11 was ACTIVATED by Priya',
    actor: { type: 'user', id: 'u-1', name: 'Priya', ref: null },
  })

  it('applies the category, event type and actor filters', () => {
    expect(matchesActivityQuery(row, {})).toBe(true)
    expect(matchesActivityQuery(row, { category: ['release'] })).toBe(true)
    expect(matchesActivityQuery(row, { category: ['runs', 'quality'] })).toBe(false)
    expect(matchesActivityQuery(row, { event_type: ['release.created'] })).toBe(false)
    expect(matchesActivityQuery(row, { actor_type: 'user' })).toBe(true)
    expect(matchesActivityQuery(row, { actor_type: 'api_key' })).toBe(false)
    expect(matchesActivityQuery(row, { actor_id: 'u-2' })).toBe(false)
    expect(matchesActivityQuery(row, { days: 7, limit: 50 })).toBe(true)
  })

  it('a search of 3+ characters is a case-insensitive substring of the summary; a shorter one is ignored', () => {
    expect(matchesActivityQuery(row, { q: 'activated' })).toBe(true)
    expect(matchesActivityQuery(row, { q: '  2026.11  ' })).toBe(true)
    expect(matchesActivityQuery(row, { q: 'checkout' })).toBe(false)
    expect(matchesActivityQuery(row, { q: 'zz' })).toBe(true)
  })
})

describe('prependLive', () => {
  it('puts the row on top of page 0 and leaves the other pages alone', () => {
    const a = makeActivityEvent()
    const b = makeActivityEvent()
    const live = makeActivityEvent()
    const pages = [feedPage([a], 'c1'), feedPage([b])]
    const next = prependLive(pages, live)
    expect(next[0].items.map((e) => e.id)).toEqual([live.id, a.id])
    expect(next[0].next_cursor).toBe('c1')
    expect(next[1]).toBe(pages[1])
    expect(pages[0].items).toEqual([a])
  })

  it('de-duplicates by id across EVERY loaded page', () => {
    const a = makeActivityEvent()
    const older = makeActivityEvent()
    const pages = [feedPage([a], 'c1'), feedPage([older])]
    expect(prependLive(pages, { ...older })).toBe(pages)
    expect(prependLive(pages, { ...a })).toBe(pages)
  })

  it('leaves nothing loaded as nothing loaded, and reads a page without items as empty', () => {
    expect(prependLive([], makeActivityEvent())).toEqual([])
    const live = makeActivityEvent()
    const broken = { ...feedPage([]), items: undefined } as unknown as ActivityPage
    expect(prependLive([broken], live)[0].items).toEqual([live])
  })
})

describe('the Live preference', () => {
  beforeEach(() => window.localStorage.clear())
  afterEach(() => vi.restoreAllMocks())

  it('is OFF when nothing is stored, ON only after it was turned on', () => {
    expect(readLivePreference()).toBe(false)
    writeLivePreference(true)
    expect(window.localStorage.getItem(LIVE_PREFERENCE_KEY)).toBe('on')
    expect(readLivePreference()).toBe(true)
    writeLivePreference(false)
    expect(readLivePreference()).toBe(false)
  })

  it('a throwing store reads OFF and a write to it does not throw', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })
    expect(readLivePreference()).toBe(false)
    expect(() => writeLivePreference(true)).not.toThrow()
  })
})
