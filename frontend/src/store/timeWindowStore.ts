/**
 * Global time-window preference — shared by every page that has a
 * "Last 24h / 7d / 30d / 90d" filter.
 *
 * Why a store: the user expects the window selection to follow them.
 * Pick 24h on /reports/summary → /my-failures, /coverage, /trends, /live,
 * /runs, /failures, /overview all open at 24h. Previously each page had
 * its own ``tl.<page>.window`` localStorage key, which broke that
 * expectation. One source of truth fixes it.
 *
 * Why Zustand: the standing rule (see ``frontend/CLAUDE.md``) is "no new
 * Zustand stores without strong reason" — a user-level preference that
 * has to be read + written from a dozen pages with cross-page reactivity
 * is the canonical strong reason.
 *
 * Pages may have non-overlapping option sets (e.g. ``LiveExecutionPage``
 * offers 14d but ``SummaryReportPage`` doesn't). Use the exported
 * ``snapToAllowed`` helper to normalise the shared value against a
 * page's allowed set instead of forcing every page to expose the same
 * options.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** Default time window — 24 hours. Matches the user's request that 24h be
 *  the starting point and only change when they explicitly pick something else. */
export const DEFAULT_TIME_WINDOW_DAYS = 1

interface TimeWindowStore {
  /** Current global window in days. ``1`` = last 24 hours. */
  days: number
  setDays: (days: number) => void
}

export const useTimeWindowStore = create<TimeWindowStore>()(
  persist(
    (set) => ({
      days: DEFAULT_TIME_WINDOW_DAYS,
      setDays: (days: number) => set({ days }),
    }),
    {
      name: 'testlookup-time-window',
      // Future-proofing: bump the version when the schema of stored state
      // changes (e.g. if we ever add per-section overrides). Today there's
      // only one field so a migrate function isn't needed.
      version: 1,
    },
  ),
)

/**
 * Snap a global window to the closest value in a page's allowed set.
 *
 * Used by pages whose option lists differ from the union — e.g. a page
 * that doesn't offer 14d shouldn't fail open when the user happens to
 * have picked 14d elsewhere. The "closest" rule means picking 90d on
 * Summary still maps to 30d (the largest available) on Live, which is
 * a less surprising fallback than "default to the page's hard-coded
 * starting point and lose the user's recency intent."
 */
export function snapToAllowed<T extends number>(
  value: number,
  allowed: readonly T[],
): T {
  if (allowed.length === 0) return value as T  // shouldn't happen
  if ((allowed as readonly number[]).includes(value)) return value as T
  let best: T = allowed[0]
  let bestDelta = Math.abs(allowed[0] - value)
  for (const candidate of allowed) {
    const delta = Math.abs(candidate - value)
    if (delta < bestDelta) {
      best = candidate
      bestDelta = delta
    }
  }
  return best
}
