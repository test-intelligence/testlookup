/**
 * W15 `useAgentActions`: every read's SWR key (no key -> no request for a
 * non-lead, without a project, in All Projects, without a run), its fetcher's
 * query, the decided list's poll (on while a row runs, off after), and the
 * decide call's revalidation. `swr` is a spy, so each case reads the arguments
 * the hook passed.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AGENT_ACTION_READ_HOOK_NAMES, agentActionHookMocks } from '@/test/agentActionMocks'
import type { AgentActionList } from '@/types/agentActions'

const swrSpy = vi.hoisted(() => vi.fn())
const mutateSpy = vi.hoisted(() => vi.fn())
/** The SWR cache the decided list reads its poll from, by serialized key. */
const cached = vi.hoisted(() => new Map<string, unknown>())
vi.mock('swr', () => ({
  default: swrSpy,
  unstable_serialize: (key: unknown) => JSON.stringify(key),
  useSWRConfig: () => ({ mutate: mutateSpy, cache: { get: (k: string) => (cached.has(k) ? { data: cached.get(k) } : undefined) } }),
}))

const service = vi.hoisted(() => ({ listAgentActions: vi.fn(), decideAgentAction: vi.fn() }))
vi.mock('@/services/agentActionService', () => ({ agentActionService: service }))

const permissions = vi.hoisted(() => ({ isQaLead: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))

const runs = vi.hoisted(() => ({ useRun: vi.fn() }))
vi.mock('@/hooks/useRuns', () => runs)

import * as hooks from './useAgentActions'

const REALTIME = 5_000

function lastCall() {
  const [key, fetcher, options] = swrSpy.mock.calls[swrSpy.mock.calls.length - 1]
  return { key, fetcher: fetcher as () => Promise<unknown>, options }
}

function decided(...statuses: string[]): AgentActionList {
  const items = statuses.map((status, i) => ({ id: `a${i}`, status })) as AgentActionList['items']
  return { items, count: items.length, total: items.length }
}

describe('useAgentActions', () => {
  beforeEach(() => {
    swrSpy.mockReset()
    swrSpy.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
    mutateSpy.mockReset()
    service.listAgentActions.mockReset()
    service.decideAgentAction.mockReset()
    runs.useRun.mockReset()
    runs.useRun.mockReturnValue({ data: { id: 'run-7', project_id: 'proj-1' } })
    permissions.isQaLead = true
  })

  it('exports exactly the hooks the page-test stub stands in for', () => {
    const names = [...AGENT_ACTION_READ_HOOK_NAMES, 'useDecideAgentAction'].sort()
    expect(Object.keys(hooks).sort()).toEqual(names)
    const stub = agentActionHookMocks()
    expect(Object.keys(stub).sort()).toEqual(names)
    expect(stub.usePendingAgentActions()).toMatchObject({ data: undefined, error: undefined, isLoading: false })
    expect(typeof stub.useDecideAgentAction()).toBe('function')
  })

  it.each<[string, () => unknown]>([
    ['pending, not a lead', () => hooks.usePendingAgentActions('proj-1', false)],
    ['pending, no project', () => hooks.usePendingAgentActions(null, true)],
    ['pending, All Projects', () => hooks.usePendingAgentActions('all', true)],
    ['decided, not a lead', () => hooks.useDecidedAgentActions('proj-1', false)],
    ['decided, no project', () => hooks.useDecidedAgentActions(null, true)],
    ['decided, All Projects', () => hooks.useDecidedAgentActions('all', true)],
  ])('%s: no key, so no request', (_name, use) => {
    renderHook(use)
    expect(lastCall().key).toBeNull()
    expect(service.listAgentActions).not.toHaveBeenCalled()
  })

  it('pending: one key for the badge and the list, the queue query, no focus revalidation', async () => {
    renderHook(() => hooks.usePendingAgentActions('proj-1', true))
    const { key, fetcher, options } = lastCall()
    expect(key).toEqual(['agent-actions', 'proj-1', 'pending'])
    expect(options).toMatchObject({ revalidateOnFocus: false })
    expect(options.refreshInterval).toBeUndefined()
    await fetcher()
    expect(service.listAgentActions).toHaveBeenCalledWith('proj-1', { view: 'pending', limit: 200 })
  })

  it('decided: its key and query; polls while a row is approved or executing, then stops', async () => {
    const DECIDED_KEY = JSON.stringify(['agent-actions', 'proj-1', 'decided'])
    const intervalFor = (list: AgentActionList | undefined) => {
      cached.clear()
      if (list) cached.set(DECIDED_KEY, list)
      renderHook(() => hooks.useDecidedAgentActions('proj-1', true))
      return lastCall().options.refreshInterval
    }
    renderHook(() => hooks.useDecidedAgentActions('proj-1', true))
    const { key, fetcher } = lastCall()
    expect(key).toEqual(['agent-actions', 'proj-1', 'decided'])
    await fetcher()
    expect(service.listAgentActions).toHaveBeenCalledWith('proj-1', { view: 'decided', limit: 20 })
    // a NUMBER from the cached list (SWR re-arms its timer when it changes), never the function form
    expect(intervalFor(decided('executed', 'approved'))).toBe(REALTIME)
    expect(intervalFor(decided('executing'))).toBe(REALTIME)
    expect(intervalFor(decided('executed', 'failed', 'rolled_back', 'rejected'))).toBe(0)
    expect(intervalFor(decided())).toBe(0)
    expect(intervalFor(undefined)).toBe(0)
    cached.clear()
  })

  it('run: keyed by the run’s own project for a QA lead, the run query', async () => {
    renderHook(() => hooks.useRunAgentActions('run-7'))
    expect(runs.useRun).toHaveBeenCalledWith('run-7')
    const { key, fetcher, options } = lastCall()
    expect(key).toEqual(['agent-actions', 'proj-1', 'run', 'run-7'])
    expect(options).toMatchObject({ revalidateOnFocus: false })
    await fetcher()
    expect(service.listAgentActions).toHaveBeenCalledWith('proj-1', { test_run_id: 'run-7', limit: 50 })
  })

  it.each<[string, () => void]>([
    ['not a lead', () => { permissions.isQaLead = false }],
    ['no run yet', () => { runs.useRun.mockReturnValue({ data: undefined }) }],
    ['a run without a project', () => { runs.useRun.mockReturnValue({ data: { id: 'run-7' } }) }],
  ])('run, %s: no key, so no request', (_name, arrange) => {
    arrange()
    renderHook(() => hooks.useRunAgentActions('run-7'))
    expect(lastCall().key).toBeNull()
  })

  it('run, no run id: no key and no run read either', () => {
    renderHook(() => hooks.useRunAgentActions(null))
    expect(runs.useRun).toHaveBeenCalledWith(undefined)
    expect(lastCall().key).toBeNull()
  })

  it('decide: PATCHes, then revalidates the project’s agent-action keys and the investigation details', async () => {
    service.decideAgentAction.mockResolvedValue({ id: 'act-1', status: 'approved' })
    const { result } = renderHook(() => hooks.useDecideAgentAction('proj-1'))
    const saved = await result.current('act-1', { status: 'approved' })
    expect(saved).toEqual({ id: 'act-1', status: 'approved' })
    expect(service.decideAgentAction).toHaveBeenCalledWith('proj-1', 'act-1', { status: 'approved' })
    expect(mutateSpy).toHaveBeenCalledTimes(2)
    const [actionsFilter, investigationFilter] = mutateSpy.mock.calls.map((call) => call[0] as (k: unknown) => boolean)
    expect(actionsFilter(['agent-actions', 'proj-1', 'pending'])).toBe(true)
    expect(actionsFilter(['agent-actions', 'proj-1', 'run', 'run-7'])).toBe(true)
    expect(actionsFilter(['agent-actions', 'proj-2', 'pending'])).toBe(false)
    expect(actionsFilter(['pending-defects', 'proj-1'])).toBe(false)
    expect(investigationFilter('/investigations/inv-1')).toBe(true)
    expect(investigationFilter('/projects/proj-1/investigations?limit=20&offset=0')).toBe(false)
  })

  it('decide: a refused PATCH revalidates nothing; no project refuses before any request', async () => {
    service.decideAgentAction.mockRejectedValue(new Error('409'))
    const { result } = renderHook(() => hooks.useDecideAgentAction('proj-1'))
    await expect(result.current('act-1', { status: 'approved' })).rejects.toThrow('409')
    expect(mutateSpy).not.toHaveBeenCalled()
    const { result: none } = renderHook(() => hooks.useDecideAgentAction('all'))
    await expect(none.current('act-1', { status: 'approved' })).rejects.toThrow('Choose one project')
    expect(service.decideAgentAction).toHaveBeenCalledTimes(1)
  })
})
