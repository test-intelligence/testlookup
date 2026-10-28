import { Fragment, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Activity as ActivityIcon, Download, Search } from 'lucide-react'
import toast from 'react-hot-toast'

import ActivityDrawer from '@/components/activity/ActivityDrawer'
import ActivityRow from '@/components/activity/ActivityRow'
import LiveToggle, { LiveStatusLine } from '@/components/activity/LiveToggle'
import EmptyState from '@/components/ui/EmptyState'
import { isDetailToken } from '@/components/ui/inPage/ids'
import { useDetailParam } from '@/components/ui/inPage/useDetailParam'
import PageHeader from '@/components/ui/PageHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { windowLabel } from '@/components/ui/WindowPicker'
import { useActivityEventTypes, useActivityFeed } from '@/hooks/useActivityFeed'
import { readLivePreference, useActivityLive, writeLivePreference } from '@/hooks/useActivityLive'
import { exportActivity } from '@/services/activityService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { formatCompactDateTime, formatNumber } from '@/utils/formatters'

/** The feed's windows: the shared set, 14d included (WIN-01), so a fortnight picked elsewhere is not shown as a week. */
const WINDOW_OPTIONS = [1, 7, 14, 30, 90] as const

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

  const [searchParams] = useSearchParams()
  // HON-09: `?category=` pre-selects the filter, so a link can open the feed
  // already narrowed (the /releases rail's "View full audit log" sends
  // `release`; so does the old `/audit` bookmark). Read once, at mount: the
  // select owns the value from then on.
  const [category, setCategory] = useState<string>(
    () => searchParams.get('category')?.trim().toLowerCase() ?? '',
  )
  const [actorType, setActorType] = useState<string>('')
  const [q, setQ] = useState('')
  const [queryText, setQueryText] = useState('')

  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, WINDOW_OPTIONS)

  const { eventTypes, error: eventTypesError } = useActivityEventTypes()
  // A category from the URL applies once the registry lists it: the API 422s
  // an unknown one. Until the registry answers, the feed waits (no flash of
  // the unfiltered feed); if the registry fails, the filter is dropped, not
  // the feed.
  const knownCategories = eventTypes?.categories
  const categoryPending = category !== '' && knownCategories === undefined && !eventTypesError
  const activeCategory = category !== '' && knownCategories?.some((known) => known === category) ? category : ''

  // `days`, not a resolved `since`. The window boundary is computed inside the
  // SWR fetcher instead of here: Date.now() during render is impure, and an
  // anchor frozen at first render would slowly exclude the newest events on a
  // page someone leaves open.
  const query = useMemo(
    () => ({
      ...(activeCategory ? { category: [activeCategory] } : {}),
      ...(actorType ? { actor_type: actorType } : {}),
      ...(q.trim().length >= 3 ? { q: q.trim() } : {}),
      days,
      limit: 50,
    }),
    [activeCategory, actorType, q, days],
  )

  // ACT-02 Live: OFF until this viewer turns it on (the choice is remembered
  // in this browser). The feed stops polling only while the stream is LIVE —
  // the live hook reports that status below, the feed learns it on the next
  // render (adjusted during render, not in an effect).
  const [liveOn, setLiveOn] = useState(readLivePreference)
  const [streaming, setStreaming] = useState(false)
  const feedProjectId = isAllProjects ? null : activeProjectId
  const feedEnabled = !isAllProjects && !categoryPending

  const {
    events,
    ledgerStartedAt,
    hasMore,
    loadMore,
    isLoading,
    isValidating,
    error,
    refresh,
    insertLive,
  } = useActivityFeed(feedProjectId, query, feedEnabled, { live: streaming })

  const live = useActivityLive({
    projectId: feedProjectId,
    enabled: liveOn && feedEnabled,
    query,
    onInsert: insertLive,
    onRefresh: refresh,
  })
  if ((live.status === 'live') !== streaming) setStreaming(live.status === 'live')
  const toggleLive = (next: boolean) => {
    setLiveOn(next)
    writeLivePreference(next)
  }

  // W27b: the open event rides `?event=` through useDetailParam: opening one
  // from closed PUSHES (Back closes it), another row REPLACES, Close pops our
  // own entry; `category` and every other param are untouched. The value is
  // checked first (isDetailToken: a production id is a UUID, the fixtures'
  // `ev-rel-1` passes): a malformed one (`../../x`) is `rejected` and never
  // reaches the request path.
  const detail = useDetailParam('event', { accept: isDetailToken })
  const openEventId = detail.id
  const detailOpen = openEventId !== null || detail.rejected !== null
  const openEvent = (id: string) => detail.open(id)
  const closeEvent = detail.close
  // Under its row when the feed holds it; else (a deep link to an older
  // event, the feed still loading, or a rejected link) right above the feed.
  const openUnderRow = openEventId !== null && events.some((e) => e.id === openEventId)

  const eventDetail = detailOpen && activeProjectId && (
    <SectionErrorBoundary key={openEventId ?? 'rejected'} message="The event could not be shown">
      <ActivityDrawer
        projectId={activeProjectId}
        eventId={openEventId}
        rejected={detail.rejected !== null}
        onClose={closeEvent}
      />
    </SectionErrorBoundary>
  )

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
          ? `Exported the most recent ${formatNumber(rowCount)} events. Narrow the window to get the rest.`
          : `Exported ${formatNumber(rowCount)} events`,
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
            className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-[var(--color-bg-card)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
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
          value={activeCategory}
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
              aria-pressed={days === d}
              onClick={() => setStoredDays(d)}
              className={
                days === d
                  ? 'rounded px-2.5 py-1 text-[12px] font-medium tabular-nums bg-[var(--color-accent-muted)] text-[var(--color-accent-ink)]'
                  : 'rounded px-2.5 py-1 text-[12px] font-medium tabular-nums text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
              }
            >
              {/* The shared label: "24h" for one day, as on every other windowed page. */}
              {windowLabel(d)}
            </button>
          ))}
        </div>

        <LiveToggle on={liveOn} status={live.status} onToggle={toggleLive} />
      </div>

      <LiveStatusLine status={live.status} message={live.message} />

      {!openUnderRow && eventDetail}

      {/* ── Feed ────────────────────────────────────────────────────────── */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)]">
        {/* An outage must never render as an empty feed. "No activity yet" and
            "we could not reach the backend" mean opposite things to a reader
            deciding whether to worry. */}
        {/* `error && events.length === 0`, NOT `error` alone.
            A failure on page N used to discard pages 1..N-1: the reader had 34
            rows, pressed "Load older", the cursor request failed, and the whole
            feed collapsed to "Activity is unavailable". Losing what you already
            had is a worse outcome than the failed page, and it makes a
            transient blip look like an outage. Keep the rows; report the
            failure inline below them (see the footer). */}
        {error && events.length === 0 ? (
          <EmptyState
            icon={<ActivityIcon className="h-6 w-6" />}
            title="Activity is unavailable"
            description="The activity feed could not be loaded. This is a loading failure, not an empty project — try again shortly."
          />
        ) : isLoading || categoryPending ? (
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
                <Fragment key={event.id}>
                  <ActivityRow event={event} onOpen={e => openEvent(e.id)} />
                  {/* The open event's detail, right under its row: keyed by the
                      event (the Fragment), so another row's detail is a new
                      one whose focus returns to THAT row on close. */}
                  {openUnderRow && event.id === openEventId && (
                    <li
                      data-in-page-row=""
                      className="border-b border-[var(--color-border)] px-4 py-3 last:border-b-0"
                    >
                      {eventDetail}
                    </li>
                  )}
                </Fragment>
              ))}
            </ul>

            <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-3">
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {formatNumber(events.length)} event
                {events.length === 1 ? '' : 's'}
                {ledgerStartedAt &&
                  ` · ledger starts ${formatCompactDateTime(ledgerStartedAt)}`}
              </p>
              <div className="flex items-center gap-3">
                {/* A page that failed AFTER some rows loaded is reported here,
                    beside the retry, instead of replacing the feed. */}
                {error && (
                  <span
                    role="status"
                    className="text-[11px] text-[var(--status-failed)]"
                  >
                    Couldn&apos;t load older events
                  </span>
                )}
                {hasMore && (
                  <button
                    type="button"
                    onClick={loadMore}
                    disabled={isValidating}
                    className="rounded border border-[var(--color-border)] px-3 py-1.5 text-[12px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
                  >
                    {isValidating ? 'Loading…' : error ? 'Try again' : 'Load older'}
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
