/**
 * What the Test Cases page (`/test-management`) and its tab bodies in this
 * folder share: the status / priority pill colours (per-theme tokens, never
 * raw palette classes) and the two date formats.
 */
import { formatCompactDateTime, formatDay } from '@/utils/formatters'

export const STATUS_COLORS: Record<string, string> = {
  draft:            'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  review_requested: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  under_review:     'bg-[var(--color-bg-secondary)]/80 text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  approved:         'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  active:           'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  rejected:         'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  needs_update:     'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  deprecated:       'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]',
  archived:         'bg-[var(--color-bg-secondary)] text-[var(--color-text-faint)] border border-[var(--color-border)]',
}

/**
 * A test case's priority pill (W29b, the W29a follow-up): the ONE severity map's
 * P0-P3 look (`components/ui/severity.ts` `SEVERITY_STYLE`) as class strings —
 * critical P0 danger, high P1 warning, medium P2 INFO (never the skipped gold),
 * low P3 neutral; the tint 15 %, the border 30 %, the text the legible `-ink`.
 * Read by the case view, ApprovalsTab and StrategyTab through `StatusPill`.
 */
export const PRIORITY_COLORS: Record<string, string> = {
  critical: 'bg-[color-mix(in_srgb,var(--ui-danger)_15%,transparent)] text-[var(--ui-danger-ink)] border border-[color-mix(in_srgb,var(--ui-danger)_30%,transparent)]',
  high:     'bg-[color-mix(in_srgb,var(--ui-warning)_15%,transparent)] text-[var(--ui-warning-ink)] border border-[color-mix(in_srgb,var(--ui-warning)_30%,transparent)]',
  medium:   'bg-[color-mix(in_srgb,var(--ui-info)_15%,transparent)] text-[var(--ui-info-ink)] border border-[color-mix(in_srgb,var(--ui-info)_30%,transparent)]',
  low:      'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]',
}

/** A day, "Sep 18, 2026" (the one date format, W29a UIX-10); "—" when there is none. */
export function fmtDate(iso?: string) {
  return formatDay(iso)
}

/** A day and time, "Sep 18, 11:50" (the table stamp, W29a UIX-10); "—" when there is none. */
export function fmtDateTime(iso?: string) {
  return formatCompactDateTime(iso)
}
