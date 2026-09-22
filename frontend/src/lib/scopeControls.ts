/**
 * Helpers for the single-value header controls of the multi-value report
 * scope (VIZ-303): `ScopeSummaryButton`, `SuiteFilterSelect`, the Overview
 * suite control. Kept out of the component files so those export components
 * only (react-refresh).
 */

/** The report dimensions the filter bar edits. */
export type ReportFilterDimension = 'release' | 'suite'

const FOCUSABLE = 'button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * The report filter bar's control for one dimension, if the page renders one
 * and it can take focus. The bar marks it `data-report-filter="<dimension>"`
 * (on the control itself or on a wrapper around it).
 */
export function findReportFilterControl(dimension: ReportFilterDimension): HTMLElement | null {
  const marked = document.querySelector<HTMLElement>(`[data-report-filter="${dimension}"]`)
  if (!marked) return null
  if (marked.matches(FOCUSABLE)) return marked
  return marked.querySelector<HTMLElement>(FOCUSABLE)
}

/** "Several values are selected": no single menu option is the selection. */
export const SEVERAL_SELECTED: readonly string[] = ['', '']

/**
 * The options a page suite `<select>` should list: the page's own options,
 * plus the selected suite when the page does not list it (a suite chosen on
 * another page, or from a link — VIZ-303 makes the selection global). Without
 * it the `<select>` falls back to its first option and reads "All suites"
 * while the page is in fact filtered (E3 review, m4).
 */
export function suiteSelectOptions(options: readonly string[], selected: string): string[] {
  if (!selected || options.includes(selected)) return [...options]
  return [selected, ...options]
}
