import type {
  RetentionPolicy,
  RetentionPolicyWrite,
  RetentionPreview,
  RetentionPurgeResponse,
} from '@/types/retention'
import { getData, postData, putData } from './http'

/**
 * Retention & purge policy (PMF backlog US-11.4) — per-project data-retention
 * configuration, dry-run preview, and manual purge trigger.
 *
 * Pinned contract; PUT/preview/purge are ADMIN-only on the backend. The purge
 * POST returns 202 {queued: true} (the purge itself runs async and writes an
 * audit record), 409 when the policy is disabled, and 422 when the typed
 * confirmation name does not match the project name.
 *
 * Rides the shared Axios base in `services/api.ts` via the `http` helpers.
 */
export const retentionService = {
  get: (projectId: string) =>
    getData<RetentionPolicy>(`/api/v1/projects/${projectId}/retention-policy`),

  update: (projectId: string, payload: RetentionPolicyWrite) =>
    putData<RetentionPolicy, RetentionPolicyWrite>(
      `/api/v1/projects/${projectId}/retention-policy`,
      payload,
    ),

  preview: (projectId: string) =>
    postData<RetentionPreview>(
      `/api/v1/projects/${projectId}/retention-policy/preview`,
      {},
    ),

  purge: (projectId: string, confirmationName: string) =>
    postData<RetentionPurgeResponse, { confirmation_name: string }>(
      `/api/v1/projects/${projectId}/retention-policy/purge`,
      { confirmation_name: confirmationName },
    ),
}
