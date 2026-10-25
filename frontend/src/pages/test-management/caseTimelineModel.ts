/**
 * W13 TCL-05 — the case Timeline's copy and pure helpers (no React, no
 * fetching). `CaseTimeline` renders these; its test imports them.
 *
 * Links are built from IDS only (each `encodeURIComponent`-ed): never from a
 * title, a detail or a Jira key. None points into `SINGLE_PROJECT_ROUTES`.
 */
import type { TimelineEntry } from '@/types/test-library'

export type TimelinePresetId = 'all' | 'changes' | 'reviews' | 'comments' | 'executions' | 'defects'

export const TIMELINE_PRESET_IDS: readonly TimelinePresetId[] = ['all', 'changes', 'reviews', 'comments', 'executions', 'defects']

export const PRESET_LABELS: Record<TimelinePresetId, string> = {
  all: 'All',
  changes: 'Changes',
  reviews: 'Reviews',
  comments: 'Comments',
  executions: 'Executions',
  defects: 'Defects',
}

/** = the server's `TIMELINE_PRESETS`; All sends no `kinds` (every kind). */
export const PRESET_KINDS: Record<TimelinePresetId, readonly string[]> = {
  all: [],
  changes: ['version', 'requirement'],
  reviews: ['review_requested', 'review_decided'],
  comments: ['comment'],
  executions: ['plan_execution', 'automated_execution'],
  defects: ['defect'],
}

export const TIMELINE_END_TEXT = 'Start of the timeline.'
export const TIMELINE_EMPTY_TEXT = 'Nothing recorded for this case yet.'
export const TIMELINE_ERROR_TEXT = 'Could not load the timeline.'
export const TIMELINE_LOADING_TEXT = 'Loading the timeline…'
export const NO_AUTOMATION_TEXT = 'No automated test is linked to this case; plan executions are shown.'
export const LOAD_MORE_TEXT = 'Load more'
export const RETRY_TEXT = 'Retry'

/** "by Priya Shah"; an automated run "by automation"; nobody recorded "by not recorded" (never blank). */
export function actorLine(entry: Pick<TimelineEntry, 'actor_name' | 'actor_source'>): string {
  const name = entry.actor_name?.trim()
  if (name) return `by ${name}`
  if (entry.actor_source === 'automation') return 'by automation'
  return 'by not recorded'
}

export interface EntryLink {
  to: string
  label: string
}

const enc = encodeURIComponent

/**
 * Where an entry leads: a per-run result → `/runs/<run>/tests/<result>`, a run
 * → `/runs/<run>`, a plan → the Plans tab, a defect → `/defects?defect=<id>`.
 * Null when the link names no id it needs.
 */
export function entryLink(entry: Pick<TimelineEntry, 'link'>): EntryLink | null {
  const link = entry.link
  if (!link) return null
  switch (link.type) {
    case 'run_result':
      if (link.run_id && link.test_case_id) return { to: `/runs/${enc(link.run_id)}/tests/${enc(link.test_case_id)}`, label: 'Open the result' }
      if (link.run_id) return { to: `/runs/${enc(link.run_id)}`, label: 'Open the run' }
      return null
    case 'run':
      return link.run_id ? { to: `/runs/${enc(link.run_id)}`, label: 'Open the run' } : null
    case 'plan':
      return { to: '/test-management?tab=plans', label: 'Open the plans' }
    case 'defect':
      return link.defect_id ? { to: `/defects?defect=${enc(link.defect_id)}`, label: 'Open the defect' } : null
    default:
      return null
  }
}

/** The kinds whose `status` is a RESULT (StatusBadge's vocabulary). */
export const RESULT_STATUS_KINDS: ReadonlySet<string> = new Set(['plan_execution', 'automated_execution'])

const UI_TONE = {
  success: 'bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)] border border-[var(--ui-success-bd)]',
  warning: 'bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)] border border-[var(--ui-warning-bd)]',
  danger: 'bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)] border border-[var(--ui-danger-bd)]',
  neutral: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
} as const

const REVIEW_TONE: Record<string, string> = {
  approved: UI_TONE.success,
  changes_requested: UI_TONE.warning,
  rejected: UI_TONE.danger,
}

/** A review decision's / a defect's pill tone (`--ui-*` tints with their `-ink`); a neutral pill otherwise. */
export function pillTone(entry: Pick<TimelineEntry, 'kind' | 'status'>): string {
  if (entry.kind === 'review_decided' && entry.status) return REVIEW_TONE[entry.status] ?? UI_TONE.neutral
  return UI_TONE.neutral
}

/** "changes_requested" → "Changes requested". */
export function humanStatus(status: string): string {
  const text = status.replace(/_/g, ' ').trim()
  return text ? text.charAt(0).toUpperCase() + text.slice(1).toLowerCase() : text
}
