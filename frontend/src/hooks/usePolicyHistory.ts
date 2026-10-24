import useSWR from 'swr'

import { getPolicyHistory, getSystemPolicyHistory, type PolicySummary } from '@/services/policyService'

/**
 * W11 GATE-02: every version of a policy's scope, newest first, with the names
 * of who created / activated each (the history routes fill them). A project
 * policy reads `GET /release-gate-policies/history/{project_id}`, the system
 * default `/system-history`. `enabled` false (the Versions disclosure closed)
 * → no key, no request: the editor's load requests are unchanged.
 */
export function usePolicyHistory(projectId: string | null | undefined, enabled: boolean) {
  return useSWR<PolicySummary[]>(
    enabled ? ['policy-history', projectId ?? 'system'] : null,
    () => (projectId ? getPolicyHistory(projectId) : getSystemPolicyHistory()),
    { revalidateOnFocus: false },
  )
}
