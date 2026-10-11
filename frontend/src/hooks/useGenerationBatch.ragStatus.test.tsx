/**
 * The RAG status the generate tab reads is the ACTIVE PROJECT's answer.
 *
 * `knowledge_rag` can be allow-listed to some projects. `GET /rag/status`
 * used to be asked with no project, so the server answered for "no project":
 * the tab said "Knowledge RAG is not enabled" in every allow-listed project
 * whose retrieve and generate calls worked. The hook now passes the project,
 * and caches per project.
 */
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useRagStatus } from './useGenerationBatch'
import { ragService } from '@/services/ragGenerationService'
import { api } from '@/services/api'

vi.mock('@/services/api', () => ({ api: { get: vi.fn() } }))

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
)

describe('useRagStatus', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset()
    vi.mocked(api.get).mockResolvedValue({ data: { enabled: true } })
  })

  it('asks the status for the active project, by project_id', async () => {
    const { result } = renderHook(() => useRagStatus('project-a'), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ enabled: true }))
    expect(api.get).toHaveBeenCalledWith('/api/v1/test-management/rag/status', {
      params: { project_id: 'project-a' },
    })
  })

  it('caches each project separately', async () => {
    const spy = vi.spyOn(ragService, 'getStatus')
    const { rerender } = renderHook(({ id }) => useRagStatus(id), {
      wrapper, initialProps: { id: 'project-a' as string | null },
    })
    await waitFor(() => expect(spy).toHaveBeenCalledWith('project-a'))
    rerender({ id: 'project-b' })
    await waitFor(() => expect(spy).toHaveBeenCalledWith('project-b'))
    spy.mockRestore()
  })

  it('without a project asks the deployment-wide status (no project_id)', async () => {
    renderHook(() => useRagStatus(null), { wrapper })
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(api.get).toHaveBeenCalledWith('/api/v1/test-management/rag/status', { params: undefined })
  })
})
