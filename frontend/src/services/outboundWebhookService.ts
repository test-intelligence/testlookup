import { deleteData, getData, patchData, postData } from './http'

export type WebhookEventType =
  | 'run.completed'
  | 'defect.promoted'
  | 'release.decided'
  | 'flaky.quarantined'
  | 'quota.exceeded'

export interface WebhookSubscriptionRead {
  id: string
  project_id: string
  name: string
  target_url: string
  events: WebhookEventType[]
  enabled: boolean
  has_secret: boolean
  max_retries: number
  last_delivered_at: string | null
  last_failure_at: string | null
  last_error: string | null
  failure_count: number
  total_delivered: number
  created_at: string
  updated_at: string
}

export interface WebhookSubscriptionWrite {
  name: string
  target_url: string
  events: WebhookEventType[]
  enabled: boolean
  max_retries: number
  secret?: string | null
}

export interface WebhookDeliveryRead {
  id: string
  subscription_id: string
  event_type: string
  event_payload: Record<string, unknown> | null
  status: 'PENDING' | 'SUCCESS' | 'FAILED' | 'DLQ'
  attempt_count: number
  http_status: number | null
  response_preview: string | null
  error: string | null
  delivered_at: string | null
  created_at: string
}

export interface WebhookEventCatalogEntry {
  event_type: WebhookEventType
  description: string
}

export interface WebhookEventCatalogResponse {
  events: WebhookEventCatalogEntry[]
}

export interface WebhookTestResponse {
  success: boolean
  status_code: number | null
  message: string
  latency_ms: number | null
}

export const outboundWebhookService = {
  events: () => getData<WebhookEventCatalogResponse>('/api/v1/webhooks/events'),

  list: (projectId?: string) =>
    getData<WebhookSubscriptionRead[]>(
      '/api/v1/webhooks',
      { params: projectId ? { project_id: projectId } : undefined },
    ),

  create: (projectId: string, payload: WebhookSubscriptionWrite) =>
    postData<WebhookSubscriptionRead>(
      '/api/v1/webhooks',
      payload,
      { params: { project_id: projectId } },
    ),

  get: (id: string) =>
    getData<WebhookSubscriptionRead>(`/api/v1/webhooks/${id}`),

  update: (id: string, payload: WebhookSubscriptionWrite) =>
    patchData<WebhookSubscriptionRead>(`/api/v1/webhooks/${id}`, payload),

  remove: (id: string) =>
    deleteData(`/api/v1/webhooks/${id}`),

  test: (id: string) =>
    postData<WebhookTestResponse>(`/api/v1/webhooks/${id}/test`, {}),

  deliveries: (id: string, limit = 50) =>
    getData<WebhookDeliveryRead[]>(
      `/api/v1/webhooks/${id}/deliveries`,
      { params: { limit } },
    ),
}
