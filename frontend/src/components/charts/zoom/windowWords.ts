/**
 * How "Apply as time filter" names the page window it sets (VIZ-407, review
 * F8): in the words the app uses for that window, "Last 24 hours" for one day
 * and "Last N days" otherwise, never "last 1 day". The phrase came from the
 * report-context panel (removed in Phase D, M0); it lives here now, its only
 * user.
 */

/** The window as a heading names it: "Last 24 hours", "Last 7 days". */
export function windowText(days: number): string {
  if (days === 1) return 'Last 24 hours'
  return `Last ${days} days`
}

/** The window inside a sentence: "last 24 hours", "last 7 days". */
export function windowWords(days: number): string {
  const text = windowText(days)
  return text.charAt(0).toLowerCase() + text.slice(1)
}

/** The enabled action's visible label: it states the window it will set. */
export function applyAsWindowLabel(days: number): string {
  return `Apply as time filter: ${windowWords(days)}`
}

/** Announced once the window is set. */
export function windowAppliedAnnouncement(days: number): string {
  return `Page window set to the ${windowWords(days)}`
}
