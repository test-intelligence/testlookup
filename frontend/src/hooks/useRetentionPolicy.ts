import useSWR from 'swr'
import { retentionService } from '@/services/retentionService'
import type {
  RetentionPolicy,
  RetentionPolicyWrite,
  RetentionPreview,
  RetentionPurgeResponse,
} from '@/types/retention'

/**
 * Per-project retention policy (Settings → Retention & Purge, US-11.4).
 *
 * SWR read of the pinned retention contract. The backend returns contract
 * defaults even when unconfigured, so this never 404s for a valid project;
 * the key is null (no fetch) in All-Projects mode.
 */
export function useRetentionPolicy(projectId: string | null) {
  // Narrow once into a const so the closure keeps the non-null type — no
  // unreachable `?? ''` fallback in the fetcher.
  const id = projectId
  return useSWR<RetentionPolicy>(
    id === null ? null : `/api/v1/projects/${id}/retention-policy`,
    id === null ? null : () => retentionService.get(id),
    { revalidateOnFocus: false },
  )
}

/**
 * Imperative helpers — not SWR resources (they are side-effecting POST/PUT
 * mutations), so they are exposed as thin async wrappers that the page calls
 * on button press. The page mutates the SWR cache with the PUT response.
 */
export function updateRetentionPolicy(
  projectId: string,
  payload: RetentionPolicyWrite,
): Promise<RetentionPolicy> {
  return retentionService.update(projectId, payload)
}

/** Dry-run purge preview — counts computed now; the nightly job may differ. */
export function previewRetentionPurge(projectId: string): Promise<RetentionPreview> {
  return retentionService.preview(projectId)
}

/** Queue a purge run (202). 409 when disabled; 422 on name mismatch. */
export function purgeRetentionNow(
  projectId: string,
  confirmationName: string,
): Promise<RetentionPurgeResponse> {
  return retentionService.purge(projectId, confirmationName)
}
