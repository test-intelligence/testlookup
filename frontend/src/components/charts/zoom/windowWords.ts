/**
 * How "Apply as time filter" names the page window it sets (VIZ-407, review
 * F8): in the words the rest of the app uses for that window. The filter bar
 * says "Last 24 hours" and its chip "last 24 hours" for a one-day window; a
 * button offering "last 1 day", followed two seconds later by a chip saying
 * "last 24 hours", named one window twice. So the words come from the report
 * context's own `windowText` — the phrase the filter bar, the chip and the
 * context line all spell the same way — never from a day count of our own.
 */
import { windowText } from '@/components/reports/contextModel'

/** The window as the chip names it: "last 24 hours", "last 7 days". */
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
