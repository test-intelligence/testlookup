import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import { Bot, Cog, KeyRound, Server, User as UserIcon } from 'lucide-react'

import type { ActivityEvent, ActorType } from '@/services/activityService'
import { formatCompactDateTime, shortAgo } from '@/utils/formatters'

/**
 * One row of the activity feed.
 *
 * The row is scanned, not read: category and actor are encoded in FORM (a
 * tinted chip, an icon) as well as in words, so a reader sweeping the column
 * can tell a configuration change from a run without parsing the sentence.
 */

/** Category tints. Semantic, not decorative: `release` and `quality` are the
 *  two a lead scans for, so they get the strongest values. */
const CATEGORY_STYLE: Record<string, string> = {
  runs: 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]',
  analysis: 'bg-[var(--color-accent-muted)]/40 text-[var(--color-accent)]',
  release: 'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]',
  quality: 'bg-[var(--status-flaky-bg)]/40 text-[var(--status-flaky)]',
  configuration: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]',
  membership: 'bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]',
  test_management: 'bg-[var(--color-accent-muted)]/30 text-[var(--color-accent)]',
  integration: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]',
  agent: 'bg-[var(--color-accent-muted)]/50 text-[var(--color-accent)]',
  system: 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]',
}

const ACTOR_ICON: Record<ActorType, typeof UserIcon> = {
  user: UserIcon,
  api_key: KeyRound,
  service_account: Server,
  system: Cog,
  agent: Bot,
}

const ACTOR_LABEL: Record<ActorType, string> = {
  user: 'person',
  api_key: 'API key',
  service_account: 'service account',
  system: 'system',
  agent: 'AI agent',
}

interface Props {
  event: ActivityEvent
  onOpen?: (event: ActivityEvent) => void
  /** Compact form for the Overview panel: no category chip, tighter padding. */
  compact?: boolean
}

export default function ActivityRow({ event, onOpen, compact = false }: Props) {
  const ActorIcon = ACTOR_ICON[event.actor.type] ?? UserIcon
  const when = event.occurred_at

  return (
    <li
      className={clsx(
        'flex items-start gap-3 border-b border-[var(--color-border)] last:border-b-0',
        compact ? 'py-2' : 'px-4 py-3',
      )}
      data-testid="activity-row"
      data-event-type={event.event_type}
    >
      {!compact && (
        <span
          className={clsx(
            'shrink-0 mt-0.5 px-2 py-0.5 rounded text-[11px] font-medium capitalize',
            CATEGORY_STYLE[event.category] ??
              'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]',
          )}
        >
          {event.category.replace('_', ' ')}
        </span>
      )}

      <div className="min-w-0 flex-1">
        {/* The summary is the row. Rendered as a button when a detail exists so
            what is interactive looks interactive; as plain text otherwise,
            rather than a control that does nothing. */}
        {onOpen ? (
          <button
            type="button"
            onClick={() => onOpen(event)}
            className="text-left text-[13px] text-[var(--color-text)] hover:text-[var(--color-accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent)] rounded-sm"
          >
            {event.summary}
          </button>
        ) : (
          <p className="text-[13px] text-[var(--color-text)]">{event.summary}</p>
        )}

        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--color-text-muted)]">
          <span className="inline-flex items-center gap-1">
            <ActorIcon className="h-3 w-3" aria-hidden="true" />
            <span>{event.actor.name || 'Unknown'}</span>
            {event.actor.type !== 'user' && (
              <span
                className="px-1 rounded bg-[var(--color-bg-secondary)]"
                title={`Acted as a ${ACTOR_LABEL[event.actor.type]}`}
              >
                {ACTOR_LABEL[event.actor.type]}
              </span>
            )}
          </span>

          {/* Absolute time in the tooltip, relative on screen: the relative
              form is what a reader scans, the absolute is what they need when
              correlating with a log. */}
          {when && (
            <span title={formatCompactDateTime(when)}>{shortAgo(when)}</span>
          )}

          {event.entity.href ? (
            <Link
              to={event.entity.href}
              className="text-[var(--color-accent)] hover:underline"
            >
              {event.entity.label || event.entity.type}
            </Link>
          ) : (
            event.entity.label && (
              // No href means the entity has no page, or was deleted. The
              // label still renders — a disabled-looking link that goes
              // nowhere is worse than plain text.
              <span title="This item no longer has a page">
                {event.entity.label}
              </span>
            )
          )}
        </div>
      </div>
    </li>
  )
}
