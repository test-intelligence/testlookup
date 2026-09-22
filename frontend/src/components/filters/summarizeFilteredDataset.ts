/**
 * The filtered-dataset summary line (VIZ-305), as a pure function of `meta`.
 *
 *   filtered    "Showing 18 of 143 runs · 412 of 3,960 executions · 2 releases · 1 suite · last 30 days"
 *   unfiltered  "Showing all 143 runs · 3,960 executions · last 30 days"
 *   matched 0   → `{ kind: 'empty' }`: the caller shows the filtered-empty copy
 *   no totals   → `null`: an older cached payload without `totals` gets NO
 *                 line — never one guessed from other fields.
 *
 * "Filtered" is read from what the server APPLIED (`scope.releases` /
 * `scope.suites`), matching the context header. Totals ignore the release and
 * suite filters but respect project and window (C2), so matched ≤ total.
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'

export type FilteredSummary =
  | { kind: 'text'; text: string; filtered: boolean }
  | { kind: 'empty'; filtered: boolean }

const plural = (n: number, one: string, many: string) => `${formatNumber(n)} ${n === 1 ? one : many}`

const windowPhrase = (days: number) => (days === 1 ? 'last 24 hours' : `last ${days} days`)

type Totals = EnvelopeMeta['totals']

const isCount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0

function readTotals(meta: Partial<EnvelopeMeta> | null | undefined): Totals | null {
  const totals = meta?.totals as Partial<Totals> | null | undefined
  if (!totals) return null
  const { matched_runs, total_runs, matched_executions, total_executions } = totals
  if (!isCount(matched_runs) || !isCount(total_runs) || !isCount(matched_executions) || !isCount(total_executions)) {
    return null
  }
  return { matched_runs, total_runs, matched_executions, total_executions }
}

export function summarizeFilteredDataset(meta: Partial<EnvelopeMeta> | null | undefined): FilteredSummary | null {
  const totals = readTotals(meta)
  const scope = meta?.scope
  if (!totals || !scope) return null
  const releases = scope.releases?.length ?? 0
  const suites = scope.suites?.length ?? 0
  const filtered = releases > 0 || suites > 0
  if (totals.matched_runs === 0) return { kind: 'empty', filtered }

  const parts: string[] = []
  if (filtered) {
    parts.push(`Showing ${formatNumber(totals.matched_runs)} of ${plural(totals.total_runs, 'run', 'runs')}`)
    parts.push(`${formatNumber(totals.matched_executions)} of ${plural(totals.total_executions, 'execution', 'executions')}`)
    if (releases > 0) parts.push(plural(releases, 'release', 'releases'))
    if (suites > 0) parts.push(plural(suites, 'suite', 'suites'))
  } else {
    parts.push(`Showing all ${plural(totals.total_runs, 'run', 'runs')}`)
    parts.push(plural(totals.total_executions, 'execution', 'executions'))
  }
  if (scope.window && isCount(scope.window.days)) parts.push(windowPhrase(scope.window.days))
  return { kind: 'text', text: parts.join(' · '), filtered }
}

/** The compact form for a `ChartFrame` footer whose scope differs from the page's. */
export function compactFilteredSummary(meta: Partial<EnvelopeMeta> | null | undefined): string | null {
  const totals = readTotals(meta)
  if (!totals) return null
  return `${formatNumber(totals.matched_runs)}/${formatNumber(totals.total_runs)} runs · ${formatNumber(
    totals.matched_executions,
  )}/${formatNumber(totals.total_executions)} executions`
}
