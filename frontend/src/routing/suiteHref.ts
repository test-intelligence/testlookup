/**
 * The Suite page of a suite known only by its NAME (an analytics row: Coverage's
 * breakdown, Trends' suite pass rates, Summary's suite table). UX redesign P4:
 * the suite page is `/suites/:id`; `/coverage/suite?name=` resolves the name to
 * the id (`SuiteByNameRedirect`) and opens its Charts tab, or the suites list
 * when no suite has that name. One hop, and no suite list fetched up front on
 * the page that shows the names.
 */
export function suiteHrefByName(name: string): string {
  return `/coverage/suite?name=${encodeURIComponent(name)}`
}
