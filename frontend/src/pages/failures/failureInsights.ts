/**
 * W07 FAIL-01 / FAIL-02: the Failures page's pure helpers for the measured
 * time to fix and the per-failure detail, outside the page module so they are
 * testable alone and the page keeps exporting components only where it can.
 */
import type { TimeToFixResponse, TopFailingItem } from '@/types/analytics'

// ── FAIL-02: time to fix ──────────────────────────────────────────────────

/** The median scores 100 at 0 h and 0 at a week or more, linearly. */
export const TIME_TO_FIX_ZERO_HOURS = 168
/** Tests that failed and recovered this many times in the window are left out (the server's `TIME_TO_FIX_OSCILLATION_MIN`). */
export const TIME_TO_FIX_OSCILLATION_MIN = 3
/**
 * Hours below this read in hours, from it in days. The contract's examples fix
 * it between 30 and 43.2 (30 h → `30.0 h`, 43.2 h → `1.8 d`): a day and a half.
 */
export const FIX_TIME_HOURS_LIMIT = 36

/** `clamp(100 - median / 168 × 100, 0, 100)`. */
export function timeToFixScore(medianHours: number): number {
  return Math.min(100, Math.max(0, 100 - (medianHours / TIME_TO_FIX_ZERO_HOURS) * 100))
}

/** Under 36 h in hours (`30.0 h`), else in days (`1.8 d`). */
export function formatFixTime(hours: number): string {
  return hours < FIX_TIME_HOURS_LIMIT ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`
}

/** A measured time to fix: the server said so AND sent a median. */
export function measuredTimeToFix(t: TimeToFixResponse | undefined): t is TimeToFixResponse & { median_hours: number } {
  return Boolean(t?.measured) && typeof t?.median_hours === 'number' && Number.isFinite(t.median_hours)
}

/** The tile's hover text for a measured median: what is counted and what is left out. */
export function timeToFixHint(t: TimeToFixResponse): string {
  const excluded = t.excluded_flaky_tests + t.excluded_oscillating_tests
  return `First failure → next pass, per test, over the last ${t.window_days} days. `
    + `Tests flagged flaky and tests that recovered ${TIME_TO_FIX_OSCILLATION_MIN}+ times are left out (${excluded}); `
    + `${t.open} still failing are not counted. `
    + 'A failure already open when the window starts counts from its first failure inside it.'
}

// ── FAIL-01: the owner chip ───────────────────────────────────────────────

/** The words after the owner's name, by where the owner came from (`owner_source`). */
const OWNER_SOURCE_WORDS: Readonly<Record<string, string>> = {
  assignee: 'assignee',
  rule: 'ownership rule',
  component_map: 'component map',
  test_label: 'owner label',
}

/** `Priya Nair · assignee · Payments`, `Payments · ownership rule`, or null (Unassigned). */
export function ownerChipText(test: Pick<TopFailingItem, 'owner' | 'owner_source' | 'owner_team'>): string | null {
  const owner = test.owner?.trim()
  if (!owner) return null
  const source = test.owner_source ? OWNER_SOURCE_WORDS[test.owner_source] : undefined
  const parts = [owner]
  if (source) parts.push(source)
  if (test.owner_source === 'assignee' && test.owner_team?.trim()) parts.push(test.owner_team.trim())
  return parts.join(' · ')
}
