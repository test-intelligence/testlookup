import useSWR from 'swr'
import { mfaService } from '@/services/mfaService'
import type { MfaPolicy, MfaPolicyUpdate } from '@/types/mfa'

export const MFA_POLICY_KEY = '/api/v1/settings/mfa-policy'

/**
 * Workspace MFA policy. Readable by QA_LEAD and above; writable by ADMIN only
 * (the backend enforces both — the in-component gate is a courtesy, not the
 * security boundary).
 */
export function useMfaPolicy(enabled = true) {
  return useSWR<MfaPolicy>(enabled ? MFA_POLICY_KEY : null, () => mfaService.getPolicy(), {
    revalidateOnFocus: false,
  })
}

/**
 * PUT the policy. Only the fields you send are changed — the backend merges
 * with `exclude_none=True`, which is why nulling `required_for_role` needs the
 * explicit `clear_required_for_role: true` flag rather than a null value.
 */
export function updateMfaPolicy(payload: MfaPolicyUpdate): Promise<MfaPolicy> {
  return mfaService.updatePolicy(payload)
}
