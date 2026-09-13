import type {
  AgentPolicy,
  AgentPolicyListResponse,
  AgentPolicyUpdate,
  AgentRunListResponse,
} from '@/types/investigator'
import type { AgentConfigDocument, AgentConfigListResponse, AgentConfigView } from '@/types/agentConfig'
import { getData, putData } from './http'

/**
 * Agent governance (AI-3) — per-project agent policies (trust-ladder mode +
 * budgets) and the agent-activity ledger. Pinned Wave-B contract.
 *
 * E4.3 adds the per-agent configuration resource (agent-configs), which
 * replaces agent-policies once E4.4 migrates the policy rows.
 */
export const agentGovernanceService = {
  listPolicies: (projectId: string) =>
    getData<AgentPolicyListResponse>(`/api/v1/projects/${projectId}/agent-policies`),

  updatePolicy: (projectId: string, agentId: string, update: AgentPolicyUpdate) =>
    putData<AgentPolicy, AgentPolicyUpdate>(
      `/api/v1/projects/${projectId}/agent-policies/${agentId}`,
      update,
    ),

  listAgentConfigs: (projectId: string) =>
    getData<AgentConfigListResponse>(`/api/v1/projects/${projectId}/agent-configs`),

  /** Replaces the whole document (QA_LEAD+); the server bumps config_version. */
  updateAgentConfig: (projectId: string, agentId: string, config: AgentConfigDocument) =>
    putData<AgentConfigView, AgentConfigDocument>(
      `/api/v1/projects/${projectId}/agent-configs/${agentId}`,
      config,
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
