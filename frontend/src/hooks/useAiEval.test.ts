/**
 * W16 `useAiEval`: every read's SWR key (no key -> no request in All
 * Projects, without an id, or for a non-admin on the two ADMIN reads), its
 * fetcher's query, the runs polls (on while a run is queued / running, off
 * at a terminal status), the Tiers badge and the queue sharing ONE key, and
 * each mutation's revalidation. `swr` is a spy, so each case reads the
 * arguments the hook passed.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AI_EVAL_MUTATION_HOOK_NAMES, AI_EVAL_READ_HOOK_NAMES, aiEvalHookMocks } from '@/test/aiEvalMocks'

const swrSpy = vi.hoisted(() => vi.fn())
const mutateSpy = vi.hoisted(() => vi.fn())
/** The SWR cache the polls read from, by serialized key. */
const cached = vi.hoisted(() => new Map<string, unknown>())
vi.mock('swr', () => ({
  default: swrSpy,
  unstable_serialize: (key: unknown) => JSON.stringify(key),
  useSWRConfig: () => ({
    mutate: mutateSpy,
    cache: { get: (k: string) => (cached.has(k) ? { data: cached.get(k) } : undefined) },
  }),
}))

const service = vi.hoisted(() => ({
  listAgentStackGateRuns: vi.fn(),
  getEvalManifest: vi.fn(),
  getCandidateOptions: vi.fn(),
  listEvalRuns: vi.fn(),
  getEvalRun: vi.fn(),
  listEvalRunItems: vi.fn(),
  listTierComparisonRuns: vi.fn(),
  listReviewerQualityRuns: vi.fn(),
  listShadowPairs: vi.fn(),
  evaluateCandidate: vi.fn(),
  setBaseline: vi.fn(),
  labelShadowPair: vi.fn(),
}))
vi.mock('@/services/aiEvalService', () => service)

import * as hooks from './useAiEval'

const REALTIME = 5_000
const P = 'prA1b2C3d4'

function lastCall() {
  const [key, fetcher, options] = swrSpy.mock.calls[swrSpy.mock.calls.length - 1]
  return { key, fetcher: fetcher as () => Promise<unknown>, options }
}

/** The mutate matcher passed by the n-th mutate call. */
function matcher(n = 0): (key: unknown) => boolean {
  return mutateSpy.mock.calls[n][0] as (key: unknown) => boolean
}

describe('useAiEval', () => {
  beforeEach(() => {
    swrSpy.mockReset()
    swrSpy.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
    mutateSpy.mockReset()
    cached.clear()
    for (const fn of Object.values(service)) fn.mockReset()
  })

  it('exports exactly the hooks the test stub stands in for', () => {
    const names = [...AI_EVAL_READ_HOOK_NAMES, ...AI_EVAL_MUTATION_HOOK_NAMES].sort()
    expect(Object.keys(hooks).sort()).toEqual(names)
    const stub = aiEvalHookMocks()
    expect(Object.keys(stub).sort()).toEqual(names)
    expect(stub.useShadowPairs()).toMatchObject({ data: undefined, error: undefined, isLoading: false })
    for (const name of AI_EVAL_MUTATION_HOOK_NAMES) expect(typeof stub[name]()).toBe('function')
  })

  it.each<[string, () => unknown]>([
    ['gate history, disabled', () => hooks.useAgentStackGateRuns(false)],
    ['a manifest without a checksum', () => hooks.useEvalManifest(null)],
    ['candidate options for a non-admin', () => hooks.useCandidateOptions(false)],
    ['runs, disabled', () => hooks.useEvalRuns({ dataset_id: 'dsA1' }, false)],
    ['a run without an id', () => hooks.useEvalRun(null)],
    ['items for a non-admin', () => hooks.useEvalRunItems('rnA1', {}, false)],
    ['items without a run', () => hooks.useEvalRunItems(null, {}, true)],
    ['G2 history, no project', () => hooks.useTierComparisonRuns(null)],
    ['G2 history, All Projects', () => hooks.useTierComparisonRuns('all')],
    ['G3 history, no project', () => hooks.useReviewerQualityRuns(null)],
    ['G3 history, All Projects', () => hooks.useReviewerQualityRuns('all')],
    ['shadow pairs, no project', () => hooks.useShadowPairs(null)],
    ['shadow pairs, All Projects', () => hooks.useShadowPairs('all')],
  ])('%s: no key, so no request', (_name, use) => {
    renderHook(use)
    expect(lastCall().key).toBeNull()
    for (const fn of Object.values(service)) expect(fn).not.toHaveBeenCalled()
  })

  it.each<[string, () => unknown, unknown[], string, unknown[]]>([
    ['gate history', () => hooks.useAgentStackGateRuns(true), ['ai-eval', 'gate-runs'],
      'listAgentStackGateRuns', [{ limit: 20 }]],
    ['a manifest', () => hooks.useEvalManifest('cafe12'), ['ai-eval', 'manifest', 'cafe12'],
      'getEvalManifest', ['cafe12']],
    ['candidate options', () => hooks.useCandidateOptions(true), ['ai-eval', 'candidate-options'],
      'getCandidateOptions', []],
    ['a dataset’s runs', () => hooks.useEvalRuns({ dataset_id: 'dsA1' }, true),
      ['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined], 'listEvalRuns',
      [{ dataset_id: 'dsA1', task_type: undefined, metric_kind: undefined, status: undefined, limit: 20 }]],
    ['a gate’s candidate runs', () => hooks.useEvalRuns(
      { task_type: 'classification', metric_kind: 'replay', status: 'completed', limit: 20 }, true,
    ), ['ai-eval', 'runs', undefined, 'classification', 'replay', 'completed'], 'listEvalRuns',
      [{ dataset_id: undefined, task_type: 'classification', metric_kind: 'replay', status: 'completed', limit: 20 }]],
    ['runs with another limit', () => hooks.useEvalRuns({ dataset_id: 'dsA1', limit: 50 }, true),
      ['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined, 50], 'listEvalRuns',
      [{ dataset_id: 'dsA1', task_type: undefined, metric_kind: undefined, status: undefined, limit: 50 }]],
    ['one run', () => hooks.useEvalRun('rnA1'), ['ai-eval', 'run', 'rnA1'], 'getEvalRun', ['rnA1']],
    ['a run’s items, first page', () => hooks.useEvalRunItems('rnA1', {}, true),
      ['ai-eval', 'run-items', 'rnA1', undefined, 25, 0], 'listEvalRunItems',
      ['rnA1', { status: undefined, limit: 25, offset: 0 }]],
    ['a run’s incorrect items, page 2', () => hooks.useEvalRunItems('rnA1', { status: 'incorrect', offset: 25 }, true),
      ['ai-eval', 'run-items', 'rnA1', 'incorrect', 25, 25], 'listEvalRunItems',
      ['rnA1', { status: 'incorrect', limit: 25, offset: 25 }]],
    ['G2 history', () => hooks.useTierComparisonRuns(P), ['ai-eval', 'tier-runs', P, undefined],
      'listTierComparisonRuns', [P, { agent_id: undefined, limit: 20 }]],
    ['G3 history', () => hooks.useReviewerQualityRuns(P), ['ai-eval', 'reviewer-runs', P],
      'listReviewerQualityRuns', [P, { limit: 20 }]],
    ['pending pairs', () => hooks.useShadowPairs(P), ['ai-eval', 'shadow-pairs', P, undefined, 'pending'],
      'listShadowPairs', [P, { agent_id: undefined, label_status: 'pending', limit: 25, offset: 0 }]],
    ['one agent’s pairs, page 2', () => hooks.useShadowPairs(P, 'agent.summary.v1', 'all', 25),
      ['ai-eval', 'shadow-pairs', P, 'agent.summary.v1', 'all', 25], 'listShadowPairs',
      [P, { agent_id: 'agent.summary.v1', label_status: 'all', limit: 25, offset: 25 }]],
  ])('%s: its key and its query', async (_name, use, key, fn, args) => {
    renderHook(use)
    const call = lastCall()
    expect(call.key).toEqual(key)
    expect(call.options).toMatchObject({ revalidateOnFocus: false })
    await call.fetcher()
    expect(service[fn as keyof typeof service].mock.calls).toEqual([args])
  })

  it('the Tiers badge and the queue’s default view read ONE key', () => {
    renderHook(() => hooks.useShadowPairs(P, undefined, 'pending'))
    const badge = lastCall().key
    renderHook(() => hooks.useShadowPairs(P))
    expect(lastCall().key).toEqual(badge)
  })

  describe('the runs polls', () => {
    const LIST_KEY = JSON.stringify(['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined])
    const RUN_KEY = JSON.stringify(['ai-eval', 'run', 'rnA1'])

    it.each<[string, unknown, number]>([
      ['nothing cached yet', undefined, 0],
      ['a queued run', { items: [{ status: 'completed' }, { status: 'queued' }], total: 2 }, REALTIME],
      ['a running run', { items: [{ status: 'running' }], total: 1 }, REALTIME],
      ['every run terminal', { items: [{ status: 'completed' }, { status: 'partial' }, { status: 'failed' }], total: 3 }, 0],
      ['a legacy row without a status', { items: [{ id: 'rnL1' }], total: 1 }, 0],
      ['an answer without items (a bare list, a catch-all)', [], 0],
    ])('the list with %s: its poll interval', (_name, data, interval) => {
      if (data !== undefined) cached.set(LIST_KEY, data)
      renderHook(() => hooks.useEvalRuns({ dataset_id: 'dsA1' }, true))
      expect(lastCall().options).toMatchObject({ refreshInterval: interval })
    })

    it('the list stops polling when its run turns terminal', () => {
      cached.set(LIST_KEY, { items: [{ status: 'running' }], total: 1 })
      const { rerender } = renderHook(() => hooks.useEvalRuns({ dataset_id: 'dsA1' }, true))
      expect(lastCall().options.refreshInterval).toBe(REALTIME)
      cached.set(LIST_KEY, { items: [{ status: 'completed' }], total: 1 })
      rerender()
      expect(lastCall().options.refreshInterval).toBe(0)
    })

    it('the poll that sees the last run finish revalidates the dashboard (Overview’s recent runs), nothing else', () => {
      cached.set(LIST_KEY, { items: [{ status: 'running' }], total: 1 })
      renderHook(() => hooks.useEvalRuns({ dataset_id: 'dsA1' }, true))
      lastCall().options.onSuccess({ items: [{ status: 'completed' }], total: 1 })
      expect(mutateSpy).toHaveBeenCalledTimes(1)
      expect(matcher()(['ai-eval/dashboard', 30])).toBe(true)
      expect(matcher()(['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined])).toBe(false)
      expect(matcher()('ai-eval/baselines')).toBe(false)
    })

    it.each<[string, unknown, unknown]>([
      ['a run still running', { items: [{ status: 'running' }], total: 1 }, { items: [{ status: 'running' }], total: 1 }],
      ['a list that loads already terminal', undefined, { items: [{ status: 'completed' }], total: 1 }],
      ['a terminal list re-read', { items: [{ status: 'completed' }], total: 1 }, { items: [{ status: 'completed' }], total: 1 }],
    ])('%s: the dashboard is not revalidated', (_name, before, answer) => {
      if (before !== undefined) cached.set(LIST_KEY, before)
      renderHook(() => hooks.useEvalRuns({ dataset_id: 'dsA1' }, true))
      lastCall().options.onSuccess(answer)
      expect(mutateSpy).not.toHaveBeenCalled()
    })

    it.each<[string, unknown, number]>([
      ['queued', { status: 'queued' }, REALTIME],
      ['running', { status: 'running' }, REALTIME],
      ['completed', { status: 'completed' }, 0],
      ['partial', { status: 'partial' }, 0],
    ])('one %s run: its poll interval', (_name, data, interval) => {
      cached.set(RUN_KEY, data)
      renderHook(() => hooks.useEvalRun('rnA1'))
      expect(lastCall().options).toMatchObject({ refreshInterval: interval })
    })
  })

  describe('the mutations', () => {
    it('Evaluate candidate posts, then revalidates every runs list (and nothing else)', async () => {
      service.evaluateCandidate.mockResolvedValue({ run_id: 'rnA1', status: 'queued' })
      const { result } = renderHook(() => hooks.useEvaluateCandidate())
      const body = { candidate: { tier: 'rules' }, project_id: P, limit: 12 }
      await expect(result.current('dsA1', body)).resolves.toEqual({ run_id: 'rnA1', status: 'queued' })
      expect(service.evaluateCandidate).toHaveBeenCalledWith('dsA1', body)
      expect(mutateSpy).toHaveBeenCalledTimes(1)
      expect(matcher()(['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined])).toBe(true)
      expect(matcher()(['ai-eval', 'runs', undefined, 'classification', 'replay', 'completed'])).toBe(true)
      expect(matcher()(['ai-eval', 'shadow-pairs', P, undefined, 'pending'])).toBe(false)
      expect(matcher()('ai-eval/baselines')).toBe(false)
    })

    it('a refused Evaluate revalidates nothing and rethrows (the form shows the message)', async () => {
      service.evaluateCandidate.mockRejectedValue(new Error('candidate_tier_unsupported'))
      const { result } = renderHook(() => hooks.useEvaluateCandidate())
      await expect(result.current('dsA1', { candidate: { tier: 'ml' }, project_id: P })).rejects.toThrow()
      expect(mutateSpy).not.toHaveBeenCalled()
    })

    it('Set as baseline revalidates the page’s baselines read and the runs', async () => {
      service.setBaseline.mockResolvedValue({ id: 'blA1' })
      const { result } = renderHook(() => hooks.useSetBaseline())
      await result.current({ task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: 'rnA1' })
      expect(service.setBaseline).toHaveBeenCalledWith({
        task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: 'rnA1',
      })
      const matchers = mutateSpy.mock.calls.map((c) => c[0] as (key: unknown) => boolean)
      const hit = (key: unknown) => matchers.some((m) => m(key))
      expect(hit('ai-eval/baselines')).toBe(true)
      expect(hit(['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined])).toBe(true)
      expect(hit(['ai-eval', 'run', 'rnA1'])).toBe(true)
      expect(hit(['ai-eval/dashboard', 30])).toBe(false)
    })

    it('a label revalidates the project’s pairs AND its G2 history, never another project’s', async () => {
      service.labelShadowPair.mockResolvedValue({ id: 'spA1', label_status: 'human_labelled' })
      const { result } = renderHook(() => hooks.useLabelShadowPair(P))
      await result.current('spA1', { verdict: 'candidate' })
      expect(service.labelShadowPair).toHaveBeenCalledWith('spA1', { verdict: 'candidate' })
      const matchers = mutateSpy.mock.calls.map((c) => c[0] as (key: unknown) => boolean)
      const hit = (key: unknown) => matchers.some((m) => m(key))
      expect(hit(['ai-eval', 'shadow-pairs', P, undefined, 'pending'])).toBe(true)
      expect(hit(['ai-eval', 'shadow-pairs', P, 'agent.summary.v1', 'pending', 25])).toBe(true)
      expect(hit(['ai-eval', 'tier-runs', P, undefined])).toBe(true)
      expect(hit(['ai-eval', 'shadow-pairs', 'prOther', undefined, 'pending'])).toBe(false)
      expect(hit(['ai-eval', 'runs', 'dsA1', undefined, undefined, undefined])).toBe(false)
    })

    it('a label in All Projects is refused before any request', async () => {
      const { result } = renderHook(() => hooks.useLabelShadowPair('all'))
      await expect(result.current('spA1', { verdict: 'both' })).rejects.toThrow(/one project/)
      expect(service.labelShadowPair).not.toHaveBeenCalled()
    })
  })
})
