// Fire-and-forget bookkeeping that should reach the server once per browser
// tab, not once per page open (D-32: the run page sent
// POST /onboarding/<project>/complete every time it opened).

const reported = new Set<string>()

/** True the first time `key` is seen in this tab; false after that. */
export function firstReportInTab(key: string): boolean {
  if (reported.has(key)) return false
  reported.add(key)
  return true
}

/** Tests only: forget every key. */
export function resetReportsInTab(): void {
  reported.clear()
}
