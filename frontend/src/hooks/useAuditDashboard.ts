import useSWR from 'swr'
import {
  type AuditCategory,
  type AuditEvent,
  type TenantObservability,
  getProjectObservability,
  listAuditEvents,
  listCategories,
} from '@/services/auditDashboardService'

/**
 * Audit-dashboard fetches as SWR hooks.
 *
 * Replaces the tab-keyed load-on-mount effects in AuditDashboardPage — the kind
 * the react-hooks `set-state-in-effect` rule (correctly) discourages, since the
 * effect drove `events`/`total`/`categories`/`obs`/`loading` state synchronously
 * off the active tab and filter selection. SWR now owns that state declaratively,
 * one hook per dataset, and re-fetches automatically when the keyed params change
 * (project, category, day window) — so the page no longer threads a `loadEvents`
 * callback through an effect dependency array.
 *
 * Categories are always fetched (they feed the events-tab filter dropdown).
 * Events are gated by `enabled` so the heavier query only runs while the events
 * tab is mounted; observability is gated by `enabled` plus a selected project,
 * mirroring the old `if (tab === ...)` / `projectId` branches in the effect.
 * `shouldRetryOnError: false` surfaces a failed load immediately, like the old
 * single-shot `try/catch`.
 */
const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

const CATEGORIES_KEY = 'audit-categories'
const EVENTS_KEY = 'audit-events'
const OBSERVABILITY_KEY = 'audit-observability'

export function useAuditCategories() {
  const { data, error, isLoading } = useSWR<AuditCategory[]>(
    [CATEGORIES_KEY] as const,
    listCategories,
    OPTS,
  )
  return { categories: data ?? [], isLoading, isError: !!error }
}

export function useAuditEvents(
  params: { projectId?: string; category?: string; days: number },
  enabled: boolean,
) {
  const { projectId, category, days } = params
  const { data, error, isLoading } = useSWR<{ total: number; items: AuditEvent[] }>(
    enabled ? ([EVENTS_KEY, projectId ?? '', category ?? '', days] as const) : null,
    ([, project, cat, d]: readonly [string, string, string, number]) =>
      listAuditEvents({
        project_id: project || undefined,
        category: cat || undefined,
        days: d,
        page_size: 100,
      }),
    OPTS,
  )
  return { events: data?.items ?? [], total: data?.total ?? 0, isLoading, isError: !!error }
}

export function useProjectObservability(
  projectId: string | undefined,
  enabled: boolean,
  days = 7,
) {
  const { data, error, isLoading } = useSWR<TenantObservability>(
    enabled && projectId ? ([OBSERVABILITY_KEY, projectId, days] as const) : null,
    ([, project, d]: readonly [string, string, number]) => getProjectObservability(project, d),
    OPTS,
  )
  return { observability: data ?? null, isLoading, isError: !!error }
}
