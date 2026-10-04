/**
 * The page's own suite filter, handed down to its charts (P2: "Filter page by
 * this" on the legacy scope).
 *
 * Trends, Coverage and Failures each keep a single-suite filter
 * (`usePageSuiteFilter`, page-local, no URL key) and draw it as the header's
 * "Test suite" select. A chart deep in the catalogue cannot reach that state,
 * so each of those pages provides it here around its catalogue composite, and
 * `useCrossFilter` writes a suite mark to it. A page with no provider (Suite
 * detail, Overview, Summary) has no suite filter a chart may write: the
 * action is then not offered.
 *
 * Imports no store: the multi-filter suite store is not the page's filter.
 */
import { createContext, useMemo } from 'react'

export interface PageSuiteTarget {
  /** The suite the page is filtered by, as its select shows it, or `''` for "All suites". */
  selected: string
  /** The select's options, as spelled (`SuiteFilterSelect` keys them by lower case). */
  options: readonly string[]
  /** Set the page's filter to one suite (REPLACES it: the legacy scope is single-select). */
  set: (name: string) => void
}

export const PageSuiteTargetContext = createContext<PageSuiteTarget | null>(null)

/** The provider's value, memoised: a new object per render would re-render every chart under it. */
export function usePageSuiteTarget(selected: string, options: readonly string[], set: (name: string) => void): PageSuiteTarget {
  return useMemo(() => ({ selected, options, set }), [selected, options, set])
}
