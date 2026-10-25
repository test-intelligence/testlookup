/**
 * W14 `useAgentOps`: every new read's SWR key (no key -> no request until its
 * id is known), its refresh rule and its fetcher. `swr` is a spy, so each case
 * reads the arguments the hook passed.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AGENT_OPS_HOOK_NAMES, agentOpsHookMocks } from '@/test/agentOpsMocks'
import type { WorkflowItem } from '@/types/workflowDefinition'

const swrSpy = vi.hoisted(() => vi.fn())
vi.mock('swr', () => ({ default: swrSpy }))

const agent = vi.hoisted(() => ({
  getReplay: vi.fn(), getAgenticRuntime: vi.fn(), comparePipelines: vi.fn(), getBulkJob: vi.fn(),
}))
vi.mock('@/services/agentService', () => ({ default: agent }))
const deep = vi.hoisted(() => ({ getPreScan: vi.fn(), getHistory: vi.fn(), getEstimate: vi.fn() }))
vi.mock('@/services/deepInvestigationService', () => ({ deepInvestigationService: deep }))
const workflows = vi.hoisted(() => ({ listWorkflows: vi.fn(), getWorkflowDefaults: vi.fn() }))
vi.mock('@/services/workflowService', () => workflows)

import * as hooks from './useAgentOps'

const REALTIME = 5_000

function lastCall() {
  const [key, fetcher, options] = swrSpy.mock.calls[swrSpy.mock.calls.length - 1]
  return { key, fetcher: fetcher as () => Promise<unknown>, options }
}

describe('useAgentOps', () => {
  beforeEach(() => {
    swrSpy.mockReset()
    swrSpy.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
    for (const fn of [...Object.values(agent), ...Object.values(deep), ...Object.values(workflows)]) fn.mockReset()
  })

  it('exports exactly the hooks the page-test stub stands in for', () => {
    expect(Object.keys(hooks).sort()).toEqual([...AGENT_OPS_HOOK_NAMES].sort())
    const stub = agentOpsHookMocks()
    expect(Object.keys(stub).sort()).toEqual([...AGENT_OPS_HOOK_NAMES].sort())
    expect(stub.usePreScan()).toMatchObject({ data: undefined, error: undefined, isLoading: false })
  })

  it.each<[string, () => unknown]>([
    ['usePipelineReplay', () => hooks.usePipelineReplay(null, true)],
    ['useAgenticRuntime', () => hooks.useAgenticRuntime(null, true)],
    ['usePipelineCompare (no a)', () => hooks.usePipelineCompare(null, 'b')],
    ['usePipelineCompare (no b)', () => hooks.usePipelineCompare('a', null)],
    ['usePipelineCompare (a === b)', () => hooks.usePipelineCompare('same', 'same')],
    ['useWorkflowChoices (no project)', () => hooks.useWorkflowChoices(null, true)],
    ['useWorkflowChoices (not opened)', () => hooks.useWorkflowChoices('p1', false)],
    ['useWorkflowDefaults', () => hooks.useWorkflowDefaults(null)],
    ['useBulkJob', () => hooks.useBulkJob(null)],
    ['usePreScan', () => hooks.usePreScan(null)],
    ['useDeepHistory', () => hooks.useDeepHistory(null, 1)],
    ['useDeepEstimate', () => hooks.useDeepEstimate(null, {})],
  ])('%s: no key without its ids, so no request', (_name, use) => {
    renderHook(use)
    expect(lastCall().key).toBeNull()
    expect(lastCall().options).toMatchObject({ revalidateOnFocus: false })
  })

  it('the replay and runtime reads poll only while the pipeline is live', async () => {
    renderHook(() => hooks.usePipelineReplay('pipe-1', true))
    expect(lastCall().key).toEqual(['agent-replay', 'pipe-1'])
    expect(lastCall().options).toEqual({ refreshInterval: REALTIME, revalidateOnFocus: false })
    agent.getReplay.mockResolvedValue({ replayable: true })
    await expect(lastCall().fetcher()).resolves.toEqual({ replayable: true })
    expect(agent.getReplay).toHaveBeenCalledWith('pipe-1')

    renderHook(() => hooks.usePipelineReplay('pipe-1', false))
    expect(lastCall().options).toEqual({ refreshInterval: 0, revalidateOnFocus: false })

    renderHook(() => hooks.useAgenticRuntime('pipe-2', false))
    expect(lastCall().key).toEqual(['agent-runtime', 'pipe-2'])
    expect(lastCall().options).toEqual({ refreshInterval: 0, revalidateOnFocus: false })
    await lastCall().fetcher()
    expect(agent.getAgenticRuntime).toHaveBeenCalledWith('pipe-2')
  })

  it('compare keys on both ids and calls the service with them', async () => {
    renderHook(() => hooks.usePipelineCompare('pipe-a', 'pipe-b'))
    expect(lastCall().key).toEqual(['agent-compare', 'pipe-a', 'pipe-b'])
    await lastCall().fetcher()
    expect(agent.comparePipelines).toHaveBeenCalledWith('pipe-a', 'pipe-b')
  })

  it('the bulk job polls while active and stops once finished or cancelled', async () => {
    renderHook(() => hooks.useBulkJob('job-1'))
    const { key, options, fetcher } = lastCall()
    expect(key).toEqual(['bulk-job', 'job-1'])
    const interval = options.refreshInterval as (data?: { state: string }) => number
    expect(interval({ state: 'active' })).toBe(REALTIME)
    expect(interval({ state: 'finished' })).toBe(0)
    expect(interval({ state: 'cancelled' })).toBe(0)
    expect(interval(undefined)).toBe(0)
    await fetcher()
    expect(agent.getBulkJob).toHaveBeenCalledWith('job-1')
  })

  it('the deep reads key on the run / project / page / query', async () => {
    renderHook(() => hooks.usePreScan('run-1'))
    expect(lastCall().key).toEqual(['deep-prescan', 'run-1'])
    await lastCall().fetcher()
    expect(deep.getPreScan).toHaveBeenCalledWith('run-1')

    renderHook(() => hooks.useDeepHistory('p1', 2))
    expect(lastCall().key).toEqual(['deep-history', 'p1', 2])
    await lastCall().fetcher()
    expect(deep.getHistory).toHaveBeenCalledWith('p1', 2)

    const q = { selected_failures: 3, model_tier: 'slm' }
    renderHook(() => hooks.useDeepEstimate('run-1', q))
    expect(lastCall().key).toEqual(['deep-estimate', 'run-1', JSON.stringify(q)])
    expect(lastCall().options).toEqual({ revalidateOnFocus: false, keepPreviousData: true })
    await lastCall().fetcher()
    expect(deep.getEstimate).toHaveBeenCalledWith('run-1', q)
  })

  it('the workflow choices list built-ins, each fork\'s newest PUBLISHED version, and the defaults', async () => {
    const item = (workflow_id: string, version: number, status: 'draft' | 'published', extra = {}) =>
      ({ workflow_id, version, status, built_in: false, published_at: null, ...extra }) as unknown as WorkflowItem
    workflows.listWorkflows.mockResolvedValue([
      item('offline', 1, 'published', { built_in: true }),
      item('deep', 1, 'published', { built_in: true }),
      item('wf.a', 3, 'draft'),
      item('wf.a', 2, 'published', { published_at: '2026-10-02T00:00:00Z' }),
      item('wf.a', 1, 'published', { published_at: '2026-10-01T00:00:00Z' }),
      item('wf.b', 1, 'published', { published_at: '2026-10-05T00:00:00Z' }),
      item('wf.draft-only', 1, 'draft'),
    ])
    workflows.getWorkflowDefaults.mockResolvedValue({ project_id: 'p1', defaults: [{ base: 'offline' }] })

    renderHook(() => hooks.useWorkflowChoices('p1', true))
    expect(lastCall().key).toEqual(['workflow-choices', 'p1'])
    const choices = (await lastCall().fetcher()) as hooks.WorkflowChoices
    expect(choices.builtins.map((w) => w.workflow_id)).toEqual(['offline', 'deep'])
    expect(choices.forks.map((w) => `${w.workflow_id}@${w.version}`)).toEqual(['wf.b@1', 'wf.a@2'])
    expect(choices.defaults).toEqual([{ base: 'offline' }])
    expect(workflows.listWorkflows).toHaveBeenCalledWith('p1')
    expect(workflows.getWorkflowDefaults).toHaveBeenCalledWith('p1')

    renderHook(() => hooks.useWorkflowDefaults('p1'))
    expect(lastCall().key).toEqual(['workflow-defaults', 'p1'])
  })
})
