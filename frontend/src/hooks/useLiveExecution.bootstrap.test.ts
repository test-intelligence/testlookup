/**
 * D-94 (2026-11-21): the socket bootstrap must not wipe the completed sessions.
 *
 * `initial_state` lists the ACTIVE runs only. The hook replaced its whole list
 * with it, so the completed sessions the first poll had loaded vanished and
 * the table read "No active execution sessions" until the next poll (10 s with
 * the socket open). The page also left its loading state as soon as any row
 * existed, so a bootstrap landing before the poll showed a partial table.
 *
 * SWR is mocked so the test drives the poll's `onSuccess` itself.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LiveSessionState } from '@/types/live-stream'

const swr = vi.hoisted(() => {
  const mutate = vi.fn(() => Promise.resolve(undefined))
  const state: { options: { onSuccess?: (d: unknown) => void } | null } = { options: null }
  const result = { data: undefined, mutate }
  return { mutate, result, state }
})
vi.mock('swr', () => ({
  default: vi.fn((_key: unknown, _fetcher: unknown, options: { onSuccess?: (d: unknown) => void }) => {
    swr.state.options = options
    return swr.result
  }),
}))
vi.mock('@/services/liveStreamService', () => ({ default: { getActiveSessions: vi.fn() } }))

import { useLiveExecution } from './useLiveExecution'
import { useAuthStore } from '@/store/authStore'

const PROJECT = 'zz-project-boot'
const U1 = 'dddddddd-0000-5000-8000-000000000001'

class FakeWebSocket {
  static readonly OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(readonly url: string) { FakeWebSocket.instances.push(this) }
  send() {}
  close() { this.readyState = 3 }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.() }
  deliver(frame: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(frame) }) }
}

function session(p: Partial<LiveSessionState>): LiveSessionState {
  return {
    run_id: 'r', project_id: PROJECT, build_number: 'b', status: 'completed',
    total: 1, passed: 1, failed: 0, skipped: 0, broken: 0, pass_rate: 100,
    completed_at: new Date().toISOString(),
    ...p,
  }
}

function mount() {
  const hook = renderHook(() => useLiveExecution(PROJECT))
  const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
  act(() => ws.open())
  return { ...hook, ws }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  swr.mutate.mockClear()
  useAuthStore.setState({ token: 'zz-token-boot' })
})
afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ token: null })
})

describe('useLiveExecution — the socket bootstrap (D-94)', () => {
  it('an empty initial_state keeps the two completed sessions the poll loaded', () => {
    const { result, ws } = mount()
    act(() => swr.state.options?.onSuccess?.({
      sessions: [session({ run_id: 'zz-done-1' }), session({ run_id: 'zz-done-2' })], count: 2,
    }))

    act(() => ws.deliver({ type: 'initial_state', sessions: [], sequence_id: '1730460000100-0' }))

    expect(result.current.sessions.map(s => s.run_id).sort()).toEqual(['zz-done-1', 'zz-done-2'])
  })

  it('a running row in the bootstrap is laid over the polled row (its run number survives)', () => {
    const { result, ws } = mount()
    act(() => swr.state.options?.onSuccess?.({
      sessions: [session({ run_id: 'ci-7', test_run_id: U1, status: 'running', run_seq: 4, passed: 1 })], count: 1,
    }))

    act(() => ws.deliver({
      type: 'reconcile_required', sequence_id: '1730460000200-0',
      sessions: [{ run_id: 'ci-7', test_run_id: U1, project_id: PROJECT, build_number: 'ci-7', status: 'running', total: 9, passed: 6, failed: 0, skipped: 0, broken: 0 }],
    }))

    expect(result.current.sessions).toEqual([expect.objectContaining({ run_id: 'ci-7', run_seq: 4, passed: 6, total: 9 })])
    expect(swr.mutate).toHaveBeenCalledTimes(1)
  })

  it('stays loading until the first poll answers, even when the bootstrap lands first', () => {
    const { result, ws } = mount()
    act(() => ws.deliver({
      type: 'initial_state', sequence_id: '1730460000300-0',
      sessions: [{ run_id: 'ci-8', test_run_id: U1, project_id: PROJECT, build_number: 'ci-8', status: 'running', total: 3, passed: 0, failed: 0, skipped: 0, broken: 0 }],
    }))
    expect(result.current.sessions).toHaveLength(1)
    expect(result.current.isLoading).toBe(true)

    act(() => swr.state.options?.onSuccess?.({ sessions: [session({ run_id: 'zz-done-3' })], count: 1 }))
    expect(result.current.isLoading).toBe(false)
  })
})
