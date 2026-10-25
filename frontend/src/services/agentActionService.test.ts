/**
 * agentActionService (W15 AGT-01): the two request paths are the backend
 * contract (and the SPA wire-contract guard reads them), so they are pinned
 * here in full rather than only mocked away in the page tests.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', () => ({
  getData: vi.fn(),
  patchData: vi.fn(),
}))

import { getData, patchData } from './http'
import { agentActionService, decideAgentAction, listAgentActions } from './agentActionService'

describe('agentActionService', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists a project queue with the query as params', async () => {
    await agentActionService.listAgentActions('proj-1', { view: 'pending', limit: 200 })
    expect(getData).toHaveBeenCalledWith('/api/v1/projects/proj-1/agent-actions', {
      params: { view: 'pending', limit: 200 },
    })
  })

  it('lists one run’s rows by test_run_id', async () => {
    await listAgentActions('proj-1', { test_run_id: 'run-7', limit: 50 })
    expect(getData).toHaveBeenCalledWith('/api/v1/projects/proj-1/agent-actions', {
      params: { test_run_id: 'run-7', limit: 50 },
    })
  })

  it('sends no params beyond the query it is given', async () => {
    await listAgentActions('proj-1')
    expect(getData).toHaveBeenCalledWith('/api/v1/projects/proj-1/agent-actions', { params: {} })
  })

  it('decides one action by PATCH, encoding its id, without the shared toast', async () => {
    await agentActionService.decideAgentAction('proj-1', 'act/1 x', {
      status: 'rejected', reason_code: 'unsupported_claim', notes: 'No evidence',
    })
    expect(patchData).toHaveBeenCalledWith(
      '/api/v1/projects/proj-1/agent-actions/act%2F1%20x',
      { status: 'rejected', reason_code: 'unsupported_claim', notes: 'No evidence' },
      { suppressToast: true },
    )
  })

  it('approve and undo send only the status', async () => {
    await decideAgentAction('proj-1', 'act-1', { status: 'approved' })
    await decideAgentAction('proj-1', 'act-1', { status: 'rolled_back' })
    expect(vi.mocked(patchData).mock.calls.map((call) => call[1])).toEqual([
      { status: 'approved' }, { status: 'rolled_back' },
    ])
  })
})
