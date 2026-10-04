/**
 * "Filter page by this" (VIZ-603, P2): promote a chart mark to a PAGE filter,
 * on the legacy scope every page already has.
 *
 * No mechanism of its own: the mark's value is written to the filter the page
 * already draws, so the control the reader would have used IS the undo.
 *
 *   - a suite goes to the page's own "Test suite" select (`usePageSuiteFilter`,
 *     reached through `PageSuiteTargetContext`, provided by Trends, Coverage
 *     and Failures around their catalogue composites); "All suites" clears it;
 *   - a release goes to the top bar's release picker
 *     (`releaseStore.setActiveRelease`); the picker mirrors it to `?release=`
 *     and drops an id its list does not hold; "All releases" clears it.
 *
 * Rules (owner decision 2026-10-04: the multi-filter runtime is off for good,
 * so nothing here reads `viz_multi_filters` or its stores):
 *   - only a mark that IS a suite or a release can filter, and with it the
 *     suite or release it sits in (a suite x release heatmap cell writes
 *     both). Any other mark (status, failure category, test, class, ...) is
 *     never offered: the page has no filter to write it to;
 *   - a suite needs the page's provider (no provider, no suite filter: Suite
 *     detail, Overview, Summary); a release needs one pinned project (a release
 *     belongs to one project, and the picker is disabled in All Projects);
 *   - a write REPLACES the filter: the legacy scope is single-select;
 *   - a suite is written as the select spells it (its options are keyed by
 *     lower case: the chart's lower-cased key beside the option's own spelling
 *     would be a second option for the same suite);
 *   - what was applied, and how to clear it, is said through the page's one
 *     announcer and shown as a toast.
 *
 * Project is not a target: no chart in the kit draws project marks (it would
 * need an All Projects per-project chart first).
 */
import { useCallback, useContext, useMemo } from 'react'
import toast from 'react-hot-toast'
import { useChartAnnouncer } from '@/components/charts/ChartAnnouncer'
import type { ChartMark } from '@/components/charts/marks'
import { isValidSuiteName } from '@/lib/suiteName'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { isRestorableReleaseId, useReleaseStore } from '@/store/releaseStore'
import { PageSuiteTargetContext, type PageSuiteTarget } from './pageSuiteTarget'
import { useReleases } from './useReleases'

/** chart-data's key for "no suite" and the roll-up of the rest: neither is one suite a page can be filtered by. */
const NOT_A_SUITE = new Set(['(none)', '__other__'])
/** The release key of the runs no release claims (`lib/scopeUrl` `UNATTRIBUTED`, the backend's sentinel). */
const UNATTRIBUTED = 'unattributed'
/** One toast, replaced by the next: a second filter does not stack a second toast. */
const TOAST_ID = 'cross-filter'

export type CrossFilterDimension = 'suite' | 'release'

/** One filter a mark can write. */
export interface CrossFilterTarget {
  dimension: CrossFilterDimension
  /** A suite: the spelling to fall back to when the page's select does not list it. A release: its id (or `unattributed`). */
  value: string
  /** What the chart called it, when the mark itself is this target; `null` for a context dimension. */
  label: string | null
}

export type CrossFilterResult =
  /** At least one filter was written. */
  | 'applied'
  /** The page is already filtered by every target: nothing written. */
  | 'already'
  /** Not a mark that can filter this page. */
  | 'invalid'

/** "suite "Payments"", "release "2026.09"": how a filter is named to the reader. */
export const filterPhrase = (dimension: CrossFilterDimension, name: string) => `${dimension} "${name}"`

/** The control that clears each filter. */
const CLEAR_CONTROL: Record<CrossFilterDimension, string> = { suite: '"All suites"', release: '"All releases"' }

/** What the reader hears and sees. Exported so the specs assert the words rather than retype them. */
export const CROSS_FILTER_WORDS = {
  applied: (phrases: readonly string[], clears: readonly string[]) =>
    `Page filtered by ${phrases.join(' and ')} (clear: ${clears.join(', ')}).`,
  already: (phrases: readonly string[]) => `The page is already filtered by ${phrases.join(' and ')}.`,
} as const

const fold = (text: string) => text.trim().toLowerCase()

/** One selector as a target, or `null` when it is not a suite or release the page can be filtered by. */
function asTarget(dimension: string, value: string, label: string | null): CrossFilterTarget | null {
  if (dimension === 'suite') {
    const key = fold(value)
    if (NOT_A_SUITE.has(key)) return null
    // The chart's key is lower-cased; the label is the suite as spelled when it is the same suite.
    const spelled = label !== null && fold(label) === key ? label : value
    return isValidSuiteName(spelled) ? { dimension: 'suite', value: spelled, label } : null
  }
  if (dimension === 'release') return isRestorableReleaseId(value) ? { dimension: 'release', value, label } : null
  return null
}

/**
 * The filters `mark` can write, at most one per dimension: the mark's own
 * (it must itself be a suite or a release), then the suite or release it sits
 * in (`context`). A suite x release cell is both; a suite x day or suite x
 * environment cell, a ladder bar or a depth-1 treemap node is its suite.
 */
export function crossFilterTargets(mark: ChartMark): CrossFilterTarget[] {
  const own = asTarget(mark.dimension, mark.value, mark.label)
  if (!own) return []
  const out = [own]
  for (const selector of mark.context ?? []) {
    if (out.some((t) => t.dimension === selector.dimension)) continue
    const target = asTarget(selector.dimension, selector.value, null)
    if (target) out.push(target)
  }
  return out
}

/** The select's own spelling of the suite (mandatory: see the header), else the target's. */
export function suiteSpelling(options: readonly string[], target: CrossFilterTarget): string {
  const key = fold(target.value)
  return options.find((option) => fold(option) === key) ?? target.value
}

export interface CrossFilter {
  /** Whether "Filter page by this" is offered for `mark` on this page. */
  offers: (mark: ChartMark) => boolean
  /** Write `mark`'s filters to the page (replacing), and say what happened. */
  apply: (mark: ChartMark) => CrossFilterResult
}

export function useCrossFilter(): CrossFilter {
  const suites: PageSuiteTarget | null = useContext(PageSuiteTargetContext)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  // The top bar's list, read from its cache entry (no request of its own): only for the release's name.
  const { data: releaseList } = useReleases(undefined, { cached: true })
  const announcer = useChartAnnouncer()
  const pinned = activeProjectId !== null && activeProjectId !== ALL_PROJECTS_ID

  const usable = useCallback(
    (mark: ChartMark) => crossFilterTargets(mark).filter((t) => (t.dimension === 'suite' ? suites !== null : pinned)),
    [pinned, suites],
  )

  const offers = useCallback((mark: ChartMark) => usable(mark).length > 0, [usable])

  const releaseName = useCallback(
    (target: CrossFilterTarget) => {
      if (target.value === UNATTRIBUTED) return 'Unattributed'
      const listed = releaseList?.items?.find((r) => r.id === target.value)
      return listed?.name ?? target.label ?? target.value
    },
    [releaseList],
  )

  const apply = useCallback(
    (mark: ChartMark): CrossFilterResult => {
      const targets = usable(mark)
      if (targets.length === 0) return 'invalid'
      const phrases: string[] = []
      const clears: string[] = []
      let wrote = false
      for (const target of targets) {
        if (target.dimension === 'suite' && suites) {
          const name = suiteSpelling(suites.options, target)
          phrases.push(filterPhrase('suite', name))
          if (fold(suites.selected) !== fold(name)) {
            suites.set(name)
            wrote = true
          }
        } else if (target.dimension === 'release' && pinned) {
          phrases.push(filterPhrase('release', releaseName(target)))
          const store = useReleaseStore.getState()
          if (store.activeReleaseId !== target.value || store.scopedProjectId !== activeProjectId) {
            store.setActiveRelease(target.value, activeProjectId)
            wrote = true
          }
        }
        clears.push(CLEAR_CONTROL[target.dimension])
      }
      const words = wrote ? CROSS_FILTER_WORDS.applied(phrases, clears) : CROSS_FILTER_WORDS.already(phrases)
      // The reader's own action: spoken at once, and shown.
      announcer?.assertive(words)
      toast(words, { id: TOAST_ID })
      return wrote ? 'applied' : 'already'
    },
    [activeProjectId, announcer, pinned, releaseName, suites, usable],
  )

  return useMemo(() => ({ offers, apply }), [apply, offers])
}
