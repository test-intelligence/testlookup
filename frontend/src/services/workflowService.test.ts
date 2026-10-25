import { beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteData, getData, postData, putData } from './http'
import {
  clearWorkflowDefault,
  deleteWorkflow,
  evaluateWorkflow,
  forkWorkflow,
  getWorkflowDefaults,
  listWorkflows,
  publishWorkflow,
  setWorkflowDefault,
  updateWorkflow,
  validateWorkflow,
} from './workflowService'
import { readSourceFile } from '@/test/readSourceFile'
import type { WorkflowBody } from '@/types/workflowDefinition'

vi.mock('./http', () => ({
  deleteData: vi.fn(),
  getData: vi.fn(),
  postData: vi.fn(),
  putData: vi.fn(),
}))

const body: WorkflowBody = {
  workflow_id: 'wf.fast',
  name: 'Fast',
  description: null,
  base: 'offline',
  steps: [{ id: 'ingest', agent_id: 'agent.ingestion.v1', tools: [], reviews: [] }],
  edges: [],
  loops: [],
  retry_policy: { max_attempts: 5, base_seconds: 30, cap_seconds: 600 },
  review_policy: 'human_required',
  deadline_seconds: 1500,
}

describe('workflowService', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the project-scoped workflow contract for every editor operation', async () => {
    vi.mocked(getData).mockResolvedValue({ workflows: [] })
    vi.mocked(putData).mockResolvedValue({})
    vi.mocked(postData).mockResolvedValue({})

    await listWorkflows('p1')
    await updateWorkflow('p1', 'wf.fast', body)
    await validateWorkflow('p1', 'wf.fast', 2)
    await forkWorkflow('p1', 'offline', { workflow_id: 'wf.fast', name: 'Fast' }, 1)
    await evaluateWorkflow('p1', 'wf.fast', 2)
    await publishWorkflow('p1', 'wf.fast', {
      version: 2,
      definition_sha256: 'a'.repeat(64),
      accept_regression: true,
      reason: 'approved',
      eval_manifest_checksum: 'b'.repeat(64),
    })

    expect(getData).toHaveBeenCalledWith('/api/v1/projects/p1/workflows')
    expect(putData).toHaveBeenCalledWith('/api/v1/projects/p1/workflows/wf.fast', body)
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/wf.fast/validate', undefined, { params: { version: 2 } },
    )
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/offline/fork',
      { workflow_id: 'wf.fast', name: 'Fast' },
      { params: { version: 1 } },
    )
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/wf.fast/evaluate', { version: 2, sample_limit: 100 },
    )
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/wf.fast/publish',
      {
        version: 2,
        definition_sha256: 'a'.repeat(64),
        accept_regression: true,
        reason: 'approved',
        eval_manifest_checksum: 'b'.repeat(64),
      },
    )
  })

  it('W14 AGT-08: the defaults and Delete draft call their full paths, the writes without a global toast', async () => {
    vi.mocked(getData).mockResolvedValue({ project_id: 'p1', defaults: [] })
    vi.mocked(putData).mockResolvedValue({})
    vi.mocked(deleteData).mockResolvedValue(undefined)

    await getWorkflowDefaults('p1')
    await setWorkflowDefault('p1', 'offline', { workflow_id: 'wf.fast' })
    await setWorkflowDefault('p1', 'deep', { workflow_id: 'wf.deep', workflow_version: 3 })
    await clearWorkflowDefault('p1', 'offline')
    await deleteWorkflow('p1', 'wf.fast')

    expect(getData).toHaveBeenCalledWith('/api/v1/projects/p1/workflows/defaults')
    expect(putData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/defaults/offline', { workflow_id: 'wf.fast' }, { suppressToast: true },
    )
    expect(putData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/defaults/deep',
      { workflow_id: 'wf.deep', workflow_version: 3 },
      { suppressToast: true },
    )
    expect(deleteData).toHaveBeenCalledWith('/api/v1/projects/p1/workflows/defaults/offline', { suppressToast: true })
    expect(deleteData).toHaveBeenCalledWith('/api/v1/projects/p1/workflows/wf.fast', { suppressToast: true })
    expect(deleteData).toHaveBeenCalledTimes(2)
  })

  it('W14: every new path is written out in full, never through the base() helper', async () => {
    const source = await readSourceFile('src/services/workflowService.ts')
    const tail = source.slice(source.indexOf('// ── W14 AGT-08'))
    expect(tail).not.toContain('base(projectId)')
    for (const path of [
      '`/api/v1/projects/${projectId}/workflows/defaults`',
      '`/api/v1/projects/${projectId}/workflows/defaults/${base}`',
      '`/api/v1/projects/${projectId}/workflows/${encodeURIComponent(workflowId)}`',
    ]) {
      expect(tail).toContain(path)
    }
  })
})
