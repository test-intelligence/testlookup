/**
 * The report context in one line (OD-16): what `ReportContextHeader` says in
 * six large cells, as small text — "Payment Service · All releases · All
 * suites · Last 90 days · Pass rate of executions · 2026-10-03 10:11 UTC".
 *
 * The collapsed report chrome shows this instead of the full header, so a
 * report page opens on its own content rather than on a second header and a
 * second row of KPI tiles. Each value's label is its tooltip; a filter the
 * server did not apply keeps its warning icon.
 */
import { AlertTriangle } from 'lucide-react'
import Skeleton from '@/components/ui/Skeleton'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { buildContextEntries, CONTEXT_INLINE_MAX, type ContextEntry } from './contextModel'

export interface CompactReportContextProps {
  meta: EnvelopeMeta | null
  allProjects: boolean
  loading?: boolean
  unavailableReason?: string
  windowNote?: string
}

/** One entry's text: its values ("a, b, c +4") or its "All …" text. */
function compactEntryText(entry: ContextEntry): string {
  if (entry.values.length === 0) return entry.allText ?? ''
  const shown = entry.values.slice(0, CONTEXT_INLINE_MAX).map((v) => (v.tag ? `${v.text} (${v.tag})` : v.text))
  const rest = entry.values.length - shown.length
  return rest > 0 ? `${shown.join(', ')} +${rest}` : shown.join(', ')
}

/** The tooltip: the label, every value, and the window's dates. */
function entryTitle(entry: ContextEntry): string {
  const all = entry.values.length > 0 ? entry.values.map((v) => v.text).join(', ') : (entry.allText ?? '')
  const parts = [`${entry.label}: ${all}`]
  if (entry.detail) parts.push(entry.detail)
  if (entry.ignoredReason) parts.push(`Filter not applied: ${entry.ignoredReason}`)
  return parts.join('\n')
}

export default function CompactReportContext({
  meta,
  allProjects,
  loading = false,
  unavailableReason = 'Report context is unavailable for this response.',
  windowNote,
}: CompactReportContextProps) {
  if (!meta) {
    return (
      <div
        aria-label="Report context"
        data-report-context-compact=""
        data-state={loading ? 'loading' : 'unavailable'}
        className="min-w-0 flex-1 text-sm text-[var(--color-text-secondary)]"
      >
        {loading ? <Skeleton variant="line" height={16} width="60%" /> : unavailableReason}
      </div>
    )
  }
  const entries = buildContextEntries({ meta, allProjects, windowNote })
  return (
    <ul
      aria-label="Report context"
      data-report-context-compact=""
      data-state="ready"
      className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--color-text-secondary)]"
    >
      {entries.map((entry, index) => (
        <li key={entry.key} data-context-entry={entry.key} title={entryTitle(entry)} className="inline-flex min-w-0 items-center gap-1">
          {index > 0 && <span aria-hidden="true" className="pr-1 text-[var(--color-text-tertiary,var(--color-text-secondary))]">·</span>}
          <span
            className={
              entry.key === 'project'
                ? 'font-semibold text-[var(--color-text)]'
                : 'text-[var(--color-text)]'
            }
          >
            {entry.key === 'generated' ? `Generated ${compactEntryText(entry)}` : compactEntryText(entry)}
          </span>
          {entry.note && <span>{entry.note}</span>}
          {entry.ignoredReason && (
            <AlertTriangle aria-label="Filter not applied" className="h-3.5 w-3.5 shrink-0 text-[var(--status-broken)]" />
          )}
        </li>
      ))}
    </ul>
  )
}
