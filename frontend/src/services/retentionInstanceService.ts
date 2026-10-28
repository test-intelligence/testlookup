import type {
  InstanceEnablePreview,
  InstanceEnableRequest,
  InstanceRetentionPolicy,
  InstanceRetentionPolicyWrite,
} from '@/types/retentionExport'
import { getData, postData, putData } from './http'

/**
 * The instance retention defaults (W22b ACT-06, Settings › Retention › Instance defaults).
 *
 * Every retention field a project leaves unset inherits these, field by field. ADMIN only on the server; the two
 * writes need an interactive login. Turning the default ON is only `enableInstancePolicy` with the preview's hash and
 * the exact typed phrase (D12); a PUT with `enabled: true` is refused, `enabled: false` switches it off. Each request
 * path is written out in full in its call (the SPA wire-contract guard reads the literals).
 */
export const retentionInstanceService = {
  getInstancePolicy: () => getData<InstanceRetentionPolicy>('/api/v1/settings/retention-defaults'),

  updateInstancePolicy: (body: InstanceRetentionPolicyWrite) =>
    putData<InstanceRetentionPolicy, InstanceRetentionPolicyWrite>('/api/v1/settings/retention-defaults', body),

  /** Read-only: every project the enable would switch on, the hash and the phrase. */
  previewInstanceEnable: () =>
    getData<InstanceEnablePreview>('/api/v1/settings/retention-defaults/enable-preview'),

  enableInstancePolicy: (body: InstanceEnableRequest) =>
    postData<InstanceRetentionPolicy, InstanceEnableRequest>('/api/v1/settings/retention-defaults/enable', body),
}
