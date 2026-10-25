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
      { headers: { 'If-Match': '"7"' } },
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
      { headers: { 'If-Match': '"7"' } },
    )
    expect(String((putData as ReturnType<typeof vi.fn>).mock.calls[0]?.[0])).not.toContain('agent-policies')
  })
})

describe('agentGovernanceService Act allowlist (W15 AGT-03)', () => {
  const path = '/api/v1/projects/proj-1/agent-configs/investigator'
  const budgets = { max_runs_per_day: 10, max_llm_calls_per_run: 30, max_tokens_per_run: 60000, max_seconds_per_run: 300 }
  const view = (version: number, act?: { allowed_action_types: string[] }) => ({
    config_version: version,
    config: {
      agent_id: 'investigator', enabled: true, mode: 'act',
      extensions: {
        fixer: null,
        investigator: {
          budgets, shadow_runs_completed: 3, promotion_note: null, ...(act ? { act } : {}),
        },
      },
    },
  })

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('updateInvestigatorAct re-reads the document and PUTs the ticks with its If-Match', async () => {
    vi.mocked(getData).mockResolvedValue(view(9, { allowed_action_types: [] }))
    vi.mocked(putData).mockResolvedValue(view(10, { allowed_action_types: ['quarantine.apply'] }))

    const saved = await agentGovernanceService.updateInvestigatorAct('proj-1', ['quarantine.apply'])

    expect(getData).toHaveBeenCalledWith(path)
    expect(putData).toHaveBeenCalledTimes(1)
    const [url, body, config] = vi.mocked(putData).mock.calls[0] ?? []
    expect(url).toBe(path)
    expect(config).toEqual({ headers: { 'If-Match': '"9"' } })
    // the whole document goes back as read; only the allowlist changes
    expect(body).toEqual({
      ...view(9).config,
      extensions: {
        fixer: null,
        investigator: {
          budgets, shadow_runs_completed: 3, promotion_note: null,
          act: { allowed_action_types: ['quarantine.apply'] },
        },
      },
    })
    expect(saved.act).toEqual({ allowed_action_types: ['quarantine.apply'] })
    expect(saved.mode).toBe('act')
  })

  it('a budget save keeps the allowlist (updatePolicy spreads the extension)', async () => {
    vi.mocked(getData).mockResolvedValue(view(4, { allowed_action_types: ['defect.promote'] }))
    vi.mocked(putData).mockResolvedValue(view(5, { allowed_action_types: ['defect.promote'] }))

    const saved = await agentGovernanceService.updatePolicy('proj-1', 'investigator', {
      enabled: true, mode: 'act', budgets: { ...budgets, max_runs_per_day: 2 },
    })

    const body = vi.mocked(putData).mock.calls[0]?.[1] as AgentConfigDocument
    expect(body.extensions?.investigator?.act).toEqual({ allowed_action_types: ['defect.promote'] })
    expect(body.extensions?.investigator?.budgets.max_runs_per_day).toBe(2)
    expect(saved.act).toEqual({ allowed_action_types: ['defect.promote'] })
  })

  it('listPolicies projects the allowlist, empty when the server sends none', async () => {
    vi.mocked(getData).mockResolvedValueOnce(view(1, { allowed_action_types: ['failure.reassign'] }))
    expect((await agentGovernanceService.listPolicies('proj-1')).policies[0]?.act).toEqual({
      allowed_action_types: ['failure.reassign'],
    })
    vi.mocked(getData).mockResolvedValueOnce(view(1))
    expect((await agentGovernanceService.listPolicies('proj-1')).policies[0]?.act).toEqual({
      allowed_action_types: [],
    })
  })
})
