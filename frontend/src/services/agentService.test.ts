import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getData, postData } from './http'
import agentService, { newClientToken } from './agentService'

vi.mock('./http', () => ({
  getData: vi.fn(),
  postData: vi.fn(),
}))

describe('agentService (W14)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getData).mockResolvedValue({})
    vi.mocked(postData).mockResolvedValue({})
  })
  afterEach(() => vi.unstubAllGlobals())

  it('triggerPipeline sends workflow_id only when given and workflow_version only with an id', async () => {
    await agentService.triggerPipeline('run-1')
    await agentService.triggerPipeline('run-1', 'deep')
    await agentService.triggerPipeline('run-1', 'wf.my-fork', 3)
    await agentService.triggerPipeline('run-1', undefined, 3)
    await agentService.triggerPipeline('run-1', 'wf.my-fork', 0)

    const bodies = vi.mocked(postData).mock.calls.map(([url, body]) => [url, body])
    expect(bodies).toEqual([
      ['/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1' }],
      ['/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1', workflow_id: 'deep' }],
      ['/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1', workflow_id: 'wf.my-fork', workflow_version: 3 }],
      ['/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1' }],
      ['/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1', workflow_id: 'wf.my-fork', workflow_version: 0 }],
    ])
  })

  it('bulkTriggerPipelines keeps the one-argument call and mints a client token', async () => {
    await agentService.bulkTriggerPipelines(['run-4', 'run-3'])
    await agentService.bulkTriggerPipelines(['run-2'], 'deep')
    await agentService.bulkTriggerPipelines(['run-1'], 'offline', 'token_given-123')

    const calls = vi.mocked(postData).mock.calls
    expect(calls.map(([url]) => url)).toEqual(Array(3).fill('/api/v1/agents/pipelines/bulk-trigger'))
    const [first, second, third] = calls.map(([, body]) => body as Record<string, unknown>)
    expect(first).toEqual({ run_ids: ['run-4', 'run-3'], workflow_type: 'offline', client_token: expect.any(String) })
    expect(first.client_token).toMatch(/^[0-9a-f]{32}$/)
    expect(second).toMatchObject({ run_ids: ['run-2'], workflow_type: 'deep' })
    expect(second.client_token).not.toBe(first.client_token)
    expect(third).toEqual({ run_ids: ['run-1'], workflow_type: 'offline', client_token: 'token_given-123' })
    for (const [, , config] of calls) expect(config).toEqual({ suppressToast: true })
  })

  it('bulkTriggerFiltered posts the filter, the type and the token', async () => {
    const runFilter = { project_id: 'p1', status: 'failed', days: 7, branch: ['main'] }
    await agentService.bulkTriggerFiltered(runFilter, 'offline', 'tok-12345678')
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/agents/pipelines/bulk-trigger',
      { run_filter: runFilter, workflow_type: 'offline', client_token: 'tok-12345678' },
      { suppressToast: true },
    )
  })

  it('the bulk-job, replay, runtime and compare reads use their full paths with encoded ids', async () => {
    await agentService.getBulkJob('job/1')
    await agentService.cancelBulkJob('job/1')
    await agentService.getReplay('pipe 1')
    await agentService.getAgenticRuntime('pipe 1')
    await agentService.comparePipelines('pipe-a', 'pipe-b')

    expect(getData).toHaveBeenNthCalledWith(1, '/api/v1/agents/pipelines/bulk-jobs/job%2F1')
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/agents/pipelines/bulk-jobs/job%2F1/cancel', undefined, { suppressToast: true },
    )
    expect(getData).toHaveBeenNthCalledWith(2, '/api/v1/agents/pipelines/pipe%201/replay')
    expect(getData).toHaveBeenNthCalledWith(3, '/api/v1/agents/pipelines/pipe%201/agentic-runtime')
    expect(getData).toHaveBeenNthCalledWith(4, '/api/v1/agents/pipelines/compare', { params: { a: 'pipe-a', b: 'pipe-b' } })
  })

  it('newClientToken is 32 lower-case hex characters from getRandomValues, never randomUUID', () => {
    const randomUUID = vi.fn(() => {
      throw new Error('randomUUID exists only in secure contexts')
    })
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.forEach((_, index) => {
        bytes[index] = index * 17
      })
      return bytes
    })
    vi.stubGlobal('crypto', { getRandomValues, randomUUID })
    const token = newClientToken()
    expect(token).toBe('00112233445566778899aabbccddeeff')
    expect(token).toMatch(/^[0-9a-f]{32}$/)
    expect(getRandomValues).toHaveBeenCalledOnce()
    expect(randomUUID).not.toHaveBeenCalled()
  })
})
