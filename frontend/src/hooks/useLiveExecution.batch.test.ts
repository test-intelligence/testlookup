/**
 * W25e HYG-06 E-3 (T-C2): the live hook asks for protocol 2 and merges a whole
 * `live_batch` in one render pass.
 *
 * A fake `WebSocket` global captures what the hook sends and lets the test
 * deliver frames; SWR is mocked so the polling side stays out of the way (its
 * `mutate` is the spy a completed run must call once per frame).
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LIVE_BATCH_FRAME, LIVE_PROTOCOL_BATCH, type LiveBatchEntry } from '@/types/liveFrames'

const swr = vi.hoisted(() => {
  const mutate = vi.fn(() => Promise.resolve(undefined))
  // ONE stable result object: the hook's callbacks depend on `mutate`, and a
  // new identity per render would reconnect the socket.
  const result = { data: undefined, mutate }
  return { mutate, result }
})
vi.mock('swr', () => ({ default: vi.fn(() => swr.result) }))
vi.mock('@/services/liveStreamService', () => ({ default: { getActiveSessions: vi.fn() } }))

import { useLiveExecution } from './useLiveExecution'
import { useAuthStore } from '@/store/authStore'

const PROJECT = 'zz-project-live'
const TOKEN = 'zz-token-sentinel-q'
const RUN_A = 'zz-run-alpha'
const RUN_B = 'zz-run-bravo'

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []
  readonly url: string
  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
  }

  /** The server accepted the connection. */
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  deliver(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

function batch(sequenceId: string, events: LiveBatchEntry[]) {
  const listed = events.reduce((n, e) => n + (e.results?.length ?? 0), 0)
  return {
    type: LIVE_BATCH_FRAME, protocol: 2, sequence_id: sequenceId, events,
    counts: { events: events.length, entries: events.length, results_listed: listed, results_dropped: 0 },
  }
}

const started = (runId: string, seq: string): LiveBatchEntry => ({
  type: 'live_run_started', run_id: runId, project_id: PROJECT, build_number: `b-${runId}`, sequence_id: seq,
})

function resultEntry(runId: string, seq: string, passed: number, failed: number, names: string[]): LiveBatchEntry {
  return {
    type: 'live_test_result', run_id: runId, project_id: PROJECT, status: 'running',
    passed, failed, skipped: 0, broken: 0, total: passed + failed,
    pass_rate: Math.round((passed / (passed + failed)) * 100),
    last_test: names[names.length - 1], last_status: failed > 0 ? 'FAILED' : 'PASSED',
    sequence_id: seq, coalesced: names.length,
    results: names.map((name, i) => ({ test_name: name, status: 'PASSED', sequence_id: `${seq.split('-')[0]}-${i}` })),
    results_dropped: 0,
  }
}

function mount() {
  let renders = 0
  const hook = renderHook(() => {
    renders += 1
    return useLiveExecution(PROJECT)
  })
  const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
  act(() => ws.open())
  return { ...hook, ws, renders: () => renders }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  swr.mutate.mockClear()
  useAuthStore.setState({ token: TOKEN })
})
afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ token: null })
})

describe('useLiveExecution — protocol 2 batches (W25e E-3)', () => {
  it('asks for protocol 2 in the auth frame', () => {
    const { ws } = mount()

    expect(ws.url).toMatch(new RegExp(`/ws/live/${PROJECT}$`))
    expect(ws.sent.map(s => JSON.parse(s) as unknown)).toEqual([
      { type: 'auth', token: TOKEN, last_event_id: null, protocol: LIVE_PROTOCOL_BATCH },
    ])
    expect(LIVE_PROTOCOL_BATCH).toBe(2)
  })

  it('one live_batch updates every run in ONE render pass and grows the feed by its expansion', () => {
    const { result, ws, renders } = mount()
    const before = renders()

    act(() => ws.deliver(batch('1730460000140-0', [
      started(RUN_A, '1730460000101-0'),
      started(RUN_B, '1730460000102-0'),
      resultEntry(RUN_A, '1730460000130-0', 2, 1, ['test_a1', 'test_a2', 'test_a3']),
      resultEntry(RUN_B, '1730460000140-0', 1, 0, ['test_b1']),
    ])))

    expect(renders() - before).toBe(1)
    const byRun = Object.fromEntries(result.current.sessions.map(s => [s.run_id, s]))
    expect(byRun[RUN_A]).toMatchObject({ status: 'running', passed: 2, failed: 1, total: 3, current_test: 'test_a3' })
    expect(byRun[RUN_B]).toMatchObject({ status: 'running', passed: 1, failed: 0, total: 1, current_test: 'test_b1' })
    // 2 starts + 3 + 1 listed results, newest first.
    expect(result.current.recentEvents.map(e => e.last_test ?? e.type)).toEqual([
      'test_b1', 'test_a3', 'test_a2', 'test_a1', 'live_run_started', 'live_run_started',
    ])
    expect(swr.mutate).not.toHaveBeenCalled()
  })

  it('ignores a batch whose sequence id is not newer than the cursor', () => {
    const { result, ws } = mount()
    act(() => ws.deliver(batch('1730460000140-0', [
      started(RUN_A, '1730460000101-0'),
      resultEntry(RUN_A, '1730460000140-0', 1, 0, ['test_a1']),
    ])))
    const sessions = result.current.sessions
    const feed = result.current.recentEvents

    act(() => ws.deliver(batch('1730460000140-0', [resultEntry(RUN_A, '1730460000140-0', 9, 9, ['test_dup'])])))
    act(() => ws.deliver(batch('1730460000139-5', [resultEntry(RUN_A, '1730460000139-5', 9, 9, ['test_old'])])))

    expect(result.current.sessions).toBe(sessions)
    expect(result.current.recentEvents).toBe(feed)
  })

  it('a batch completing two runs refetches once and marks both completed', () => {
    const { result, ws } = mount()
    act(() => ws.deliver(batch('1730460000102-0', [started(RUN_A, '1730460000101-0'), started(RUN_B, '1730460000102-0')])))
    expect(swr.mutate).not.toHaveBeenCalled()

    act(() => ws.deliver(batch('1730460000201-0', [
      { type: 'live_run_complete', run_id: RUN_A, pass_rate: 100, sequence_id: '1730460000200-0' },
      { type: 'live_run_complete', run_id: RUN_B, pass_rate: 50, sequence_id: '1730460000201-0' },
    ])))

    expect(swr.mutate).toHaveBeenCalledTimes(1)
    expect(result.current.sessions.map(s => [s.run_id, s.status, s.pass_rate])).toEqual(
      expect.arrayContaining([[RUN_A, 'completed', 100], [RUN_B, 'completed', 50]]),
    )
    expect(result.current.sessions.every(s => typeof s.completed_at === 'string')).toBe(true)
  })

  it('still merges legacy frames from an older server (one event per frame)', () => {
    const { result, ws } = mount()

    act(() => ws.deliver({ type: 'live_run_started', run_id: RUN_A, project_id: PROJECT, build_number: 'b-7', sequence_id: '1730460000300-0' }))
    act(() => ws.deliver({
      type: 'live_test_result', run_id: RUN_A, project_id: PROJECT, passed: 4, failed: 1, skipped: 0, broken: 0,
      total: 5, pass_rate: 80, last_test: 'test_legacy', last_status: 'FAILED', sequence_id: '1730460000301-0',
    }))

    expect(result.current.sessions).toEqual([expect.objectContaining({
      run_id: RUN_A, build_number: 'b-7', status: 'running', passed: 4, failed: 1, total: 5, current_test: 'test_legacy',
    })])
    expect(result.current.recentEvents.map(e => e.type)).toEqual(['live_test_result', 'live_run_started'])
    act(() => ws.deliver({ type: 'live_run_complete', run_id: RUN_A, pass_rate: 80, sequence_id: '1730460000302-0' }))
    expect(swr.mutate).toHaveBeenCalledTimes(1)
    expect(result.current.sessions[0].status).toBe('completed')
  })
})
