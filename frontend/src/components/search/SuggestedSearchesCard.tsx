/**
 * W12 SRCH-03 — Suggested searches for the pinned project (box empty), from
 * `GET /search/suggested`: the tests failing most in the last 7 days (a top 5,
 * each with its own count), the newest open defects with a Jira key, and the
 * active release's failures. A group with nothing is left out; with nothing at
 * all the card is left out. A row runs its query (built server-side) in the
 * current project. Labels are React text.
 */
import { AlertTriangle, Package, ShieldCheck, Sparkles } from 'lucide-react'
import type { SearchSuggestion, SearchSuggestions } from '@/types/search'
import { Kbd, SearchCardShell, SearchListRow } from './RecentSearchesCard'

export const SUGGESTED_LOAD_ERROR = 'Could not load suggested searches.'

const tint = (token: string) => `color-mix(in srgb, var(${token}) 14%, transparent)`

function Group({
  heading, rows, Icon, iconBg, iconFg, label, onPick,
}: {
  heading: string
  rows: SearchSuggestion[]
  Icon: typeof Sparkles
  iconBg: string
  iconFg: string
  label?: (row: SearchSuggestion) => string
  onPick: (row: SearchSuggestion) => void
}) {
  if (rows.length === 0) return null
  return (
    <section aria-label={heading} data-suggested-group="">
      <h4 className="eyebrow m-0 px-4 pb-1 pt-3">{heading}</h4>
      {rows.map((row) => (
        <SearchListRow
          key={`${row.kind}-${row.q}`}
          Icon={Icon}
          iconBg={iconBg}
          iconFg={iconFg}
          label={label ? label(row) : row.label}
          title={row.label}
          meta={row.detail ?? undefined}
          trailing={<Kbd>↵</Kbd>}
          onClick={() => onPick(row)}
          dataAttributes={{ 'data-suggested-search': row.kind }}
        />
      ))}
    </section>
  )
}

export default function SuggestedSearchesCard({
  suggestions, failed, onPick,
}: {
  suggestions: SearchSuggestions | undefined
  failed: boolean
  onPick: (row: SearchSuggestion) => void
}) {
  if (failed && !suggestions) {
    return (
      <SearchCardShell title={<span className="inline-flex items-center gap-2"><Sparkles className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />Suggested</span>}>
        <p className="m-0 px-4 py-4 text-sm text-[var(--color-text-secondary)]">{SUGGESTED_LOAD_ERROR}</p>
      </SearchCardShell>
    )
  }
  if (!suggestions) return null
  const failing = suggestions.failing_tests ?? []
  const defects = suggestions.open_defects ?? []
  const release = suggestions.active_release ? [suggestions.active_release] : []
  if (failing.length === 0 && defects.length === 0 && release.length === 0) return null
  return (
    <SearchCardShell
      testId="suggested-searches"
      title={
        <span className="inline-flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Suggested
        </span>
      }
    >
      <Group
        heading={`Failing in the last ${suggestions.window_days} days`}
        rows={failing}
        Icon={AlertTriangle}
        iconBg={tint('--ui-danger')}
        iconFg="var(--ui-danger)"
        onPick={onPick}
      />
      <Group
        heading="Open defects"
        rows={defects}
        Icon={ShieldCheck}
        iconBg="var(--color-cyan-soft)"
        iconFg="var(--color-cyan)"
        onPick={onPick}
      />
      <Group
        heading="Active release"
        rows={release}
        Icon={Package}
        iconBg={tint('--color-accent')}
        iconFg="var(--color-accent)"
        label={(row) => `Failures in ${row.label}`}
        onPick={onPick}
      />
    </SearchCardShell>
  )
}
