/**
 * agentGovernanceService agent-config calls (E4.3): the URLs and the PUT body
 * are the backend contract, so they are pinned here rather than only mocked
 * away in the panel tests.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', () => ({
  getData: vi.fn(),
  putData: vi.fn(),
}))

import { getData, putData } from './http'
import { agentGovernanceService } from './agentGovernanceService'
import type { AgentConfigDocument } from '@/types/agentConfig'

describe('agentGovernanceService agent configs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lists every agent configuration of a project', async () => {
    await agentGovernanceService.listAgentConfigs('proj-1')
    expect(getData).toHaveBeenCalledWith('/api/v1/projects/proj-1/agent-configs')
  })

  it('PUTs one agent configuration document to its agent', async () => {
    const document = { agent_id: 'agent.summary.v1', mode: 'suggest' } as unknown as AgentConfigDocument
    await agentGovernanceService.updateAgentConfig('proj-1', 'agent.summary.v1', document)
    expect(putData).toHaveBeenCalledWith('/api/v1/projects/proj-1/agent-configs/agent.summary.v1', document)
  })
})
