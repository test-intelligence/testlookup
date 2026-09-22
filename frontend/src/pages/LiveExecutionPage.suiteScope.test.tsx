/**
 * E3 review m3: /live sent the global suite selection only when exactly ONE
 * suite was selected, and fetched everything otherwise, narrowing client-side
 * — the server's 50-session cap then dropped matching sessions. It also read
 * the UI clock (every click) rather than the SETTLED scope. Now it sends the
 * settled selection: one suite exactly as before (scalar `suite_name`),
 * several as a repeated `suite_name` (backend support: agent C, E3 round A).
 */
import { act, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const live = vi.hoisted(() => ({
  useLiveExecution: vi.fn(() => ({
    sessions: [],
    activeSessions: [],
    completedSessions: [],
    recentEvents: [],
    wsStatus: 'closed',
    isLoading: false,
  })),
}))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: ['cart', 'payments'], isLoading: false })) }))
const getData = vi.hoisted(() => vi.fn(async () => ({ sessions: [] })))
vi.mock('@/services/http', () => ({ getData, postData: vi.fn(), deleteData: vi.fn() }))

import LiveExecutionPage from './LiveExecutionPage'
import liveStreamService from '@/services/liveStreamService'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { useSuiteStore } from '@/store/suiteStore'
import { SCOPE_SETTLE_MS, settleScopeNow } from '@/store/settledScope'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'

/** The suite argument of the latest `useLiveExecution` call. */
const lastSuiteArg = () => {
  const calls = live.useLiveExecution.mock.calls as unknown as unknown[][]
  return calls[calls.length - 1][1]
}

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockClear()
  getData.mockClear()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
  settleScopeNow()
})

describe('LiveExecutionPage suite scope', () => {
  it('passes the SETTLED selection — several suites too — not the click-by-click one', async () => {
    vi.useFakeTimers()
    try {
      render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
      expect(lastSuiteArg()).toBeNull()
      act(() => { useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT) })
      // Not yet settled: the request is not rebuilt per click.
      expect(lastSuiteArg()).toBeNull()
      await act(async () => { await vi.advanceTimersByTimeAsync(SCOPE_SETTLE_MS) })
      expect(lastSuiteArg()).toEqual(['cart', 'payments'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('flag OFF: exactly the page-local single suite, as before', () => {
    useMultiFiltersFlagStore.setState({ enabled: false, resolved: true })
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
    expect(lastSuiteArg()).toBeNull()
  })
})

describe('liveStreamService.getActiveSessions wire', () => {
  it('one suite is the scalar it always was; several are a repeated suite_name; none is absent', async () => {
    await liveStreamService.getActiveSessions(PROJECT, 'cart', 1)
    await liveStreamService.getActiveSessions(PROJECT, ['cart', 'payments'], 1)
    await liveStreamService.getActiveSessions(PROJECT, null, 1)
    await liveStreamService.getActiveSessions(PROJECT, [], 1)
    const params = getData.mock.calls.map((c) => (c as unknown as [string, { params: Record<string, unknown> }])[1].params)
    expect(params[0]).toEqual({ project_id: PROJECT, suite_name: 'cart', days: 1 })
    expect(params[1]).toEqual({ project_id: PROJECT, suite_name: ['cart', 'payments'], days: 1 })
    expect(params[2]).toEqual({ project_id: PROJECT, days: 1 })
    expect(params[3]).toEqual({ project_id: PROJECT, days: 1 })
  })
})
