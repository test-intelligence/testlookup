import useSWR from 'swr'
import { appMutate } from '@/utils/swrCacheMutate'
import { agentGovernanceService } from '@/services/agentGovernanceService'
import type { AgentPolicy, AgentPolicyListResponse, AgentRunListResponse } from '@/types/investigator'
import type { AgentConfigListResponse } from '@/types/agentConfig'

/**
 * Agent-governance hooks (AI-3).
 *
 * Named `useAgentGovernance.ts` — NOT `useAgentRuns.ts` — because that file
 * already exists for the LangGraph pipeline views (usePipelines etc.); the
 * `useAgentRuns` hook here is the activity-LEDGER read (AgentRunEntry rows),
 * a different resource.
 */

/**
 * Revalidate every key holding a project's agent governance.
 *
 * One AgentConfig document is read through two SWR keys:
 * `/projects/{id}/agent-configs/investigator` (the policy cards) and
 * `/projects/{id}/agent-configs` (the config panel). Both are rendered on the
 * SAME screen — the panel sits directly under the cards — and each save
 * refreshed only its own key, so the page showed one agent's configuration
 * twice with two different answers.
 *
 * The second-order effect is worse than the display mismatch: `AgentConfigPanel`
 * sends `If-Match: "<config_version>"` from its cache, so after the other half
 * saved, the panel's NEXT save was rejected on a version precondition the user
 * has no way to see.
 *
 * The policy key is a path extension of the config key, so one prefix matcher
 * covers both. `appMutate`, not the `swr` module's mutate: this app supplies
 * its own cache provider (see `utils/swrCacheMutate`).
 */
export function refreshAgentGovernance(projectId: string | null) {
  if (!projectId) return Promise.resolve(undefined)
  const prefix = `/projects/${projectId}/agent-configs`
  return appMutate((key) => typeof key === 'string' && key.startsWith(prefix))
}


export function useAgentPolicies(projectId: string | null) {
  const swr = useSWR<AgentPolicyListResponse>(
    projectId ? `/projects/${projectId}/agent-configs/investigator` : null,
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

/** Every configurable agent's configuration for a project (E4.3), defaults included. */
export function useAgentConfigs(projectId: string | null) {
  const swr = useSWR<AgentConfigListResponse>(
    projectId ? `/projects/${projectId}/agent-configs` : null,
    () => agentGovernanceService.listAgentConfigs(projectId ?? ''),
    { revalidateOnFocus: false },
  )
  return {
    ...swr,
    configs: swr.data?.configs ?? [],
    tools: swr.data?.tools ?? {},
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
