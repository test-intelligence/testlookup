/**
 * What the Test Cases page (`/test-management`) and its tab bodies in this
 * folder share: the status / priority pill colours (per-theme tokens, never
 * raw palette classes) and the two date formats.
 */

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

export const PRIORITY_COLORS: Record<string, string> = {
  critical: 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  high:     'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  medium:   'bg-[var(--status-skipped-bg)] text-[var(--status-skipped)] border border-[var(--status-skipped-bd)]',
  low:      'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]',
}

export function fmtDate(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function fmtDateTime(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
