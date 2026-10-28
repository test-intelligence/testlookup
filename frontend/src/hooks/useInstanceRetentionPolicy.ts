import useSWR from 'swr'

import { retentionInstanceService } from '@/services/retentionInstanceService'
import type { InstanceEnablePreview, InstanceRetentionPolicy } from '@/types/retentionExport'

/**
 * The instance retention defaults (W22b ACT-06). The key is `null` — no request at all — until `enabled` (the
 * Instance defaults card is open), so the Retention page issues no instance read at load.
 */
export function useInstanceRetentionPolicy(enabled: boolean) {
  return useSWR<InstanceRetentionPolicy>(
    enabled ? '/api/v1/settings/retention-defaults' : null,
    () => retentionInstanceService.getInstancePolicy(),
    { revalidateOnFocus: false },
  )
}

/**
 * The D12 enable preview: every project switching the default on would affect, its hash and the typed phrase. A
 * read that writes nothing; the key is `null` until `enabled` (the admin asked to turn it on).
 */
export function useInstanceEnablePreview(enabled: boolean) {
  return useSWR<InstanceEnablePreview>(
    enabled ? '/api/v1/settings/retention-defaults/enable-preview' : null,
    () => retentionInstanceService.previewInstanceEnable(),
    { revalidateOnFocus: false },
  )
}
