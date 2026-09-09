import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'
import toast from 'react-hot-toast'

import { useActivityEvent } from '@/hooks/useActivityFeed'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatCompactDateTime } from '@/utils/formatters'

interface Props {
  projectId: string
  eventId: string | null
  onClose: () => void
}

/**
 * Detail panel for one activity event.
 *
 * `aria-labelledby` points at the live heading id rather than a static
 * `aria-label`: the title is dynamic, and a fixed label goes stale the moment
 * the drawer shows a different event. The `frontend.modal-dialog-role` gate
 * checks the role is present; the naming is on us.
 */
export default function ActivityDrawer({ projectId, eventId, onClose }: Props) {
  const { event, error, isLoading } = useActivityEvent(projectId, eventId)
  const headingId = useId()
  const closeRef = useRef<HTMLButtonElement>(null)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    if (!eventId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    closeRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [eventId, onClose])

  if (!eventId) return null

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
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      role="presentation"
      onClick={onClose}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        className="h-full w-full max-w-lg overflow-y-auto bg-[var(--color-bg-card)] border-l border-[var(--color-border)] shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <header className="sticky top-0 flex items-start gap-3 border-b border-[var(--color-border)] bg-[var(--color-bg-card)] px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2
              id={headingId}
              className="text-[15px] font-semibold text-[var(--color-text)]"
            >
              {event?.summary ?? 'Activity event'}
            </h2>
            <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
              {event?.event_type}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close activity details"
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-5 px-5 py-4 text-[13px]">
          {isLoading && (
            <p className="text-[var(--color-text-muted)]">Loading details…</p>
          )}

          {error && (
            <p className="text-[var(--status-failed)]">
              This event could not be loaded. It may belong to a project you do
              not have access to.
            </p>
          )}

          {event && (
            <>
              <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2">
                <dt className="text-[var(--color-text-muted)]">When</dt>
                <dd className="text-[var(--color-text)]">
                  {formatCompactDateTime(event.occurred_at)}
                </dd>
                <dt className="text-[var(--color-text-muted)]">Who</dt>
                <dd className="text-[var(--color-text)]">
                  {event.actor.name || 'Unknown'}{' '}
                  <span className="text-[var(--color-text-muted)]">
                    ({event.actor.type.replace('_', ' ')}
                    {event.actor.ref ? ` · ${event.actor.ref}` : ''})
                  </span>
                </dd>
                <dt className="text-[var(--color-text-muted)]">What</dt>
                <dd className="text-[var(--color-text)]">
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
                        <span className="truncate text-[var(--color-text-muted)]">
                          {field}
                        </span>
                        <span className="text-[var(--color-text-muted)]">→</span>
                        <span className="truncate text-[var(--color-text)]">
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
      </aside>
    </div>
  )
}

/** Render a JSON value for display without ever printing "[object Object]". */
function formatValue(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
