/**
 * W12 SRCH-03 — Recent searches on /search (moved out of `SearchPage.tsx`), now
 * from the server (`GET /search/recent`: the reader's last 20, across browsers,
 * kept 30 days) instead of this browser's storage.
 *
 * Clear history still asks inside the card, right under its header (W27b: in
 * the page, never the browser's prompt); only the strip's Clear sends
 * `DELETE /search/recent`.
 *
 * Also the /search cards' shared pieces (the card shell, the list row, the
 * key hint, the highlighted query), brought to the UI standard as they moved:
 * `.card`, the type scale, `--ui-*` / `--color-*` tokens.
 */
import { useState, type ReactNode } from 'react'
import { History as HistoryIcon } from 'lucide-react'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import { useNow } from '@/hooks/useNow'
import { formatNumber } from '@/utils/formatters'
import type { RecentSearch, RecentSearches } from '@/types/search'
import { errorSentence, relativeTime } from './searchModel'

// ── Shared pieces ───────────────────────────────────────────────────────

export function SearchCardShell({
  title, rightSlot, children, testId,
}: { title: ReactNode; rightSlot?: ReactNode; children?: ReactNode; testId?: string }) {
  return (
    <div className="card !p-0 overflow-hidden rounded-xl" data-testid={testId}>
      <div className="flex items-center justify-between gap-2.5 border-b border-[var(--color-border)] px-4 py-3">
        <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">{title}</h3>
        {rightSlot && <div className="flex items-center gap-2.5 text-xs text-[var(--color-text-muted)]">{rightSlot}</div>}
      </div>
      {children}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex items-center gap-0.5 whitespace-nowrap rounded-sm border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 py-px font-mono text-2xs text-[var(--color-text-secondary)]">
      {children}
    </kbd>
  )
}

const TOKEN_STYLE = {
  background: 'color-mix(in srgb, var(--color-accent) 6%, transparent)',
  border: '1px solid color-mix(in srgb, var(--color-accent) 18%, transparent)',
  color: 'var(--color-accent)',
} as const

/**
 * A query with its `key:value` tokens (and quoted phrases) as inline code —
 * React nodes, never HTML: a query like `<img src=x onerror=…>` is text.
 */
export function HighlightedQuery({ q }: { q: string }) {
  const parts: ReactNode[] = []
  const re = /(\b[a-z_]+:(?:"[^"]*"|[^\s"]+)|"[^"]+")/gi
  let last = 0
  let key = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(q)) !== null) {
    if (m.index > last) parts.push(<span key={key++}>{q.slice(last, m.index)}</span>)
    parts.push(
      <code key={key++} className="mx-0.5 rounded-sm px-1 py-px font-mono text-2xs" style={TOKEN_STYLE}>
        {m[0]}
      </code>,
    )
    last = m.index + m[0].length
  }
  if (last < q.length) parts.push(<span key={key}>{q.slice(last)}</span>)
  return <>{parts}</>
}

export interface SearchListRowProps {
  Icon: typeof HistoryIcon
  iconBg: string
  iconFg: string
  /** The row's main text (a query, a saved search's name, a suggestion's label). */
  label: ReactNode
  /** The full text for the row's title (hover). */
  title?: string
  /** A short marker after the label ("Shared") that never truncates: the label truncates instead. */
  badge?: ReactNode
  meta?: ReactNode
  /** The meta line's full text (hover), when it may truncate. */
  metaTitle?: string
  trailing?: ReactNode
  actions?: ReactNode
  onClick: () => void
  /** Rendered under the row at the card's width (an inline form, a confirmation strip). */
  below?: ReactNode
  dataAttributes?: { [data: `data-${string}`]: string }
}

export function SearchListRow({
  Icon, iconBg, iconFg, label, title, badge, meta, metaTitle, trailing, actions, onClick, below, dataAttributes,
}: SearchListRowProps) {
  return (
    <div className="border-b border-[var(--color-border)]" {...dataAttributes}>
      <div className="grid grid-cols-[22px_minmax(0,1fr)_auto_auto] items-center gap-2.5 px-4 py-2.5">
        <span
          className="inline-flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md"
          style={{ background: iconBg, color: iconFg }}
        >
          <Icon className="h-3 w-3" />
        </span>
        <button
          type="button"
          onClick={onClick}
          className="-mx-1 min-w-0 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
        >
          {badge ? (
            <div className="flex min-w-0 items-center gap-1.5">
              <div className="min-w-0 truncate text-sm text-[var(--color-text)]" title={title}>{label}</div>
              <span className="flex-none">{badge}</span>
            </div>
          ) : (
            <div className="truncate text-sm text-[var(--color-text)]" title={title}>{label}</div>
          )}
          {meta && <div className="mt-0.5 truncate text-2xs text-[var(--color-text-muted)]" title={metaTitle}>{meta}</div>}
        </button>
        <span className="text-2xs tabular-nums text-[var(--color-text-muted)]">{trailing}</span>
        {actions ? <span className="flex flex-none items-center gap-1.5">{actions}</span> : <span aria-hidden className="w-3" />}
      </div>
      {below && <div className="px-4 pb-3">{below}</div>}
    </div>
  )
}

// ── Recent searches ─────────────────────────────────────────────────────

export const RECENT_EMPTY = 'Your queries will appear here.'
export const RECENT_EMPTY_HINT = 'Start typing a search above.'
export const RECENT_LOAD_ERROR = 'Could not load recent searches.'
export const RECENT_CLEAR_ERROR = 'Could not clear recent searches.'

const ACCENT_TINT = 'color-mix(in srgb, var(--color-accent) 14%, transparent)'

function resultsLabel(entry: RecentSearch): string {
  const n = entry.result_count
  const plus = entry.counts_exact === false ? '+' : ''
  return `${formatNumber(n)}${plus} result${n === 1 && !plus ? '' : 's'}`
}

export default function RecentSearchesCard({
  recents, loading, failed, projectNames, pinnedProjectId, onPick, onClear,
}: {
  recents: RecentSearches | undefined
  loading: boolean
  failed: boolean
  /** project id -> name, for an entry searched in another project. */
  projectNames: Record<string, string>
  /** The project pinned in the top bar; null in All Projects. */
  pinnedProjectId: string | null
  onPick: (entry: RecentSearch) => void
  /** Clears the server's list (DELETE /search/recent); throws on a refusal. */
  onClear: () => Promise<void>
}) {
  const now = useNow()
  const rows = recents?.items ?? []
  const [confirming, setConfirming] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)

  // The strip's Clear closes the strip at once; a refusal is said under the header.
  const clear = async () => {
    setConfirming(false)
    setClearError(null)
    try {
      await onClear()
    } catch (error) {
      setClearError(errorSentence(error, RECENT_CLEAR_ERROR))
    }
  }

  const where = (entry: RecentSearch): string | null => {
    const id = entry.project_id ?? null
    if (id === pinnedProjectId) return null
    return id === null ? 'All Projects' : (projectNames[id] ?? null)
  }

  return (
    <SearchCardShell
      testId="recent-searches"
      title={
        <span className="inline-flex items-center gap-2">
          <HistoryIcon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Recent searches
        </span>
      }
      rightSlot={
        rows.length > 0 && (
          <button
            type="button"
            onClick={() => { setClearError(null); setConfirming(true) }}
            className="text-[var(--color-accent)] hover:underline"
          >
            Clear history
          </button>
        )
      }
    >
      {confirming && rows.length > 0 && (
        <div className="px-4 pt-3">
          <InlineConfirm
            message={`Clear all ${rows.length} recent search${rows.length === 1 ? '' : 'es'}? This can't be undone.`}
            confirmLabel="Clear"
            tone="danger"
            onConfirm={() => void clear()}
            onCancel={() => setConfirming(false)}
          />
        </div>
      )}
      {clearError && (
        <p role="alert" className="m-0 px-4 pt-3 text-xs text-[var(--ui-danger-ink)]">{clearError}</p>
      )}
      {failed && rows.length === 0 ? (
        <p className="m-0 px-4 py-6 text-center text-sm text-[var(--color-text-secondary)]">{RECENT_LOAD_ERROR}</p>
      ) : loading && rows.length === 0 ? (
        <p className="m-0 px-4 py-6 text-center text-sm text-[var(--color-text-muted)]">Loading recent searches…</p>
      ) : rows.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <p className="m-0 mb-2 text-sm text-[var(--color-text-secondary)]">{RECENT_EMPTY}</p>
          <p className="m-0 text-xs text-[var(--color-text-muted)]">{RECENT_EMPTY_HINT}</p>
        </div>
      ) : (
        <div>
          {rows.map((entry) => {
            const project = where(entry)
            const ts = Date.parse(entry.ts)
            return (
              <SearchListRow
                key={`${entry.ts}-${entry.q}-${entry.scope}-${entry.mode}-${entry.project_id ?? ''}`}
                Icon={HistoryIcon}
                iconBg={ACCENT_TINT}
                iconFg="var(--color-accent)"
                label={<HighlightedQuery q={entry.q} />}
                title={entry.q}
                meta={
                  <>
                    <strong className="font-semibold text-[var(--color-text-secondary)]">{resultsLabel(entry)}</strong>
                    {' · '}
                    {Number.isFinite(ts) ? relativeTime(ts, now) : '—'}
                    {' · '}
                    {entry.mode}
                    {' · '}
                    {entry.scope}
                    {project ? <>{' · '}{project}</> : null}
                  </>
                }
                trailing={<Kbd>↵</Kbd>}
                onClick={() => onPick(entry)}
                dataAttributes={{ 'data-recent-search': '' }}
              />
            )
          })}
        </div>
      )}
    </SearchCardShell>
  )
}
