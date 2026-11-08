import useSWR from 'swr'

import { getScimDeleteAction, type ScimDeleteActionSetting } from '@/services/ssoService'
import { subjectRightsService } from '@/services/subjectRightsService'
import type { ErasureRequestFilter, ErasureRequestList, MyErasureRequest } from '@/types/subjectRights'

/**
 * W38 (W31 F-1 / F-2): the erasure-request reads and the SCIM delete setting (SWR), beside `useSubjectRights.ts`.
 *
 * - `useMyErasureRequest()` — my NEWEST erasure request, or null (Settings › Profile › Your data).
 * - `useErasureRequests(status, enabled)` — the administrators' queue (Settings › Users › Erasure requests); NO
 *   request unless enabled (the tab is open and the reader is an ADMIN).
 * - `useScimDeleteAction(enabled)` — what a SCIM `DELETE` does (Settings › SSO › SCIM Tokens); NO request unless
 *   enabled (the SCIM tab is open and the reader is an ADMIN).
 *
 * None of them polls or revalidates on focus: the cards `mutate()` after their own POST / PUT.
 */

/** SWR keys, exported so a card can `mutate` them after a POST. */
export const myErasureRequestKey = () => 'subject-rights/me/erasure-request'
export const erasureRequestsKey = (status: ErasureRequestFilter) => `subject-rights/erasure-requests/${status}`
export const scimDeleteActionKey = () => 'settings/scim-delete-action'

export function useMyErasureRequest() {
  return useSWR<MyErasureRequest | null>(myErasureRequestKey(), () => subjectRightsService.getMyErasureRequest(), {
    revalidateOnFocus: false,
  })
}

export function useErasureRequests(status: ErasureRequestFilter, enabled = true) {
  return useSWR<ErasureRequestList>(
    enabled ? erasureRequestsKey(status) : null,
    () => subjectRightsService.listErasureRequests(status),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}

export function useScimDeleteAction(enabled = true) {
  return useSWR<ScimDeleteActionSetting>(enabled ? scimDeleteActionKey() : null, () => getScimDeleteAction(), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
}
