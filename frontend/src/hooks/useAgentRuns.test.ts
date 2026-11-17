/**
 * D-0020 (GPT-6): a run's pipelines are read by run, like every other read on the run page. The hook used to send the
 * selected project as well; with another of the member's projects selected the server intersected the two
 * (`200 []`) and the Evidence tab said no pipeline had analysed the run. The project-wide list keeps its project.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

const mocked = vi.hoisted(() => ({
  useSWR: vi.fn(),
  listPipelines: vi.fn(),
  activeProjectId: 'project-b' as string | null,
}))

vi.mock('swr', () => ({ default: mocked.useSWR }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (state: { activeProjectId: string | null }) => unknown) =>
    selector({ activeProjectId: mocked.activeProjectId }),
}))
vi.mock('@/services/agentService', () => ({ default: { listPipelines: mocked.listPipelines } }))

import { usePipelines } from './useAgentRuns'

/** The SWR key of the last render, and the arguments its fetcher passed to `listPipelines`. */
async function lastRead() {
  const renders = mocked.useSWR.mock.calls
  const [key, fetcher] = renders[renders.length - 1] as [string, () => Promise<unknown>]
  await fetcher()
  const reads = mocked.listPipelines.mock.calls
  return { key, args: reads[reads.length - 1] }
}

describe('usePipelines', () => {
  beforeEach(() => {
    mocked.useSWR.mockReset()
    mocked.listPipelines.mockReset().mockResolvedValue([])
    mocked.activeProjectId = 'project-b'
  })

  it("reads one run's pipelines by run, whichever project is selected (D-0020)", async () => {
    renderHook(() => usePipelines('run-1'))
    expect(await lastRead()).toEqual({ key: '/pipelines?run=run-1', args: ['run-1', undefined, undefined, 100] })
  })

  it("keeps the selected project on the project-wide list", async () => {
    renderHook(() => usePipelines())
    expect(await lastRead()).toEqual({
      key: '/pipelines?project=project-b&limit=100',
      args: [undefined, 'project-b', undefined, 100],
    })
  })

  it('sends no project in All Projects', async () => {
    mocked.activeProjectId = 'all'
    renderHook(() => usePipelines())
    expect(await lastRead()).toEqual({ key: '/pipelines?limit=100', args: [undefined, undefined, undefined, 100] })
  })
})
