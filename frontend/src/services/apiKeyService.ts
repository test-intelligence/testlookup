import { deleteData, getData, postData } from './http'
import type { ApiKey, ApiKeyCreatePayload, ApiKeyCreatedResponse } from '@/types/apiKey'

export const apiKeyService = {
  list: (projectId?: string | null) =>
    getData<ApiKey[]>('/api/v1/keys', {
      params: projectId ? { project_id: projectId } : undefined,
    }),

  create: (payload: ApiKeyCreatePayload) =>
    postData<ApiKeyCreatedResponse, ApiKeyCreatePayload>('/api/v1/keys', payload),

  revoke: (keyId: string) => deleteData(`/api/v1/keys/${keyId}`),
}
