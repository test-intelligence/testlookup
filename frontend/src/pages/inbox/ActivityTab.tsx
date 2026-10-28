/**
 * Inbox › Activity (W21d ACT-02, EVERY role, `?tab=activity`): what the reader
 * did and what involves them — mentions, test cases they are assigned to or
 * wrote, defects assigned to them — across their projects.
 *
 * Reads `/activity/mine` only while this tab is rendered (the page renders it
 * only on its own tab), through `useMyActivity`. The top bar's project pins
 * the read; All Projects reads every project the reader belongs to and names
 * each row's project. The count line is the server's `total` for the window,
 * not the page length; a failed read says so — never an empty list, never 0.
 * Each row is the feed's own `ActivityRow` plus the involvement chip; its link
 * is the server's `entity.href`, and clicking it first switches to the row's
 * project when another one is active, so the target opens where it lives.
 */
import { Activity as ActivityIcon } from 'lucide-react'

import ActivityRow from '@/components/activity/ActivityRow'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WindowPicker from '@/components/ui/WindowPicker'
import { useTabParam } from '@/components/ui/useTabParam'
import { useMyActivity } from '@/hooks/useMyActivity'
import type { ActivityEvent, MyActivityItem } from '@/services/activityService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { INVOLVEMENT_LABELS, MINE_VIEWS } from '@/types/activityLive'
import ChipFilter from './ChipFilter'
import {
  DEFAULT_VIEW,
  LOAD_ERROR,
  LOAD_OLDER_ERROR,
  VIEW_OPTIONS,
  countLine,
  emptySentence,
} from './activityTabModel'

export default function ActivityTab({ daysOptions }: { daysOptions: readonly number[] }) {
  const [view, setView] = useTabParam(MINE_VIEWS, DEFAULT_VIEW, 'view')
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projects = useProjectStore((s) => s.projects)
  const setActiveProject = useProjectStore((s) => s.setActiveProject)
  const allProjects = activeProjectId === ALL_PROJECTS_ID || !activeProjectId
  const storedDays = useTimeWindowStore((s) => s.days)
  const days = snapToAllowed(storedDays, daysOptions)

  const { items, total, projects: covered, hasMore, loadMore, isLoading, isValidating, error } = useMyActivity({
    view,
    days,
    projectId: allProjects ? null : activeProjectId,
  })

  const projectName = (id: string) => (Array.isArray(projects) ? projects : []).find((p) => p.id === id)?.name ?? null
  const openInProject = (event: ActivityEvent) => {
    if (event.project_id === activeProjectId) return
    const target = (Array.isArray(projects) ? projects : []).find((p) => p.id === event.project_id)
    if (target) setActiveProject(target)
  }

  return (
    <div className="space-y-4">
      {/* Toolbar: one row */}
      <div className="flex items-center gap-3 flex-wrap">
        <WindowPicker options={daysOptions} />
        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
        <ChipFilter label="Show" ariaLabel="Show activity" value={view} onChange={setView} options={VIEW_OPTIONS} />
        {total !== null && !(error && items.length === 0) && (
          <span data-activity-count="" className="ml-auto text-xs text-[var(--color-text-muted)] tabular-nums">
            {countLine(total, days)}
          </span>
        )}
      </div>

      <section data-primary="" aria-label="Your activity">
        {error && items.length === 0 ? (
          <EmptyState
            tone="warning"
            role="alert"
            icon={<ActivityIcon className="h-6 w-6" />}
            title={LOAD_ERROR}
            description="This is a loading failure, not an empty inbox. Reopen the tab or reload the page to try again."
          />
        ) : isLoading && items.length === 0 ? (
          <div className="flex items-center justify-center py-16" aria-busy="true">
            <LoadingSpinner size="lg" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={<ActivityIcon className="h-6 w-6" />} title={emptySentence(view, covered, days)} />
        ) : (
          <div className="card p-0">
            <ul role="feed" aria-busy={isValidating} data-inbox-activity="" className="list-none">
              {items.map((item) => (
                <ActivityRow
                  key={item.id}
                  event={item}
                  onEntityClick={openInProject}
                  meta={<RowMeta item={item} projectName={allProjects ? projectName(item.project_id) : null} />}
                />
              ))}
            </ul>
            {(hasMore || error) && (
              <div className="flex items-center justify-end gap-3 border-t border-[var(--color-border)] px-4 py-3">
                {error && (
                  <span role="status" className="text-2xs text-[var(--ui-danger)]">
                    {LOAD_OLDER_ERROR}
                  </span>
                )}
                {hasMore && (
                  <button type="button" className="btn-secondary btn-sm" onClick={loadMore} disabled={isValidating}>
                    {isValidating ? 'Loading…' : error ? 'Try again' : 'Load older'}
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}

/** The end of a row's meta line: why it is here, and (All Projects) where. */
function RowMeta({ item, projectName }: { item: MyActivityItem; projectName: string | null }) {
  const label = INVOLVEMENT_LABELS[item.involvement] ?? null
  return (
    <>
      {label && (
        <span
          data-involvement={item.involvement}
          className="inline-flex items-center rounded-full border border-[var(--color-border)] px-1.5 text-2xs leading-4 text-[var(--color-text-secondary)]"
        >
          {label}
        </span>
      )}
      {projectName && (
        <span data-activity-project="" title={projectName} className="max-w-[160px] truncate">
          {projectName}
        </span>
      )}
    </>
  )
}
