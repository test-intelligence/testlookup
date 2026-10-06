/**
 * Helpers for the page suite `<select>` controls (`SuiteFilterSelect`, the
 * Overview suite control). Kept out of the component files so those export
 * components only (react-refresh).
 */

/**
 * The options a page suite `<select>` should list: the page's own options,
 * plus the selected suite when the page does not list it (a suite set by
 * "Filter page by this" from a chart, VIZ-603, may be one the page's recent
 * runs do not list). Without
 * it the `<select>` falls back to its first option and reads "All suites"
 * while the page is in fact filtered (E3 review, m4).
 */
export function suiteSelectOptions(options: readonly string[], selected: string): string[] {
  if (!selected || options.includes(selected)) return [...options]
  return [selected, ...options]
}
