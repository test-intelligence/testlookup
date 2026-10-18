import { useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'

import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { useActivityEvent } from '@/hooks/useActivityFeed'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatCompactDateTime } from '@/utils/formatters'

interface Props {
  projectId: string
  eventId: string | null
  onClose: () => void
  /**
   * The `?event=` value failed the page's id check (W27b): the detail says
   * "That is not a valid event link." and reads nothing, so a crafted value
   * (`../../x`) never reaches a request path.
   */
  rejected?: boolean
}

/**
 * The detail of one activity event, rendered IN THE PAGE (W27b PAN-05b):
 * ActivityPage places it right under the feed row it belongs to, or right
 * above the feed for a deep link to an event the feed has not loaded.
 *
 * A read-only `<aside>` named by its live heading (the event's summary,
 * `Activity event` while it loads): a fixed label would go stale the moment
 * the detail shows another event. No backdrop, no trap: focus moves to its X
 * when it opens, Escape with focus inside closes it, and focus goes back to
 * the row that opened it (`InlineFormSection` / `useInPageFocus`).
 */
export default function ActivityDrawer({ projectId, eventId, onClose, rejected = false }: Props) {
  const { event, error, isLoading } = useActivityEvent(projectId, rejected ? null : eventId)
  const errorStatus = (error as { response?: { status?: number } } | undefined)
    ?.response?.status
  const [showRaw, setShowRaw] = useState(false)

  if (!eventId && !rejected) return null

  const changed = event?.diff?.changed_fields ?? []
  const before = (event?.diff?.before ?? {}) as Record<string, unknown>
  const after = (event?.diff?.after ?? {}) as Record<string, unknown>

  const copyPermalink = async () => {
    const url = `${window.location.origin}/activity?event=${eventId}`
    const ok = await copyTextToClipboard(url)
    if (ok) toast.success('Link copied')
    else toast.error('Could not copy the link')
  }

  return (
    <InlineFormSection
      as="aside"
      title={event?.summary ?? 'Activity event'}
      description={event?.event_type}
      onClose={onClose}
      closeLabel="Close activity details"
      dataAttributes={{ 'data-activity-detail': '' }}
      className="min-w-0"
    >
      <div className="min-w-0 space-y-5 text-[13px]">
        {rejected && (
          <p className="text-[var(--status-failed)]">That is not a valid event link.</p>
        )}
        {isLoading && (
          <p className="text-[var(--color-text-muted)]">Loading details…</p>
        )}

        {error && (
          <p className="text-[var(--status-failed)]">
            {/* Say which failure it was. A single "may belong to a project
                you do not have access to" was shown for a malformed id
                (422), a deleted/absent event (404) AND a real permission
                error - so the one case where it was true was indistinguishable
                from the two where it was a guess. */}
            {errorStatus === 404
              ? 'This event no longer exists. It may have been removed by the retention purge.'
              : errorStatus === 422
                ? 'That is not a valid event link.'
                : errorStatus === 403
                  ? "You do not have access to this project's activity."
                  : 'This event could not be loaded. Try again shortly.'}
          </p>
        )}

        {event && (
          <>
            <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2">
              <dt className="text-[var(--color-text-muted)]">When</dt>
              <dd className="min-w-0 break-words text-[var(--color-text)]">
                {formatCompactDateTime(event.occurred_at)}
              </dd>
              <dt className="text-[var(--color-text-muted)]">Who</dt>
              <dd className="min-w-0 break-words text-[var(--color-text)]">
                {event.actor.name || 'Unknown'}{' '}
                <span className="text-[var(--color-text-muted)]">
                  ({event.actor.type.replace('_', ' ')}
                  {event.actor.ref ? ` · ${event.actor.ref}` : ''})
                </span>
              </dd>
              <dt className="text-[var(--color-text-muted)]">What</dt>
              <dd className="min-w-0 break-words text-[var(--color-text)]">
                {event.entity.href ? (
                  <Link
                    to={event.entity.href}
                    className="text-[var(--color-accent)] hover:underline"
                  >
                    {event.entity.label || event.entity.id}
                  </Link>
                ) : (
                  (event.entity.label || event.entity.id)
                )}
              </dd>
            </dl>

            {/* Changed fields first — that is what a reader came for. */}
            {changed.length > 0 && (
              <section>
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  Changed
                </h3>
                <ul className="space-y-1">
                  {changed.map(field => (
                    <li
                      key={field}
                      className="grid grid-cols-[1fr_auto_1fr] items-baseline gap-2 rounded bg-[var(--color-bg-secondary)]/50 px-2 py-1"
                    >
                      <span className="min-w-0 truncate text-[var(--color-text-muted)]">
                        {field}
                      </span>
                      <span className="text-[var(--color-text-muted)]">→</span>
                      <span className="min-w-0 truncate text-[var(--color-text)]">
                        {formatValue(after[field]) ?? '—'}
                      </span>
                      {before[field] !== undefined && (
                        <span className="col-span-3 text-[11px] text-[var(--color-text-muted)]">
                          was {formatValue(before[field]) ?? '—'}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {event.context && Object.keys(event.context).length > 0 && (
              <section>
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  Details
                </h3>
                <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1">
                  {Object.entries(event.context).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="truncate text-[var(--color-text-muted)]">
                        {key.replace(/_/g, ' ')}
                      </dt>
                      <dd className="break-words text-[var(--color-text)]">
                        {formatValue(value) ?? '—'}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}

            {event.source && (
              <p className="text-[11px] text-[var(--color-text-muted)]">
                Compliance record: {event.source.table}
                {event.source.id ? ` · ${event.source.id}` : ''}
              </p>
            )}

            {event.has_diff && (
              <div>
                <button
                  type="button"
                  onClick={() => setShowRaw(v => !v)}
                  className="text-[11px] text-[var(--color-accent)] hover:underline"
                >
                  {showRaw ? 'Hide raw payload' : 'Show raw payload'}
                </button>
                {showRaw && (
                  <pre className="mt-2 max-h-64 overflow-auto rounded bg-[var(--color-bg-secondary)] p-3 text-[11px] text-[var(--color-text-secondary)]">
                    {JSON.stringify(event.diff, null, 2)}
                  </pre>
                )}
              </div>
            )}

            <button
              type="button"
              onClick={copyPermalink}
              className="rounded border border-[var(--color-border)] px-3 py-1.5 text-[12px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
            >
              Copy link to this event
            </button>
          </>
        )}
      </div>
    </InlineFormSection>
  )
}

/** Render a JSON value for display without ever printing "[object Object]". */
function formatValue(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
