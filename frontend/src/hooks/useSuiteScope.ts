import { useMemo, useState } from 'react'
import { useSuiteStore } from '@/store/suiteStore'
import { useMultiFiltersEnabled, useMultiFiltersState } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { useSettledScopeStore } from '@/store/settledScope'
import { normalizeScope, SCOPE_KEY_SEP, scopeArg, scopeKey } from '@/lib/scopeParams'

/** The raw selection, sorted and identity-stable (UI clock: every click). */
function useSelectedSuiteNames(): readonly string[] {
  const names = useSuiteStore((s) => s.activeSuiteNames)
  const key = scopeKey(names)
  return useMemo(() => (key === null ? [] : normalizeScope(key.split(SCOPE_KEY_SEP))), [key])
}

/**
 * The global suite selection a report should be filtered by (VIZ-303), or
 * `null` for none. Always `null` with the `viz_multi_filters` flag off — the
 * pages then keep their own local suite state, exactly as before.
 *
 * This is the DATA clock: the SETTLED selection (store/settledScope.ts), which
 * follows the picker 250 ms after the last change, so a burst of ticks is one
 * request. Sorted and identity-stable (an array in a dep list that changes
 * identity per render is a refetch loop).
 */
export function useSuiteScope(): readonly string[] | null {
  const multi = useMultiFiltersEnabled()
  const settled = useSettledScopeStore((s) => s.suiteNames)
  if (!multi || settled.length === 0) return null
  return settled
}

/** What a page with a suite dropdown needs. */
export interface PageSuiteFilter {
  /**
   * The single suite shown in the page's legacy dropdown, or `''`. With
   * several suites selected (flag on) it is `''` — use `suiteFilter` for data
   * and `suiteLabel` for text, never this.
   */
  selectedSuite: string
  /** Set from the page's single-suite dropdown (`''` clears). */
  setSelectedSuite: (value: string) => void
  /** For data hooks and `suite_name` params: `null`, one name (scalar — the
   *  legacy wire), or several (sorted array → repeated `suite_name`). Flag on,
   *  this is the SETTLED selection (250 ms after the last change); every other
   *  field follows the click. */
  suiteFilter: string | string[] | null
  /** Every selected name (empty for none) — the UI clock: for text and
   *  controls, not for building a request. */
  suiteNames: readonly string[]
  /** Human text for the selection: `''`, `"payments"`, `"payments, cart"`. */
  suiteLabel: string
  /** Label for the legacy dropdown when several suites are selected
   *  (`"2 suites"`), else `undefined`. */
  multiLabel: string | undefined
  /**
   * False while the `viz_multi_filters` flag has not answered. The fields
   * above then describe the page-local (flag-off) state, which may be about
   * to be replaced by the global selection: a page must not ACT on a suite
   * change in that window (e.g. redirect) — the flag resolving is not the
   * user changing the suite.
   */
  flagResolved: boolean
}

/**
 * The page-level suite filter. Replaces the page-local
 * `const [selectedSuite, setSelectedSuite] = useState('')` that each report
 * page used to hold (VIZ-303).
 *
 * Flag OFF: it IS that local state — same initial `''`, same setter, the
 * suite forgotten on navigation — so the page behaves byte-for-byte as it did.
 * Flag ON: it reads and writes the global suite store, so the selection
 * follows the user from page to page and into the URL.
 */
export function usePageSuiteFilter(): PageSuiteFilter {
  const multi = useMultiFiltersEnabled()
  const flagResolved = useMultiFiltersState() !== 'unknown'
  const [localSuite, setLocalSuite] = useState('')
  // Two clocks: the dropdown, label and chips follow the click; the data
  // filter (`suiteFilter`) follows the settled scope.
  const selectedNames = useSelectedSuiteNames()
  const settledNames = useSuiteScope()
  const setActiveSuites = useSuiteStore((s) => s.setActiveSuites)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)

  return useMemo<PageSuiteFilter>(() => {
    if (!multi) {
      return {
        selectedSuite: localSuite,
        setSelectedSuite: setLocalSuite,
        suiteFilter: localSuite || null,
        suiteNames: localSuite ? [localSuite] : [],
        suiteLabel: localSuite,
        multiLabel: undefined,
        flagResolved,
      }
    }
    const names = selectedNames
    return {
      selectedSuite: names.length === 1 ? names[0] : '',
      setSelectedSuite: (value: string) => {
        setActiveSuites(value ? [value] : [], activeProjectId)
      },
      suiteFilter: scopeArg(settledNames),
      suiteNames: names,
      suiteLabel: names.join(', '),
      multiLabel: names.length > 1 ? `${names.length} suites` : undefined,
      flagResolved,
    }
  }, [multi, flagResolved, localSuite, selectedNames, settledNames, setActiveSuites, activeProjectId])
}
