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

/** Default time window — 30 days.
 *
 *  History: 24h → 7d (2026-05-18, "24h is too noisy for a first
 *  impression") → 30d (2026-08-15).
 *
 *  The 7-day default assumed a project ships runs most days. When one does
 *  not, every windowed surface empties out and the product looks broken —
 *  which is exactly what was reported: four projects whose most recent runs
 *  were 8–10 days old rendered as "no records", and the intelligence page
 *  went as far as calling that "All clear". 30 days is long enough that a
 *  quiet fortnight does not read as an outage, and it is present in every
 *  page's allowed option set, so ``snapToAllowed`` returns it exactly rather
 *  than rounding to a neighbour.
 *
 *  Pages with a different intrinsic range (e.g. Live, which is always
 *  current) still snap via ``snapToAllowed``. */
export const DEFAULT_TIME_WINDOW_DAYS = 30

/** Defaults this store has shipped, newest first. A stored value equal to a
 *  PREVIOUS default is indistinguishable from "never chose", which is what
 *  the migration below re-seeds. Kept as data so adding a future default
 *  cannot forget to list the one it replaces. */
const SUPERSEDED_DEFAULTS = [7, 1] as const

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
      version: 3,
      migrate: (persistedState, fromVersion) => {
        // Re-seed anyone sitting on a SUPERSEDED default, and leave every
        // other value alone.
        //
        // Honest limitation: a stored 7 from someone who deliberately chose
        // 7 is byte-identical to a 7 nobody ever touched, so this moves both.
        // The store records a number, not an intent. Preferring to re-seed is
        // the lesser harm — the cost of moving a deliberate choice is one
        // click, and the cost of leaving a stale implicit default is a
        // product that looks empty.
        const state = (persistedState as { days?: number } | undefined) ?? {}
        if (
          fromVersion < 3 &&
          typeof state.days === 'number' &&
          (SUPERSEDED_DEFAULTS as readonly number[]).includes(state.days)
        ) {
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
