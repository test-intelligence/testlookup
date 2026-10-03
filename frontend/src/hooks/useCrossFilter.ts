/**
 * "Filter page by this" (VIZ-603): promote a chart mark to a PAGE filter.
 *
 * No mechanism of its own (EPIC "Writes to the same scope stores as VIZ-303"):
 * the mark's value is APPENDED to the global multi-selection in the existing
 * stores (`suiteStore`, `releaseStore`). Every catalogue chart already reads
 * those through the 250 ms settled scope, the report chrome draws the chip
 * ("Suite: payments") and `useScopeUrlSync` puts it in the address bar. So
 * removing that chip IS the undo: the selection is back to what it was.
 *
 * Rules (OD-2, plan 3.4.2):
 *   - only `suite` and `release` marks can filter: they are the only global
 *     dimensions. Any other mark (status, failure category, test, ...) is
 *     never offered; the EPIC's page-local chip is not built this wave;
 *   - only with `viz_multi_filters` ON: with it off the stores are not the
 *     page's filter (each page keeps its own local suite state), so the action
 *     is ABSENT, never disabled;
 *   - the caps (`SUITE_CAP`, `RELEASE_CAP`) are honoured by refusing, not by
 *     replacing: a full selection is left as it is, the notice store names
 *     the value that was not added, and the reader hears "Filter limit reached";
 *   - a release belongs to one project: in All Projects mode it is refused
 *     with the store's own reason; a selection made in another project is not
 *     appended to (it would filter this project by foreign ids);
 *   - the result of the reader's own action is spoken at once, through the
 *     page's one announcer.
 */
import { useCallback, useMemo } from 'react'
import { useChartAnnouncer } from '@/components/charts/ChartAnnouncer'
import type { ChartMark } from '@/components/charts/marks'
import { useMultiFiltersEnabled } from '@/store/multiFiltersFlag'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { isRestorableReleaseId, RELEASE_CAP, selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { DROP_REASONS, useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { isValidSuiteName, SUITE_CAP, useSuiteStore } from '@/store/suiteStore'

/** chart-data's key for "no suite" and the roll-up of the rest: neither is one suite a page can be filtered by. */
const NOT_A_SUITE = new Set(['(none)', '__other__'])

export type CrossFilterDimension = 'suite' | 'release'

export type CrossFilterResult =
  /** Appended to the page's selection. */
  | 'added'
  /** Already in the selection: nothing written. */
  | 'already'
  /** The selection is full: nothing written, the notice names the value. */
  | 'limit'
  /** A release in All Projects mode: nothing written, the notice says why. */
  | 'needs-project'
  /** Not a mark that can filter the page. */
  | 'invalid'
  /** `viz_multi_filters` is off: there is no page filter to write to. */
  | 'unavailable'

const NOUN: Record<CrossFilterDimension, string> = { suite: 'suite', release: 'release' }

/** What the reader hears. Exported so the specs assert the words rather than retype them. */
export const CROSS_FILTER_WORDS = {
  added: (noun: string, label: string) => `Page filtered by ${noun} ${label}.`,
  already: (noun: string, label: string) => `The page is already filtered by ${noun} ${label}.`,
  limit: (cap: number) => `Filter limit reached (${cap}): the page filter was not changed.`,
  needsProject: 'Pick one project to filter by release: a release belongs to one project.',
} as const

/**
 * The page-filter value a mark stands for, or `null` when it cannot filter the
 * page. A suite is written as the chart DREW it when that is the same suite as
 * its key (the key is lower-cased; the picker and the chip show the suite as
 * spelled, and the server matches suites without regard to case), else as the
 * key. A release is always its id (or `unattributed`), never its name.
 */
export function crossFilterValue(mark: ChartMark): { dimension: CrossFilterDimension; value: string } | null {
  if (mark.dimension === 'suite') {
    const key = mark.value.trim().toLowerCase()
    if (NOT_A_SUITE.has(key)) return null
    const value = mark.label.trim().toLowerCase() === key ? mark.label : mark.value
    return isValidSuiteName(value) ? { dimension: 'suite', value } : null
  }
  if (mark.dimension === 'release') {
    return isRestorableReleaseId(mark.value) ? { dimension: 'release', value: mark.value } : null
  }
  return null
}

export interface CrossFilter {
  /** `viz_multi_filters` is on: the page has a global filter to write to. */
  enabled: boolean
  /** Whether "Filter page by this" is offered for `mark`. */
  offers: (mark: ChartMark) => boolean
  /** Append `mark`'s value to the page filter (and say what happened). */
  apply: (mark: ChartMark) => CrossFilterResult
}

export function useCrossFilter(): CrossFilter {
  const enabled = useMultiFiltersEnabled()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const announcer = useChartAnnouncer()

  const offers = useCallback((mark: ChartMark) => enabled && crossFilterValue(mark) !== null, [enabled])

  const apply = useCallback(
    (mark: ChartMark): CrossFilterResult => {
      if (!enabled) return 'unavailable'
      const target = crossFilterValue(mark)
      if (!target) return 'invalid'
      const say = (text: string) => announcer?.assertive(text)
      const { pushNotice } = useScopeNoticeStore.getState()
      const noun = NOUN[target.dimension]

      if (target.dimension === 'suite') {
        const store = useSuiteStore.getState()
        const current = store.activeSuiteNames
        const wanted = target.value.trim().toLowerCase()
        if (current.some((name) => name.trim().toLowerCase() === wanted)) {
          say(CROSS_FILTER_WORDS.already(noun, mark.label))
          return 'already'
        }
        if (current.length >= SUITE_CAP) {
          pushNotice({ dimension: 'suite', values: [target.value], reason: DROP_REASONS.overCap(SUITE_CAP) })
          say(CROSS_FILTER_WORDS.limit(SUITE_CAP))
          return 'limit'
        }
        store.setActiveSuites([...current, target.value], activeProjectId)
        say(CROSS_FILTER_WORDS.added(noun, mark.label))
        return 'added'
      }

      if (!activeProjectId || activeProjectId === ALL_PROJECTS_ID) {
        pushNotice({ dimension: 'release', values: [target.value], reason: DROP_REASONS.needsProject })
        say(CROSS_FILTER_WORDS.needsProject)
        return 'needs-project'
      }
      const store = useReleaseStore.getState()
      const current = store.scopedProjectId === activeProjectId ? selectReleaseIds(store) : []
      if (current.includes(target.value)) {
        say(CROSS_FILTER_WORDS.already(noun, mark.label))
        return 'already'
      }
      if (current.length >= RELEASE_CAP) {
        pushNotice({ dimension: 'release', values: [target.value], reason: DROP_REASONS.overCap(RELEASE_CAP) })
        say(CROSS_FILTER_WORDS.limit(RELEASE_CAP))
        return 'limit'
      }
      store.setActiveReleases([...current, target.value], activeProjectId)
      say(CROSS_FILTER_WORDS.added(noun, mark.label))
      return 'added'
    },
    [activeProjectId, announcer, enabled],
  )

  return useMemo(() => ({ enabled, offers, apply }), [apply, enabled, offers])
}
