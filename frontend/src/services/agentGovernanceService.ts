import type {
  AgentPolicy,
  AgentPolicyListResponse,
  AgentPolicyUpdate,
  AgentRunListResponse,
} from '@/types/investigator'
import type { AgentConfigDocument, AgentConfigListResponse, AgentConfigView } from '@/types/agentConfig'
import { getData, putData } from './http'

const INVESTIGATOR_AGENT_ID = 'investigator'

/**
 * Agent governance (AI-3). AgentConfig is the writable source; the pinned
 * AgentPolicy shape is projected here while the server keeps a read-only alias.
 */
export const agentGovernanceService = {
  listPolicies: async (projectId: string): Promise<AgentPolicyListResponse> => {
    const view = await getData<AgentConfigView>(
      `/api/v1/projects/${projectId}/agent-configs/${INVESTIGATOR_AGENT_ID}`,
    )
    const extension = view.config.extensions?.investigator
    if (!extension) throw new Error('Investigator AgentConfig extension is missing')
    return {
      policies: [{
        agent_id: INVESTIGATOR_AGENT_ID,
        enabled: view.config.enabled,
        mode: view.config.mode,
        budgets: extension.budgets,
        promotion: {
          shadow_runs_completed: extension.shadow_runs_completed,
          note: extension.promotion_note,
        },
        act: { allowed_action_types: extension.act?.allowed_action_types ?? [] },
      }],
    }
  },

  updatePolicy: async (projectId: string, agentId: string, update: AgentPolicyUpdate): Promise<AgentPolicy> => {
    const path = `/api/v1/projects/${projectId}/agent-configs/${agentId}`
    const view = await getData<AgentConfigView>(path)
    const extension = view.config.extensions?.investigator
    if (!extension) throw new Error('Investigator AgentConfig extension is missing')
    const config: AgentConfigDocument = {
      ...view.config,
      enabled: update.enabled,
      mode: update.mode,
      extensions: {
        ...(view.config.extensions ?? { investigator: null, fixer: null }),
        investigator: { ...extension, budgets: { ...extension.budgets, ...update.budgets } },
      },
    }
    const saved = await putData<AgentConfigView, AgentConfigDocument>(path, config, {
      headers: { 'If-Match': `"${view.config_version}"` },
    })
    const savedExtension = saved.config.extensions?.investigator
    if (!savedExtension) throw new Error('Saved Investigator AgentConfig extension is missing')
    return {
      agent_id: agentId,
      enabled: saved.config.enabled,
      mode: saved.config.mode,
      budgets: savedExtension.budgets,
      promotion: {
        shadow_runs_completed: savedExtension.shadow_runs_completed,
        note: savedExtension.promotion_note,
      },
      act: { allowed_action_types: savedExtension.act?.allowed_action_types ?? [] },
    }
  },

  /**
   * W15 AGT-03: the action types Act may run in this project. The same
   * GET-then-PUT with `If-Match` as `updatePolicy` (a fresh read, so a save
   * right after `updatePolicy` carries the NEW `config_version`); every other
   * field of the document — mode, budgets — is sent back as read.
   */
  updateInvestigatorAct: async (projectId: string, allowedActionTypes: string[]): Promise<AgentPolicy> => {
    const view = await getData<AgentConfigView>(
      `/api/v1/projects/${projectId}/agent-configs/${INVESTIGATOR_AGENT_ID}`,
    )
    const extension = view.config.extensions?.investigator
    if (!extension) throw new Error('Investigator AgentConfig extension is missing')
    const config: AgentConfigDocument = {
      ...view.config,
      extensions: {
        ...(view.config.extensions ?? { investigator: null, fixer: null }),
        investigator: { ...extension, act: { allowed_action_types: [...allowedActionTypes] } },
      },
    }
    const saved = await putData<AgentConfigView, AgentConfigDocument>(
      `/api/v1/projects/${projectId}/agent-configs/${INVESTIGATOR_AGENT_ID}`,
      config,
      { headers: { 'If-Match': `"${view.config_version}"` } },
    )
    const savedExtension = saved.config.extensions?.investigator
    if (!savedExtension) throw new Error('Saved Investigator AgentConfig extension is missing')
    return {
      agent_id: INVESTIGATOR_AGENT_ID,
      enabled: saved.config.enabled,
      mode: saved.config.mode,
      budgets: savedExtension.budgets,
      promotion: {
        shadow_runs_completed: savedExtension.shadow_runs_completed,
        note: savedExtension.promotion_note,
      },
      act: { allowed_action_types: savedExtension.act?.allowed_action_types ?? [] },
    }
  },

  listAgentConfigs: (projectId: string) =>
    getData<AgentConfigListResponse>(`/api/v1/projects/${projectId}/agent-configs`),

  /** Replaces the whole document (QA_LEAD+); the server bumps config_version. */
  updateAgentConfig: (projectId: string, agentId: string, config: AgentConfigDocument, configVersion: number) =>
    putData<AgentConfigView, AgentConfigDocument>(
      `/api/v1/projects/${projectId}/agent-configs/${agentId}`,
      config,
      { headers: { 'If-Match': `"${configVersion}"` } },
    ),

  listAgentRuns: (
    projectId: string,
    opts: { agentId?: string; limit?: number; offset?: number } = {},
  ) =>
    getData<AgentRunListResponse>(`/api/v1/projects/${projectId}/agent-runs`, {
      params: {
        agent_id: opts.agentId,
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
      },
    }),
}

export default agentGovernanceService
