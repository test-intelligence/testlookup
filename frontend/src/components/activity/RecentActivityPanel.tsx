import { Link } from 'react-router-dom'
import { Activity as ActivityIcon } from 'lucide-react'

import ActivityRow from '@/components/activity/ActivityRow'
import { useActivityFeed } from '@/hooks/useActivityFeed'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

/**
 * The eight most recent things that happened in this project, on /overview.
 *
 * Its own SWR key, deliberately: the panel must never be able to delay the
 * Overview render or take the page down with it. It also only READS — nothing
 * here feeds the first-run wizard's existence probe, which asks a different
 * question ("has this project ever had a run?") of a different table.
 */
export default function RecentActivityPanel({ days = 30 }: { days?: number }) {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects =
    activeProjectId === ALL_PROJECTS_ID || activeProjectId === null

  const { events, isLoading, error } = useActivityFeed(
    isAllProjects ? null : activeProjectId,
    { days, limit: 8 },
    !isAllProjects,
  )

  // In All Projects mode there is nothing coherent to show — "what happened"
  // has no meaning across a tenant — so say so in one line rather than
  // rendering an empty card that looks broken.
  if (isAllProjects) {
    return (
      <div className="card" style={{ padding: '14px 18px 18px' }}>
        <h3 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">
          Recent activity
        </h3>
        <p className="m-0 mt-2 text-[11px] text-[var(--color-text-muted)]">
          Pick a single project to see what has been happening in it.
        </p>
      </div>
    )
  }

  return (
    <div className="card" style={{ padding: '14px 18px 18px' }}>
      <div className="flex items-start justify-between gap-2.5">
        <div>
          <h3 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">
            Recent activity
          </h3>
          <p className="m-0 mt-0.5 text-[11px] text-[var(--color-text-muted)]">
            Runs, releases and changes in this project
          </p>
        </div>
        <Link
          to="/activity"
          className="text-[11px] text-[var(--color-accent)] hover:underline"
        >
          View all
        </Link>
      </div>

      <div className="mt-3">
        {error ? (
          // Not "nothing happened" — those mean opposite things to a reader.
          <p className="text-[12px] text-[var(--color-text-muted)]">
            Activity could not be loaded right now.
          </p>
        ) : isLoading ? (
          <p className="text-[12px] text-[var(--color-text-muted)]" aria-busy="true">
            Loading…
          </p>
        ) : events.length === 0 ? (
          <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
            <ActivityIcon className="h-3.5 w-3.5" />
            <span>Nothing recorded in the last {days} days.</span>
          </div>
        ) : (
          <ul className="list-none" role="feed" aria-label="Recent project activity">
            {events.slice(0, 8).map(event => (
              <ActivityRow key={event.id} event={event} compact />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
