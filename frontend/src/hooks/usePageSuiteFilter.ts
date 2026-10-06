import { useMemo, useState } from 'react'

/** What a page with a suite dropdown needs. */
export interface PageSuiteFilter {
  /** The suite shown in the page's dropdown, or `''`. */
  selectedSuite: string
  /** Set from the page's dropdown (`''` clears). */
  setSelectedSuite: (value: string) => void
  /** For data hooks and `suite_name` params: `null`, or the one name. */
  suiteFilter: string | null
  /** The selected name as a list (empty for none). */
  suiteNames: readonly string[]
  /** Human text for the selection: `''` or the name. */
  suiteLabel: string
}

/**
 * The page-level suite filter: the page-local
 * `const [selectedSuite, setSelectedSuite] = useState('')` each report page
 * used to hold, behind one interface. The suite is forgotten on navigation.
 *
 * (VIZ-303 also gave it a global, multi-select mode behind `viz_multi_filters`;
 * that flag stayed off for good and the mode was removed in Phase D, M1-M3.
 * Cross-filter writes this filter through `pageSuiteTarget`.)
 */
export function usePageSuiteFilter(): PageSuiteFilter {
  const [localSuite, setLocalSuite] = useState('')
  return useMemo<PageSuiteFilter>(
    () => ({
      selectedSuite: localSuite,
      setSelectedSuite: setLocalSuite,
      suiteFilter: localSuite || null,
      suiteNames: localSuite ? [localSuite] : [],
      suiteLabel: localSuite,
    }),
    [localSuite],
  )
}
