/**
 * W15 `useDecidedAgentActions` through the REAL SWR (the sibling
 * `useAgentActions.test.ts` spies on `swr`): the decided list STARTS polling
 * once a revalidation brings a running row (an Approve elsewhere revalidates
 * the key) and STOPS once nothing runs. SWR re-reads a function-form
 * `refreshInterval` only when a timer it armed fires, so with that form the
 * list never polled after an Approve and the row sat on "Running…" (found by
 * G-E's `inbox-agent-actions` spec). REALTIME is shortened to 40 ms here.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgentActionList } from '@/types/agentActions'

vi.mock('@/config/refreshIntervals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/config/refreshIntervals')>()
  return { ...actual, REFRESH_INTERVALS: { ...actual.REFRESH_INTERVALS, REALTIME: 40 } }
})
const service = vi.hoisted(() => ({ listAgentActions: vi.fn(), decideAgentAction: vi.fn() }))
vi.mock('@/services/agentActionService', () => ({ agentActionService: service }))

import { useDecidedAgentActions } from './useAgentActions'

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function decided(status: string): AgentActionList {
  return { items: [{ id: 'act-1', status }] as AgentActionList['items'], count: 1, total: 1 }
}

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

describe('useDecidedAgentActions polling (real SWR)', () => {
  beforeEach(() => service.listAgentActions.mockReset())

  it('starts once a running row arrives and stops when it has finished', async () => {
    const answers = [decided('rejected'), decided('approved'), decided('executing'), decided('executed')]
    let call = 0
    service.listAgentActions.mockImplementation(async () => answers[Math.min(call++, answers.length - 1)])

    const { result } = renderHook(() => useDecidedAgentActions('proj-1', true), { wrapper })
    await waitFor(() => expect(result.current.data?.items[0]?.status).toBe('rejected'))
    await pause(150)
    expect(service.listAgentActions, 'nothing runs: no poll').toHaveBeenCalledTimes(1)

    // An Approve elsewhere revalidates the key (useDecideAgentAction's mutate): the row is now approved.
    await act(async () => {
      await result.current.mutate()
    })
    await waitFor(() => expect(result.current.data?.items[0]?.status).toBe('executed'), { timeout: 2000 })
    expect(service.listAgentActions).toHaveBeenCalledTimes(4)

    await pause(200)
    expect(service.listAgentActions, 'nothing runs any more: the poll stopped').toHaveBeenCalledTimes(4)
  })

  it('a first load that already holds a running row polls until it finishes', async () => {
    const answers = [decided('executing'), decided('executed')]
    let call = 0
    service.listAgentActions.mockImplementation(async () => answers[Math.min(call++, answers.length - 1)])

    const { result } = renderHook(() => useDecidedAgentActions('proj-1', true), { wrapper })
    await waitFor(() => expect(result.current.data?.items[0]?.status).toBe('executed'), { timeout: 2000 })
    await pause(200)
    expect(service.listAgentActions).toHaveBeenCalledTimes(2)
  })
})
