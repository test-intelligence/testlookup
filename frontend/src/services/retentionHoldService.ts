import { api } from './api'
import type {
  HoldEntityType,
  HoldStateFilter,
  RetentionHold,
  RetentionHoldCreate,
  RetentionHoldList,
} from '@/types/retentionHolds'

/**
 * W22a ACT-05: retention holds of a project.
 *
 * - list: any member of the project (a project-bound key too) — read only;
 * - place: QA_LEAD or ADMIN, signed in (an API key gets 403);
 * - release: ADMIN, signed in, with a reason.
 *
 * Every request path is written out in full in its call (the SPA
 * wire-contract guard reads them). Errors ride the shared Axios interceptor;
 * the hold routes' own 4xx carry `detail: {code, message}` with one of
 * `HOLD_ERROR_SENTENCES`.
 */

export interface ListRetentionHoldsOptions {
  state?: HoldStateFilter
  entityType?: HoldEntityType
  entityId?: string
  limit?: number
}

export async function listRetentionHolds(
  projectId: string,
  options: ListRetentionHoldsOptions = {},
): Promise<RetentionHoldList> {
  const params: Record<string, string | number> = {}
  if (options.state) params.state = options.state
  if (options.entityType) params.entity_type = options.entityType
  if (options.entityId) params.entity_id = options.entityId
  if (options.limit !== undefined) params.limit = options.limit
  const { data } = await api.get<RetentionHoldList>(`/api/v1/projects/${projectId}/retention-holds`, { params })
  return data
}

export async function placeRetentionHold(projectId: string, body: RetentionHoldCreate): Promise<RetentionHold> {
  const { data } = await api.post<RetentionHold>(`/api/v1/projects/${projectId}/retention-holds`, body)
  return data
}

export async function releaseRetentionHold(projectId: string, holdId: string, reason: string): Promise<RetentionHold> {
  const { data } = await api.post<RetentionHold>(
    `/api/v1/projects/${projectId}/retention-holds/${holdId}/release`,
    { reason },
  )
  return data
}
