/**
 * W13 TCL-05 — one timeline per authored test case: versions, review requests
 * and decisions, comments, plan and automated executions, defects and
 * requirement links, newest first, keyset-paged (**Load more**; there is no
 * total — a stream is paged, not counted).
 *
 * Mounted by the case panel's Timeline tab only while that tab is open, so it
 * asks nothing before (`useTestCaseTimeline(caseId, kinds, true)`). One chip
 * at a time; All sends no `kinds`.
 *
 * Every title, name and detail is React TEXT (a `<script>` detail is shown as
 * those characters); links are built from ids only (`caseTimelineModel`).
 */
import { useState, type ComponentType } from 'react'
import { Link } from 'react-router-dom'
import { Bot, Bug, CheckCircle2, ClipboardCheck, FileText, Link2, MessageSquare, Send } from 'lucide-react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import StatusBadge from '@/components/ui/StatusBadge'
import { useTestCaseTimeline } from '@/hooks/useTestLibrary'
import type { TimelineEntry } from '@/types/test-library'
import { formatCompactDateTime } from '@/utils/formatters'
import { STATUS_COLORS } from './format'
import { StatusPill } from './tmUi'
import {
  LOAD_MORE_TEXT,
  NO_AUTOMATION_TEXT,
  PRESET_KINDS,
  PRESET_LABELS,
  RESULT_STATUS_KINDS,
  RETRY_TEXT,
  TIMELINE_EMPTY_TEXT,
  TIMELINE_END_TEXT,
  TIMELINE_ERROR_TEXT,
  TIMELINE_LOADING_TEXT,
  TIMELINE_PRESET_IDS,
  actorLine,
  entryLink,
  humanStatus,
  pillTone,
  type TimelinePresetId,
} from './caseTimelineModel'

export interface CaseTimelineProps {
  caseId: string
  /** Reserved for an entry that names another case (none of the eight kinds does today). */
  onOpenCase?: (caseId: string) => void
}

const KIND_ICONS: Record<string, ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' }>> = {
  version: FileText,
  review_requested: Send,
  review_decided: CheckCircle2,
  comment: MessageSquare,
  plan_execution: ClipboardCheck,
  automated_execution: Bot,
  defect: Bug,
  requirement: Link2,
}

const CHIP =
  'inline-flex items-center rounded-full border border-[var(--color-border)] px-2.5 py-0.5 text-xs text-[var(--color-text-secondary)] ' +
  'hover:bg-[var(--color-bg-hover)] aria-pressed:border-[var(--color-accent)] aria-pressed:bg-[var(--color-accent-bg-soft)] aria-pressed:text-[var(--color-accent-ink)]'
const LINK = 'text-xs text-[var(--color-accent-ink)] hover:underline'

function EntryStatus({ entry }: { entry: TimelineEntry }) {
  if (!entry.status) return null
  if (RESULT_STATUS_KINDS.has(entry.kind)) return <StatusBadge status={entry.status} />
  if (entry.kind === 'version') return <StatusPill status={entry.status} map={STATUS_COLORS} />
  return (
    <span className={clsx('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', pillTone(entry))}>
      {humanStatus(entry.status)}
    </span>
  )
}

function EntryRow({ entry }: { entry: TimelineEntry }) {
  const Icon = KIND_ICONS[entry.kind] ?? FileText
  const link = entryLink(entry)
  return (
    <li
      data-timeline-entry={entry.id}
      data-kind={entry.kind}
      className="grid grid-cols-[88px_20px_minmax(0,1fr)_auto] items-start gap-2 border-b border-[var(--color-border)] py-2 last:border-b-0"
    >
      <time dateTime={entry.occurred_at} title={entry.occurred_at} className="text-xs tabular-nums text-[var(--color-text-muted)]">
        {formatCompactDateTime(entry.occurred_at)}
      </time>
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 text-[var(--color-text-secondary)]" />
      <div className="min-w-0 space-y-0.5">
        <p className="m-0 text-sm font-medium text-[var(--color-text)] [overflow-wrap:anywhere]">{entry.title}</p>
        <p className="m-0 text-xs text-[var(--color-text-muted)]" data-timeline-actor="">{actorLine(entry)}</p>
        {entry.detail && (
          <p className="m-0 line-clamp-2 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" title={entry.detail} data-timeline-detail="">
            {entry.detail}
          </p>
        )}
        {link && (
          <Link to={link.to} className={LINK} data-timeline-link={entry.link?.type}>
            {link.label}
          </Link>
        )}
      </div>
      <div className="justify-self-end"><EntryStatus entry={entry} /></div>
    </li>
  )
}

export default function CaseTimeline({ caseId }: CaseTimelineProps) {
  const [preset, setPreset] = useState<TimelinePresetId>('all')
  const kinds = PRESET_KINDS[preset] as string[]
  const { items, automationLinked, hasMore, loadMore, isLoading, isLoadingMore, error, mutate } = useTestCaseTimeline(caseId, kinds, true)

  return (
    <section aria-label="Timeline" data-case-timeline="" className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show on the timeline">
        {TIMELINE_PRESET_IDS.map((id) => (
          <button
            key={id}
            type="button"
            data-timeline-chip={id}
            aria-pressed={preset === id}
            className={CHIP}
            onClick={() => setPreset(id)}
          >
            {PRESET_LABELS[id]}
          </button>
        ))}
      </div>
      {preset === 'executions' && automationLinked === false && (
        <p role="note" data-timeline-no-automation="" className="m-0 text-xs text-[var(--color-text-muted)]">{NO_AUTOMATION_TEXT}</p>
      )}
      {isLoading && items.length === 0 ? (
        <div role="status" className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <LoadingSpinner size="sm" />
          {TIMELINE_LOADING_TEXT}
        </div>
      ) : error && items.length === 0 ? (
        <TimelineError onRetry={() => void mutate()} />
      ) : items.length === 0 ? (
        <p data-timeline-empty="" className="m-0 text-sm text-[var(--color-text-muted)]">{TIMELINE_EMPTY_TEXT}</p>
      ) : (
        <>
          <ol className="m-0 list-none p-0" data-timeline-list="">
            {items.map((entry) => (
              <EntryRow key={`${entry.kind}:${entry.id}`} entry={entry} />
            ))}
          </ol>
          {error ? (
            <TimelineError onRetry={() => void mutate()} />
          ) : hasMore ? (
            <button
              type="button"
              data-timeline-more=""
              className="btn-secondary btn-sm inline-flex items-center gap-1.5"
              disabled={isLoadingMore}
              onClick={() => void loadMore()}
            >
              {isLoadingMore && <LoadingSpinner size="sm" />}
              {LOAD_MORE_TEXT}
            </button>
          ) : (
            <p data-timeline-end="" className="m-0 text-xs text-[var(--color-text-muted)]">{TIMELINE_END_TEXT}</p>
          )}
        </>
      )}
    </section>
  )
}

function TimelineError({ onRetry }: { onRetry: () => void }) {
  return (
    <div role="alert" data-timeline-error="" className="flex flex-wrap items-center gap-2 text-sm text-[var(--ui-danger-ink)]">
      <span>{TIMELINE_ERROR_TEXT}</span>
      <button type="button" className="btn-secondary btn-sm" onClick={onRetry}>{RETRY_TEXT}</button>
    </div>
  )
}
