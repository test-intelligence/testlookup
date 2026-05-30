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
}

export interface FeatureFlagCreate {
  key: string
  description?: string
  enabled_global?: boolean
  enabled_projects?: string[]
  enabled_roles?: string[]
  rollout_percent?: number
}

export interface FeatureFlagUpdate {
  description?: string
  enabled_global?: boolean
  enabled_projects?: string[] | null
  enabled_roles?: string[] | null
  rollout_percent?: number
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
  status: (key: string, projectId?: string | null) => {
    const params = projectId ? { project_id: projectId } : undefined
    return getData<FeatureFlagStatus>(`/api/v1/feature-flags/${key}/status`, { params })
  },
}
