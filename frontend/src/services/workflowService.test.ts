import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getData, postData, putData } from './http'
import {
  evaluateWorkflow,
  forkWorkflow,
  listWorkflows,
  publishWorkflow,
  updateWorkflow,
  validateWorkflow,
} from './workflowService'
import type { WorkflowBody } from '@/types/workflowDefinition'

vi.mock('./http', () => ({
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
      '/api/v1/projects/p1/workflows/wf.fast/evaluate', { version: 2, sample_limit: 20 },
    )
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/projects/p1/workflows/wf.fast/publish',
      {
        version: 2,
        definition_sha256: 'a'.repeat(64),
        accept_regression: true,
        reason: 'approved',
      },
    )
  })
})
