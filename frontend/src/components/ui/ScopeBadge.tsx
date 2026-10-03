/**
 * VIZ-307: a panel that does not honour a filter says so — "Not filtered by
 * release", with the reason on hover and focus.
 *
 * Driven ONLY by the server: `IgnoredFilterBadges` reads `meta.ignored_filters`
 * (contract C2), which a route fills when it RECEIVED a filter it cannot apply.
 * No filter sent, nothing ignored, no badge — so the badge never becomes
 * furniture, and there is no client-side list of "pages that ignore releases"
 * to drift out of date.
 *
 * `ScopeBadge` is the one look for every such marker; `AllReleasesBadge`
 * (client-driven, for panels with no envelope) renders through it.
 */
import { CalendarOff, Layers, ListFilter, type LucideIcon } from 'lucide-react'
import type { EnvelopeMeta } from '@/lib/viz/contracts'

export type ScopeDimension = EnvelopeMeta['ignored_filters'][number]['dimension']

const DIMENSION_TEXT: Record<ScopeDimension, string> = {
  release: 'release',
  suite: 'suite',
  window: 'time window',
}

const DIMENSION_ICON: Record<ScopeDimension, LucideIcon> = {
  release: Layers,
  suite: ListFilter,
  window: CalendarOff,
}

const SCOPE_BADGE_CLASS =
  'inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-secondary)] whitespace-nowrap focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'

/** "Not filtered by release". */
function scopeBadgeText(dimension: ScopeDimension): string {
  return `Not filtered by ${DIMENSION_TEXT[dimension]}`
}

export interface ScopeBadgeProps {
  dimension: ScopeDimension
  /** Why the panel cannot honour the filter (shown on hover and focus). Untrusted text. */
  reason?: string
  /** Replaces the visible words ("All releases"); the tooltip still says what is not filtered. */
  label?: string
  /** Replaces the whole tooltip (a caller with its own established wording). */
  title?: string
}

export default function ScopeBadge({ dimension, reason, label, title }: ScopeBadgeProps) {
  const Icon = DIMENSION_ICON[dimension]
  const text = scopeBadgeText(dimension)
  const tooltip = title ?? (reason ? `${text}. ${reason}` : `${text}.`)
  return (
    <span
      data-scope-badge={dimension}
      // Focusable, so the reason in the tooltip is reachable without a mouse.
      tabIndex={0}
      title={tooltip}
      aria-label={tooltip}
      className={SCOPE_BADGE_CLASS}
    >
      <Icon aria-hidden="true" className="h-3 w-3 shrink-0" />
      {label ?? text}
    </span>
  )
}

/**
 * One badge per dimension the server declared it ignored, in the order it
 * declared them. A dimension listed twice shows once, with its first reason.
 */
export function IgnoredFilterBadges({ meta }: { meta: Pick<EnvelopeMeta, 'ignored_filters'> | null | undefined }) {
  const ignored = meta?.ignored_filters ?? []
  if (ignored.length === 0) return null
  const seen = new Set<ScopeDimension>()
  const unique = ignored.filter((entry) => {
    if (seen.has(entry.dimension) || !(entry.dimension in DIMENSION_TEXT)) return false
    seen.add(entry.dimension)
    return true
  })
  if (unique.length === 0) return null
  return (
    <span data-ignored-filters="" className="inline-flex flex-wrap items-center gap-1">
      {unique.map((entry) => (
        <ScopeBadge key={entry.dimension} dimension={entry.dimension} reason={entry.reason} />
      ))}
    </span>
  )
}
