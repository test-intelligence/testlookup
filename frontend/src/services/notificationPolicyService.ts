/**
 * NTF-02: the project's notification policy (`/api/v1/notifications/projects/{id}/transition-policy`).
 *
 * Its own module, NOT `notificationService.ts`: that module is fixed-factory
 * mocked in two test files, where a function added to it would be undefined.
 *
 * Both calls opt out of the global error toast: the policy card renders a
 * failed read (a sentence and Retry) and a refused save (the server's detail)
 * itself, and My notifications reads the policy only for its hints, which a
 * failed read simply leaves out.
 */
import type { TransitionPolicyBody } from '@/types/notifications'
import { getData, putData } from './http'

const policyPath = (projectId: string) =>
  `/api/v1/notifications/projects/${encodeURIComponent(projectId)}/transition-policy`

/** The raw answer: read it through `parseTransitionPolicy` (types/notifications.ts). */
export function getTransitionPolicy(projectId: string): Promise<unknown> {
  return getData<unknown>(policyPath(projectId), { suppressToast: true })
}

/** Replaces the whole policy (QA lead and above, a member of the project). */
export function updateTransitionPolicy(projectId: string, body: TransitionPolicyBody): Promise<unknown> {
  return putData<unknown, TransitionPolicyBody>(policyPath(projectId), body, { suppressToast: true })
}
