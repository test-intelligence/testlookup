/**
 * W23c AGT-10 / AGT-11: the review-gate and agent-invoke services, and their hooks through the REAL SWR.
 *
 * The request paths are the backend contract (the SPA wire-contract guard reads them), so each one is
 * pinned here in full, with its body; the PUT and the invoke pass `suppressToast: true` (the page / panel
 * shows the refusal inline); every invoke carries an `Idempotency-Key`, a fresh one per call unless the
 * caller passes the one minted for its Run click.
 *
 * Hooks: `useReviewGate` / `useAgentCatalog` ask nothing until enabled; `save` PUTs and caches the PUT's
 * own answer (no second GET). `useInvocation` polls ONLY while the invocation is `in_progress` — the
 * interval is a NUMBER from the cached answer, so polling starts on the first in-progress answer and stops
 * on `completed` (REALTIME is shortened to 40 ms here; the real value is pinned at 5 000).
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/config/refreshIntervals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/config/refreshIntervals')>()
  return { ...actual, REFRESH_INTERVALS: { ...actual.REFRESH_INTERVALS, REALTIME: 40 } }
})
const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { getCatalogAgent, getInvocation, invoke, listCatalog, newIdempotencyKey } from './agentInvokeService'
import { getReviewGate, putReviewGate } from './reviewGateService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { invocationRefreshInterval, useAgentCatalog, useInvocation } from '@/hooks/useAgentInvoke'
import { useReviewGate } from '@/hooks/useReviewGate'
import type { AgentInvokeBody } from '@/types/agentInvoke'
import {
  RG_INVOCATION_ID,
  RG_PROJECT_ID,
  RG_RUN_ID,
  gateScheduled,
  gateUnset,
  invocation,
  invocationAwaitingReview,
  invocationInProgress,
  mockCatalog,
} from '@/test/reviewGateInlineMocks'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

function body(): AgentInvokeBody {
  return {
    project_id: RG_PROJECT_ID,
    input: { agent_id: 'agent.summary.v1', payload: { test_run_id: RG_RUN_ID } },
    mode: 'async',
    inline_input: { schema_version: 1, failures: [{ test_name: 'checkout pays sentinel', confidence_score: 70 }] },
  }
}

beforeEach(() => {
  apiMock.get.mockReset().mockResolvedValue({ data: {} })
  apiMock.put.mockReset().mockResolvedValue({ data: {} })
  apiMock.post.mockReset().mockResolvedValue({ data: {} })
})

describe('reviewGateService', () => {
  it('GETs the literal path and returns the raw answer', async () => {
    const answer = gateUnset()
    apiMock.get.mockResolvedValue({ data: answer })
    await expect(getReviewGate()).resolves.toBe(answer)
    expect(apiMock.get).toHaveBeenCalledWith('/api/v1/settings/review-gate', undefined)
  })

  it('PUTs the date (or null) to the literal path with no toast', async () => {
    await putReviewGate({ enforced_from: '2026-11-02T09:00:00+01:00' })
    expect(apiMock.put).toHaveBeenCalledWith(
      '/api/v1/settings/review-gate', { enforced_from: '2026-11-02T09:00:00+01:00' }, { suppressToast: true },
    )
    await putReviewGate({ enforced_from: null })
    expect(apiMock.put).toHaveBeenLastCalledWith('/api/v1/settings/review-gate', { enforced_from: null }, { suppressToast: true })
  })
})

describe('agentInvokeService', () => {
  it('reads the catalog, one agent (id encoded) and one invocation by their literal paths', async () => {
    await listCatalog()
    expect(apiMock.get).toHaveBeenLastCalledWith('/api/v1/agents/catalog', undefined)
    await getCatalogAgent('agent.summary.v1')
    expect(apiMock.get).toHaveBeenLastCalledWith('/api/v1/agents/catalog/agent.summary.v1', undefined)
    await getCatalogAgent('a/b c')
    expect(apiMock.get).toHaveBeenLastCalledWith('/api/v1/agents/catalog/a%2Fb%20c', undefined)
    await getInvocation(RG_INVOCATION_ID)
    expect(apiMock.get).toHaveBeenLastCalledWith(`/api/v1/agents/invocations/${RG_INVOCATION_ID}`, undefined)
  })

  it('POSTs the body with the Idempotency-Key it was given, and no toast', async () => {
    const key = newIdempotencyKey()
    await invoke('agent.summary.v1', body(), key)
    expect(apiMock.post).toHaveBeenCalledWith('/api/v1/agents/agent.summary.v1/invoke', body(), {
      headers: { 'Idempotency-Key': key },
      suppressToast: true,
    })
  })

  it('mints a different Idempotency-Key per call when none is given', async () => {
    await invoke('agent.summary.v1', body())
    await invoke('agent.summary.v1', body())
    const keys = apiMock.post.mock.calls.map((call) => (call[2] as { headers: Record<string, string> }).headers['Idempotency-Key'])
    expect(keys).toHaveLength(2)
    expect(keys[0]).toMatch(UUID_RE)
    expect(keys[1]).toMatch(UUID_RE)
    expect(keys[0]).not.toBe(keys[1])
  })
})

describe('useReviewGate (real SWR)', () => {
  it('asks nothing while disabled (a non-admin never sees a 403 toast)', async () => {
    const { result } = renderHook(() => useReviewGate(false), { wrapper })
    await pause(30)
    expect(apiMock.get).not.toHaveBeenCalled()
    expect(result.current.data).toBeNull()
  })

  it('reads once; save PUTs and caches the PUT answer without a second GET', async () => {
    apiMock.get.mockResolvedValue({ data: gateUnset() })
    const scheduled = gateScheduled()
    apiMock.put.mockResolvedValue({ data: scheduled })
    const { result } = renderHook(() => useReviewGate(true), { wrapper })
    await waitFor(() => expect(result.current.data?.source).toBe('unset'))
    await act(async () => {
      await expect(result.current.save(scheduled.enforced_from)).resolves.toEqual(scheduled)
    })
    await waitFor(() => expect(result.current.data?.source).toBe('scheduled'))
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.put).toHaveBeenCalledWith(
      '/api/v1/settings/review-gate', { enforced_from: scheduled.enforced_from }, { suppressToast: true },
    )
  })

  it('save rethrows a refusal so the page can show it inline', async () => {
    apiMock.get.mockResolvedValue({ data: gateUnset() })
    const refusal = Object.assign(new Error('Request failed with status code 403'), {
      response: { status: 403, data: { detail: { code: 'interactive_only', message: 'refused' } } },
    })
    apiMock.put.mockRejectedValue(refusal)
    const { result } = renderHook(() => useReviewGate(true), { wrapper })
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(async () => {
      await expect(result.current.save(null)).rejects.toBe(refusal)
    })
    expect(result.current.data?.source).toBe('unset')
  })

  it('a malformed answer is an error, not a crash', async () => {
    apiMock.get.mockResolvedValue({ data: { source: 'sometime' } })
    const { result } = renderHook(() => useReviewGate(true), { wrapper })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.data).toBeNull()
  })
})

describe('useAgentCatalog (real SWR)', () => {
  it('reads the catalog only once the section is open', async () => {
    apiMock.get.mockResolvedValue({ data: mockCatalog() })
    const { result, rerender } = renderHook(({ open }) => useAgentCatalog(open), { wrapper, initialProps: { open: false } })
    await pause(30)
    expect(apiMock.get).not.toHaveBeenCalled()
    rerender({ open: true })
    await waitFor(() => expect(result.current.data?.[0]?.agent_id).toBe('agent.summary.v1'))
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.get).toHaveBeenCalledWith('/api/v1/agents/catalog', undefined)
  })
})

describe('useInvocation polling', () => {
  it('the interval is REALTIME only for in_progress (5 000 ms in the real config)', async () => {
    const real = await vi.importActual<typeof import('@/config/refreshIntervals')>('@/config/refreshIntervals')
    expect(real.REFRESH_INTERVALS.REALTIME).toBe(5_000)
    expect(invocationRefreshInterval(invocationInProgress())).toBe(REFRESH_INTERVALS.REALTIME)
    for (const status of ['completed', 'failed', 'passed'] as const) {
      expect(invocationRefreshInterval(invocation({ status }))).toBe(0)
    }
    expect(invocationRefreshInterval(undefined)).toBe(0)
    expect(invocationRefreshInterval(null)).toBe(0)
  })

  it('starts on an in-progress answer and stops once completed (real SWR)', async () => {
    const answers = [invocationInProgress(), invocationInProgress(), invocationAwaitingReview()]
    let call = 0
    apiMock.get.mockImplementation(async () => ({ data: answers[Math.min(call++, answers.length - 1)] }))
    const { result } = renderHook(() => useInvocation(RG_INVOCATION_ID), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('completed'), { timeout: 2000 })
    expect(apiMock.get).toHaveBeenCalledTimes(3)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/agents/invocations/${RG_INVOCATION_ID}`, undefined)
    await pause(200)
    expect(apiMock.get, 'completed: the poll stopped').toHaveBeenCalledTimes(3)
    expect(result.current.data?.input_kind).toBe('inline')
  })

  it('asks nothing without an id', async () => {
    renderHook(() => useInvocation(null), { wrapper })
    await pause(30)
    expect(apiMock.get).not.toHaveBeenCalled()
  })
})
