/**
 * Filter values that were DROPPED, waiting to be named to the user (VIZ-303,
 * VIZ-306): a release or suite from a shared link that the viewer cannot
 * access, that no longer exists, or that is past the cap; or a selection that
 * did not survive a project change.
 *
 * A filter that disappears silently leaves the reader looking at changed
 * numbers with nothing connecting them to the change, so every drop lands
 * here and the report filter bar renders it. Not persisted: a notice is about
 * this page load.
 */
import { create } from 'zustand'

export type ScopeDimension = 'release' | 'suite'

export interface DroppedNotice {
  dimension: ScopeDimension
  values: string[]
  reason: string
}

interface ScopeNoticeStore {
  notices: DroppedNotice[]
  /** Add a notice; values already named for the same dimension and reason are
   *  merged rather than repeated. Empty `values` is a no-op. */
  pushNotice: (notice: DroppedNotice) => void
  dismiss: () => void
}

export const useScopeNoticeStore = create<ScopeNoticeStore>()((set, get) => ({
  notices: [],
  pushNotice: (notice) => {
    if (notice.values.length === 0) return
    const existing = get().notices
    const same = existing.find((n) => n.dimension === notice.dimension && n.reason === notice.reason)
    if (same) {
      const merged = Array.from(new Set([...same.values, ...notice.values]))
      if (merged.length === same.values.length) return
      set({ notices: existing.map((n) => (n === same ? { ...n, values: merged } : n)) })
      return
    }
    set({ notices: [...existing, { ...notice, values: Array.from(new Set(notice.values)) }] })
  },
  dismiss: () => set({ notices: [] }),
}))

/** Reasons, as the notice states them. One place so tests and UI agree. */
export const DROP_REASONS = {
  overCap: (cap: number) => `Limit reached (${cap}) — the rest were not applied`,
  malformed: 'Not a valid value',
  unknownRelease: 'Not found in this project, or you do not have access',
  unknownSuite: 'No runs of this suite in the selected project',
  projectChanged: 'Removed because the project changed',
  needsProject: 'Pick a single project to filter by release — releases belong to one project',
} as const
