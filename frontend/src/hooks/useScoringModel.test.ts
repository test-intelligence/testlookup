/**
 * GATE-05: the scoring model is fetched for a project (or a run's project)
 * and cached per scope. The SWR key was the constant 'scoring-model', so a
 * project-scoped model would have been served to every other project.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/services/api', () => ({ api: { get } }))

import { runIntelligenceService } from '@/services/runIntelligenceService'
import { useScoringModel } from './useRunIntelligence'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

describe('runIntelligenceService.getScoringModel', () => {
  beforeEach(() => get.mockReset().mockResolvedValue({ data: { dimensions: [] } }))

  it('keeps the unscoped call exactly as it was', async () => {
    await runIntelligenceService.getScoringModel()
    expect(get).toHaveBeenCalledWith('/api/v1/scoring-model')
  })

  it('sends project_id, else run_id', async () => {
    await runIntelligenceService.getScoringModel({ projectId: 'p-1', runId: 'r-1' })
    expect(get).toHaveBeenLastCalledWith('/api/v1/scoring-model', { params: { project_id: 'p-1' } })
    await runIntelligenceService.getScoringModel({ runId: 'r-1' })
    expect(get).toHaveBeenLastCalledWith('/api/v1/scoring-model', { params: { run_id: 'r-1' } })
  })
})

describe('useScoringModel', () => {
  beforeEach(() => {
    get.mockReset().mockImplementation((_url: string, config?: { params?: Record<string, string> }) =>
      Promise.resolve({
        data: {
          version: 1,
          go_threshold: 20,
          no_go_threshold: 55,
          dimensions: [{ name: 'user_impact', weight: config?.params?.project_id === 'p-1' ? 0.4 : 0.25, description: 'd' }],
        },
      }),
    )
  })

  it('caches per project: two projects in one cache never share a model', async () => {
    const { result } = renderHook(
      () => ({ a: useScoringModel({ projectId: 'p-1' }), b: useScoringModel({ projectId: 'p-2' }) }),
      { wrapper },
    )
    await waitFor(() => {
      expect(result.current.a.scoringModel).toBeDefined()
      expect(result.current.b.scoringModel).toBeDefined()
    })

    expect(result.current.a.scoringModel?.dimensions[0].weight).toBe(0.4)
    expect(result.current.b.scoringModel?.dimensions[0].weight).toBe(0.25)
    expect(get).toHaveBeenCalledWith('/api/v1/scoring-model', { params: { project_id: 'p-1' } })
    expect(get).toHaveBeenCalledWith('/api/v1/scoring-model', { params: { project_id: 'p-2' } })
  })

  it('scopes by run when no project is known, and stays unscoped with neither', async () => {
    const run = renderHook(() => useScoringModel({ runId: 'r-1' }), { wrapper })
    await waitFor(() => expect(run.result.current.scoringModel).toBeDefined())
    expect(get).toHaveBeenCalledWith('/api/v1/scoring-model', { params: { run_id: 'r-1' } })

    const none = renderHook(() => useScoringModel(), { wrapper })
    await waitFor(() => expect(none.result.current.scoringModel).toBeDefined())
    expect(get).toHaveBeenCalledWith('/api/v1/scoring-model')
    expect(none.result.current.getDescription('user_impact')).toBe('d')
  })
})
