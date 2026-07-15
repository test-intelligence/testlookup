import useSWR from 'swr'
import { agentGovernanceService } from '@/services/agentGovernanceService'
import type { AgentPolicy, AgentPolicyListResponse, AgentRunListResponse } from '@/types/investigator'

/**
 * Agent-governance hooks (AI-3).
 *
 * Named `useAgentGovernance.ts` — NOT `useAgentRuns.ts` — because that file
 * already exists for the LangGraph pipeline views (usePipelines etc.); the
 * `useAgentRuns` hook here is the activity-LEDGER read (AgentRunEntry rows),
 * a different resource.
 */

export function useAgentPolicies(projectId: string | null) {
  const swr = useSWR<AgentPolicyListResponse>(
    projectId ? `/projects/${projectId}/agent-policies` : null,
    () => agentGovernanceService.listPolicies(projectId ?? ''),
    { revalidateOnFocus: false },
  )
  return {
    ...swr,
    policies: swr.data?.policies ?? [],
    /** Convenience: the Investigator agent's policy, when loaded. */
    investigatorPolicy: swr.data?.policies.find((p): p is AgentPolicy => p.agent_id === 'investigator') ?? null,
  }
}

export function useAgentRuns(
  projectId: string | null,
  opts: { agentId?: string; limit?: number; offset?: number } = {},
) {
  const { agentId, limit = 50, offset = 0 } = opts
  return useSWR<AgentRunListResponse>(
    projectId
      ? `/projects/${projectId}/agent-runs?agent_id=${agentId ?? ''}&limit=${limit}&offset=${offset}`
      : null,
    () => agentGovernanceService.listAgentRuns(projectId ?? '', { agentId, limit, offset }),
    { revalidateOnFocus: false },
  )
}
