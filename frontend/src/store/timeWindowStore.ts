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

/** Default time window — 7 days. Updated 2026-05-18 from 24h based on
 *  user feedback that "7d is the more useful starting view for trends,
 *  coverage, and the inbox; 24h is too noisy for a first impression."
 *  Pages with a different intrinsic default (e.g. Live, which is always
 *  current) snap via ``snapToAllowed`` to the closest in-range option. */
export const DEFAULT_TIME_WINDOW_DAYS = 7

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
      // v2 (2026-05-18): default flipped from 24h → 7d. Bumping the
      // version forces ``zustand/persist`` to re-seed any stored state
      // that hasn't been touched, so users who previously took the
      // implicit-default 24h pick up the new default on their next visit
      // (users who explicitly picked 24h still see 24h — the migrate
      // step below preserves any non-default value).
      version: 2,
      migrate: (persistedState, fromVersion) => {
        // v1 → v2: only re-seed if the user was on the OLD default (1).
        // Any other explicit selection is preserved. ``persistedState``
        // shape from v1 is ``{ days: number }``.
        const state = (persistedState as { days?: number } | undefined) ?? {}
        if (fromVersion < 2 && state.days === 1) {
          return { ...state, days: DEFAULT_TIME_WINDOW_DAYS }
        }
        return state as unknown
      },
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
