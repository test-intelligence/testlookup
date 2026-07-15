import type {
  AgentPolicy,
  AgentPolicyListResponse,
  AgentPolicyUpdate,
  AgentRunListResponse,
} from '@/types/investigator'
import { getData, putData } from './http'

/**
 * Agent governance (AI-3) — per-project agent policies (trust-ladder mode +
 * budgets) and the agent-activity ledger. Pinned Wave-B contract.
 */
export const agentGovernanceService = {
  listPolicies: (projectId: string) =>
    getData<AgentPolicyListResponse>(`/api/v1/projects/${projectId}/agent-policies`),

  updatePolicy: (projectId: string, agentId: string, update: AgentPolicyUpdate) =>
    putData<AgentPolicy, AgentPolicyUpdate>(
      `/api/v1/projects/${projectId}/agent-policies/${agentId}`,
      update,
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
