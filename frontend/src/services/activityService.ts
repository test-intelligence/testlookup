import type { Involvement, MineView } from '@/types/activityLive'

import { api } from './api'

/**
 * Project activity ledger (epic ACT).
 *
 * Paging is by opaque CURSOR, not page number. The ledger is written to while
 * it is being read — every ingest appends — so offset paging would repeat or
 * skip rows on each page turn. `next_cursor === null` means the end.
 */

export type ActivityCategory =
  | 'runs'
  | 'analysis'
  | 'release'
  | 'quality'
  | 'configuration'
  | 'membership'
  | 'test_management'
  | 'integration'
  | 'agent'
  | 'system'

export type ActorType = 'user' | 'api_key' | 'service_account' | 'system' | 'agent'

export interface ActivityActor {
  type: ActorType
  id: string | null
  name: string | null
  ref: string | null
}

export interface ActivityEntity {
  type: string
  id: string
  label: string | null
  /** Resolved server-side so the CLI and MCP tools get the same links. */
  href: string | null
}

export interface ActivityEvent {
  id: string
  project_id: string
  release_id: string | null
  occurred_at: string | null
  category: ActivityCategory
  event_type: string
  actor: ActivityActor
  entity: ActivityEntity
  target: { type: string; id: string | null } | null
  summary: string
  context: Record<string, unknown> | null
  has_diff: boolean
  source: { table: string; id: string | null } | null
  group_key: string | null
}

export interface ActivityPage {
  items: ActivityEvent[]
  next_cursor: string | null
  /**
   * When this project's ledger begins. Ignores every filter, so the empty
   * state can say "the ledger starts on X" instead of implying nothing ever
   * happened. Null means the project has no activity at all yet.
   */
  ledger_started_at: string | null
  window: { since: string | null; until: string | null }
}

export interface ActivityEventDetail extends ActivityEvent {
  diff: {
    before?: Record<string, unknown> | null
    after?: Record<string, unknown> | null
    changed_fields?: string[]
  } | null
}

export interface ActivityEventTypes {
  categories: ActivityCategory[]
  actor_types: ActorType[]
  entity_types: string[]
  events: Record<string, { event_type: string; entity_type: string }[]>
}

export interface ActivityQuery {
  category?: string[]
  event_type?: string[]
  actor_id?: string
  actor_type?: string
  entity_type?: string
  entity_id?: string
  release_id?: string
  since?: string
  until?: string
  q?: string
  limit?: number
  cursor?: string
}

export async function listActivity(
  projectId: string,
  params: ActivityQuery = {},
): Promise<ActivityPage> {
  const { data } = await api.get<ActivityPage>(
    `/api/v1/projects/${projectId}/activity`,
    { params },
  )
  return data
}

export async function getActivityEvent(
  projectId: string,
  eventId: string,
): Promise<ActivityEventDetail> {
  const { data } = await api.get<ActivityEventDetail>(
    `/api/v1/projects/${projectId}/activity/${eventId}`,
  )
  return data
}

/**
 * The caller's activity, GET /activity/mine (W21d ACT-02): their own rows and the
 * rows that involve them, across every project they can reach (or the one
 * `project_id` pins). Each item is the feed's item plus why it is listed.
 */
export interface MyActivityItem extends ActivityEvent {
  involvement: Involvement
}

export interface MyActivityPage {
  items: MyActivityItem[]
  /** Distinct rows of this view in the window; ignores the cursor. */
  total: number
  next_cursor: string | null
  window: { since: string; until: string | null }
  view: MineView
  /** Projects the read covered; null = every project (admin); 0 = no membership. */
  projects: number | null
}

export interface MyActivityQuery {
  view?: MineView
  since?: string
  until?: string
  limit?: number
  cursor?: string
  /** Pins the read to one project (the Inbox's active project); omitted = every reachable project. */
  project_id?: string
}

export async function listMyActivity(query: MyActivityQuery = {}): Promise<MyActivityPage> {
  const { data } = await api.get<MyActivityPage>('/api/v1/activity/mine', { params: query })
  return data
}

export async function listActivityEventTypes(): Promise<ActivityEventTypes> {
  const { data } = await api.get<ActivityEventTypes>('/api/v1/activity/event-types')
  return data
}

export async function listEntityActivity(
  projectId: string,
  entityType: string,
  entityId: string,
  limit = 50,
): Promise<{ items: ActivityEvent[] }> {
  const { data } = await api.get<{ items: ActivityEvent[] }>(
    `/api/v1/projects/${projectId}/activity/entity/${entityType}/${entityId}`,
    { params: { limit } },
  )
  return data
}

/**
 * Download an export.
 *
 * Uses a blob + object URL rather than pointing the browser at the endpoint,
 * because the request needs the Authorization header that only the shared
 * axios instance carries.
 */
export async function exportActivity(
  projectId: string,
  params: ActivityQuery & { format: 'csv' | 'ndjson' },
): Promise<{ truncated: boolean; rowCount: number }> {
  const response = await api.get(`/api/v1/projects/${projectId}/activity/export`, {
    params,
    responseType: 'blob',
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `activity-${projectId}.${params.format}`)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
  return {
    truncated: response.headers?.['x-truncated'] === 'true',
    rowCount: Number(response.headers?.['x-row-count'] ?? 0),
  }
}
