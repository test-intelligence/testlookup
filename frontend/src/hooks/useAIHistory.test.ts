/**
 * W03 AIH-04: the AI history hooks ask nothing until their ids exist (a null key: a closed tab, a run still loading
 * or two runs of different projects make no request), and pass every option through to the service.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiHistoryService } from '@/services/aiHistoryService'
import { useAICompare, useAIHistory, useAIVersion, useAIVersionDiff, usePreviousRun } from './useAIHistory'

vi.mock('@/services/aiHistoryService', () => ({
  aiHistoryService: {
    listVersions: vi.fn(async () => ({ versions: [] })),
    getVersion: vi.fn(async () => ({ version: 1 })),
    diffVersions: vi.fn(async () => ({ diff: {} })),
    getPreviousRun: vi.fn(async () => ({ baseline: null })),
    compare: vi.fn(async () => ({ diff: null })),
  },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } }, children)

beforeEach(() => vi.clearAllMocks())

describe('useAIHistory hooks', () => {
  it('ask nothing without their ids', async () => {
    renderHook(() => {
      useAIHistory(null)
      useAIVersion('run-1', null)
      useAIVersion(null, 'v1')
      useAIVersionDiff('run-1', null, 'live')
      useAIVersionDiff('run-1', 'v1', null)
      useAICompare(null, { baselineRunId: 'b' })
      usePreviousRun(undefined)
    }, { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    for (const fn of Object.values(aiHistoryService)) expect(fn).not.toHaveBeenCalled()
  })

  it('pass the run, the kind, the ids and every compare option to the service', async () => {
    const { result } = renderHook(() => ({
      list: useAIHistory('run-1', 'run_summary'),
      version: useAIVersion('run-1', 'v1'),
      diff: useAIVersionDiff('run-1', 'v1', 'live'),
      compare: useAICompare('run-1', { baselineRunId: 'b', suiteName: 'Auth', versionId: 'v2', baselineVersionId: 'bv' }),
      previous: usePreviousRun('run-1', 'Auth'),
    }), { wrapper })
    await waitFor(() => expect(result.current.compare.data).toBeDefined())
    expect(aiHistoryService.listVersions).toHaveBeenCalledWith('run-1', { kind: 'run_summary' })
    expect(aiHistoryService.getVersion).toHaveBeenCalledWith('run-1', 'v1')
    expect(aiHistoryService.diffVersions).toHaveBeenCalledWith('run-1', { from: 'v1', to: 'live' })
    expect(aiHistoryService.compare).toHaveBeenCalledWith('run-1', { baselineRunId: 'b', suiteName: 'Auth', versionId: 'v2', baselineVersionId: 'bv' })
    expect(aiHistoryService.getPreviousRun).toHaveBeenCalledWith('run-1', { suiteName: 'Auth' })
  })

  it('the version list defaults to the digest kind', async () => {
    renderHook(() => useAIHistory('run-1'), { wrapper })
    await waitFor(() => expect(aiHistoryService.listVersions).toHaveBeenCalledWith('run-1', { kind: 'analysis_digest' }))
  })
})
