import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Activity as ActivityIcon, Download, Search } from 'lucide-react'
import toast from 'react-hot-toast'

import ActivityDrawer from '@/components/activity/ActivityDrawer'
import ActivityRow from '@/components/activity/ActivityRow'
import EmptyState from '@/components/ui/EmptyState'
import PageHeader from '@/components/ui/PageHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { useActivityEventTypes, useActivityFeed } from '@/hooks/useActivityFeed'
import { exportActivity } from '@/services/activityService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { formatCompactDateTime } from '@/utils/formatters'

const WINDOW_OPTIONS = [1, 7, 30, 90] as const

/**
 * The project activity feed.
 *
 * Single-project by design and declared as such in `config/routeScope.ts` —
 * without that declaration every ScopedLink pointing here would promise a
 * destination that renders a picker prompt instead of content.
 */
export default function ActivityPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || activeProjectId === null

  const [searchParams, setSearchParams] = useSearchParams()
  const [category, setCategory] = useState<string>('')
  const [actorType, setActorType] = useState<string>('')
  const [q, setQ] = useState('')
  const [queryText, setQueryText] = useState('')

  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, WINDOW_OPTIONS)

  const { eventTypes } = useActivityEventTypes()

  // `days`, not a resolved `since`. The window boundary is computed inside the
  // SWR fetcher instead of here: Date.now() during render is impure, and an
  // anchor frozen at first render would slowly exclude the newest events on a
  // page someone leaves open.
  const query = useMemo(
    () => ({
      ...(category ? { category: [category] } : {}),
      ...(actorType ? { actor_type: actorType } : {}),
      ...(q.trim().length >= 3 ? { q: q.trim() } : {}),
      days,
      limit: 50,
    }),
    [category, actorType, q, days],
  )

  const {
    events,
    ledgerStartedAt,
    hasMore,
    loadMore,
    isLoading,
    isValidating,
    error,
  } = useActivityFeed(isAllProjects ? null : activeProjectId, query, !isAllProjects)

  const openEventId = searchParams.get('event')
  const openEvent = (id: string) => {
    searchParams.set('event', id)
    setSearchParams(searchParams, { replace: true })
  }
  const closeEvent = () => {
    searchParams.delete('event')
    setSearchParams(searchParams, { replace: true })
  }

  const handleExport = async (format: 'csv' | 'ndjson') => {
    if (!activeProjectId || isAllProjects) return
    try {
      // `days` is a client-side shorthand the feed hook resolves in its
      // fetcher; the API only knows `since`. Resolving it here (in an event
      // handler, not during render) keeps the export window identical to the
      // one on screen.
      const { days: windowDays, ...rest } = query
      const { truncated, rowCount } = await exportActivity(activeProjectId, {
        ...rest,
        since: new Date(Date.now() - windowDays * 86_400_000).toISOString(),
        format,
      })
      toast.success(
        truncated
          ? `Exported the most recent ${rowCount.toLocaleString()} events. Narrow the window to get the rest.`
          : `Exported ${rowCount.toLocaleString()} events`,
      )
    } catch {
      toast.error('Export failed. You need the QA Lead role on this project.')
    }
  }

  // ── All-projects prompt ───────────────────────────────────────────────────
  // The feed is inherently per-project: "what happened" has no meaning across
  // a tenant.
  //
  // ProjectRequiredEmptyState, not a bare EmptyState. A hand-rolled guard here
  // gives the reader a sentence telling them to go and find the top bar, with
  // nothing to press — and it is invisible to the routeScope ratchet, so every
  // ScopedLink pointing at /activity would still promise a working
  // destination. The ratchet caught exactly that when this page first hand-
  // rolled the check.
  if (isAllProjects) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Activity"
          subtitle="Everything that happened in a project — runs, releases, quality and configuration changes."
        />
        <ProjectRequiredEmptyState
          icon={<ActivityIcon className="h-6 w-6" />}
          description="Activity is recorded per project, so pick one to see what happened in it."
        />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Activity"
        subtitle="Everything that happened in this project — runs, releases, quality and configuration changes."
        actions={
          <button
            type="button"
            onClick={() => handleExport('csv')}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-bg-card)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
          >
            <Download className="h-4 w-4" />
            Export CSV
          </button>
        }
      />

      {/* ── Filters ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="activity-search">
          Search activity
        </label>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            id="activity-search"
            value={queryText}
            onChange={e => setQueryText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') setQ(queryText)
            }}
            onBlur={() => setQ(queryText)}
            placeholder="Search activity…"
            className="w-56 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] py-1.5 pl-8 pr-3 text-[13px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
          />
        </div>

        <select
          aria-label="Filter by category"
          value={category}
          onChange={e => setCategory(e.target.value)}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] px-3 py-1.5 text-[13px] text-[var(--color-text)]"
        >
          <option value="">All categories</option>
          {(eventTypes?.categories ?? []).map(c => (
            <option key={c} value={c}>
              {c.replace('_', ' ')}
            </option>
          ))}
        </select>

        <select
          aria-label="Filter by who acted"
          value={actorType}
          onChange={e => setActorType(e.target.value)}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] px-3 py-1.5 text-[13px] text-[var(--color-text)]"
        >
          <option value="">Anyone</option>
          {(eventTypes?.actor_types ?? []).map(a => (
            <option key={a} value={a}>
              {a.replace('_', ' ')}
            </option>
          ))}
        </select>

        <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] p-0.5">
          {WINDOW_OPTIONS.map(d => (
            <button
              key={d}
              type="button"
              onClick={() => setStoredDays(d)}
              className={
                days === d
                  ? 'rounded px-2.5 py-1 text-[12px] font-medium tabular-nums bg-[var(--color-accent)] text-white'
                  : 'rounded px-2.5 py-1 text-[12px] font-medium tabular-nums text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
              }
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* ── Feed ────────────────────────────────────────────────────────── */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)]">
        {/* An outage must never render as an empty feed. "No activity yet" and
            "we could not reach the backend" mean opposite things to a reader
            deciding whether to worry. */}
        {error ? (
          <EmptyState
            icon={<ActivityIcon className="h-6 w-6" />}
            title="Activity is unavailable"
            description="The activity feed could not be loaded. This is a loading failure, not an empty project — try again shortly."
          />
        ) : isLoading ? (
          <p
            className="px-4 py-10 text-center text-[13px] text-[var(--color-text-muted)]"
            aria-busy="true"
          >
            Loading activity…
          </p>
        ) : events.length === 0 ? (
          <EmptyState
            icon={<ActivityIcon className="h-6 w-6" />}
            title="No activity in this window"
            description={
              ledgerStartedAt
                ? `Nothing matched your filters. This project's activity ledger starts on ${formatCompactDateTime(ledgerStartedAt)}.`
                : 'Nothing has been recorded for this project yet. Upload a test report or change a setting, and it will show up here.'
            }
          />
        ) : (
          <>
            <ul role="feed" aria-busy={isValidating} className="list-none">
              {events.map(event => (
                <ActivityRow key={event.id} event={event} onOpen={e => openEvent(e.id)} />
              ))}
            </ul>

            <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-3">
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {events.length.toLocaleString()} event
                {events.length === 1 ? '' : 's'}
                {ledgerStartedAt &&
                  ` · ledger starts ${formatCompactDateTime(ledgerStartedAt)}`}
              </p>
              {hasMore && (
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={isValidating}
                  className="rounded border border-[var(--color-border)] px-3 py-1.5 text-[12px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
                >
                  {isValidating ? 'Loading…' : 'Load older'}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {activeProjectId && (
        <ActivityDrawer
          projectId={activeProjectId}
          eventId={openEventId}
          onClose={closeEvent}
        />
      )}
    </div>
  )
}
