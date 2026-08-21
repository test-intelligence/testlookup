/**
 * "Nothing in this window" vs "nothing at all" — and what to suggest.
 *
 * The dashboard rendered `0` / `—` across every KPI whenever the selected
 * time window contained no runs, with no indication of why. On the measured
 * deployment the newest run in every active project was 14–16 days old, so a
 * 7- or 14-day window was *correctly* empty and looked identical to a broken
 * page. `DEFAULT_TIME_WINDOW_DAYS` was already moved 7 → 30 for this exact
 * complaint, but a user who picks 7 themselves still gets the silent version.
 *
 * The two cases need different copy, and conflating them is the actual defect:
 * telling someone with no data at all to "widen the window" sends them round a
 * loop that cannot help.
 */

export type EmptyWindowState =
  | { kind: 'has-data' }
  | { kind: 'no-runs-at-all' }
  | {
      kind: 'outside-window'
      /** Whole days between the newest run and now (never negative). */
      ageDays: number
      /** Smallest allowed window that would include it, or null if none does. */
      suggestedDays: number | null
    }

export interface EmptyWindowInput {
  /** Executions reported for the selected window. */
  totalInWindow: number | null | undefined
  /** ISO timestamp of the newest run **ignoring** the window, if any. */
  newestRunAt: string | null | undefined
  /** The selected window, in days. */
  days: number
  /** Windows the picker offers, ascending. */
  options: readonly number[]
  /** Injected so this is deterministic under test. */
  now?: Date
}

const DAY_MS = 24 * 60 * 60 * 1000

export function describeEmptyWindow({
  totalInWindow,
  newestRunAt,
  days,
  options,
  now = new Date(),
}: EmptyWindowInput): EmptyWindowState {
  if (totalInWindow != null && totalInWindow > 0) return { kind: 'has-data' }

  if (!newestRunAt) return { kind: 'no-runs-at-all' }

  const newest = new Date(newestRunAt)
  if (Number.isNaN(newest.getTime())) return { kind: 'no-runs-at-all' }

  // Floor, and clamp at 0: a run finished 3 hours ago is "0 days" old, not
  // negative, and a clock skew that puts it slightly in the future must not
  // render "-1 days ago".
  const ageDays = Math.max(0, Math.floor((now.getTime() - newest.getTime()) / DAY_MS))

  // Only suggest a window that is BOTH larger than the current one and large
  // enough to reach the run. Suggesting the window they are already on, or one
  // that still excludes the data, is worse than saying nothing.
  const suggestedDays =
    options.filter((d) => d > days && d >= ageDays + 1).sort((a, b) => a - b)[0] ?? null

  return { kind: 'outside-window', ageDays, suggestedDays }
}

/** Human phrasing for an age in whole days. */
export function formatAgeDays(ageDays: number): string {
  if (ageDays === 0) return 'earlier today'
  if (ageDays === 1) return 'yesterday'
  return `${ageDays} days ago`
}
