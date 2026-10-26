import { deleteData, getData, patchData, postData } from './http'

export interface FeatureFlag {
  id: string
  key: string
  description: string | null
  enabled_global: boolean
  enabled_projects: string[] | null
  enabled_roles: string[] | null
  rollout_percent: number
  created_at: string
  updated_at: string
  updated_by_user_id: string | null
  /** W17 ADM-07: who last changed it (a name), and the reason given with that change. */
  updated_by_name?: string | null
  last_change_reason?: string | null
}

/** One change of a flag (W17 ADM-07): field names only, never values. */
export interface FeatureFlagHistoryEntry {
  action: string
  actor_name: string | null
  changed_fields: string[]
  reason: string | null
  created_at: string | null
}

export interface FeatureFlagCreate {
  key: string
  description?: string
  enabled_global?: boolean
  enabled_projects?: string[]
  enabled_roles?: string[]
  rollout_percent?: number
  reason?: string
}

export interface FeatureFlagUpdate {
  description?: string
  enabled_global?: boolean
  enabled_projects?: string[] | null
  enabled_roles?: string[] | null
  rollout_percent?: number
  /** W17 ADM-07: optional "why", up to 500 characters, kept on the audit row. */
  reason?: string
}

export interface FeatureFlagStatus {
  key: string
  enabled: boolean
}

export const featureFlagService = {
  list: () => getData<FeatureFlag[]>('/api/v1/feature-flags'),
  get: (key: string) => getData<FeatureFlag>(`/api/v1/feature-flags/${key}`),
  create: (payload: FeatureFlagCreate) =>
    postData<FeatureFlag>('/api/v1/feature-flags', payload),
  update: (key: string, payload: FeatureFlagUpdate) =>
    patchData<FeatureFlag>(`/api/v1/feature-flags/${key}`, payload),
  remove: (key: string) => deleteData(`/api/v1/feature-flags/${key}`),
  history: (key: string, limit = 20) =>
    getData<FeatureFlagHistoryEntry[]>(`/api/v1/feature-flags/${key}/history`, { params: { limit } }),
  status: (key: string, projectId?: string | null) => {
    const params = projectId ? { project_id: projectId } : undefined
    return getData<FeatureFlagStatus>(`/api/v1/feature-flags/${key}/status`, { params })
  },
}
