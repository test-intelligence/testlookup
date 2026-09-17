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
    await agentGovernanceService.updateAgentConfig('proj-1', 'agent.summary.v1', document, 7)
    expect(putData).toHaveBeenCalledWith(
      '/api/v1/projects/proj-1/agent-configs/agent.summary.v1',
      document,
      { headers: { 'If-Match': '7' } },
    )
  })

  it('projects and updates Investigator policy through agent-configs only', async () => {
    const view = {
      config_version: 7,
      config: {
        agent_id: 'investigator', enabled: true, mode: 'shadow',
        extensions: {
          fixer: null,
          investigator: {
            budgets: { max_runs_per_day: 10, max_llm_calls_per_run: 30, max_tokens_per_run: 60000,
              max_seconds_per_run: 300 },
            shadow_runs_completed: 7,
            promotion_note: 'observed',
          },
        },
      },
    }
    ;(getData as ReturnType<typeof vi.fn>).mockResolvedValue(view)
    ;(putData as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...view,
      config: { ...view.config, enabled: false, mode: 'suggest' },
    })

    const listed = await agentGovernanceService.listPolicies('proj-1')
    expect(listed.policies[0]?.promotion).toEqual({ shadow_runs_completed: 7, note: 'observed' })
    await agentGovernanceService.updatePolicy('proj-1', 'investigator', {
      enabled: false,
      mode: 'suggest',
      budgets: { max_runs_per_day: 5, max_llm_calls_per_run: 12, max_tokens_per_run: 20000,
        max_seconds_per_run: 120 },
    })

    const path = '/api/v1/projects/proj-1/agent-configs/investigator'
    expect(getData).toHaveBeenCalledWith(path)
    expect(putData).toHaveBeenCalledWith(
      path,
      expect.objectContaining({
        enabled: false,
        mode: 'suggest',
        extensions: expect.objectContaining({ investigator: expect.objectContaining({
          shadow_runs_completed: 7,
          promotion_note: 'observed',
          budgets: expect.objectContaining({ max_runs_per_day: 5, max_seconds_per_run: 120 }),
        }) }),
      }),
      { headers: { 'If-Match': '7' } },
    )
    expect(String((putData as ReturnType<typeof vi.fn>).mock.calls[0]?.[0])).not.toContain('agent-policies')
  })
})
