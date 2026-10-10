/**
 * The plain rules behind Flaky tests (`/flaky`, UX redesign P4): the header's
 * count line and the quarantine request states each tab shows. Kept out of the
 * page modules so those export only components.
 */
import type { FlakyCoachResponse } from '@/services/testHealthService'
import type { QuarantineStatsResponse, QuarantineStatus } from '@/services/flakyQuarantineService'

/**
 * The header line of the flaky-test list.
 *
 * BUG-007: the summary report's "Flaky" tile counts a DIFFERENT population —
 * each test's last 10 runs, needing 5 — so it can read 0 while this page lists
 * a flaky test. Measured live, the two disagreed on 2 of 5 projects. Naming the
 * window here is half of making the pair reconcilable; the report states its
 * own rule on the tile.
 *
 * "quarantine candidate" is spelled out because the short form read as an
 * active quarantine in the original report. Both counts are routinely 1 — a
 * single flaky test is the common case — so the nouns have to agree rather
 * than sit hardcoded plural ("1 flaky tests").
 */
export function flakySubtitle(coach: Pick<FlakyCoachResponse, 'total_flaky' | 'quarantine_candidates'> | undefined): string {
  // No answer yet (loading, or the analysis failed) is not "0 flaky tests":
  // the header read that for the 30 s a starved request took while the list
  // below it later showed two (E2E 2026-10-10).
  if (!coach) return 'Counting flaky tests · last 30 days'
  const flakyCount = coach.total_flaky ?? 0
  const candidateCount = coach.quarantine_candidates ?? 0
  return `${flakyCount} flaky test${flakyCount === 1 ? '' : 's'} · ${candidateCount} quarantine candidate${candidateCount === 1 ? '' : 's'} · last 30 days`
}

/** The three views of the quarantine state machine (Flaky tests › Proposed · Quarantined · History). */
export type QuarantineView = 'proposals' | 'active' | 'history'

export const QUARANTINE_VIEW_STATUSES: Record<QuarantineView, QuarantineStatus[]> = {
  proposals: ['PROPOSED', 'DETECTED'],
  active: ['APPROVED', 'QUARANTINED', 'RECHECK_SCHEDULED', 'RE_QUARANTINED'],
  history: ['RELEASED', 'REJECTED', 'EXPIRED'],
}

/** How many requests each view holds, from the stats endpoint (undefined until it answers). */
export function quarantineCounts(stats: QuarantineStatsResponse | undefined): Record<QuarantineView, number | undefined> {
  if (!stats) return { proposals: undefined, active: undefined, history: undefined }
  return {
    proposals: stats.proposed + stats.detected,
    active: stats.quarantined + stats.approved + stats.recheck_scheduled + stats.re_quarantined,
    history: stats.released + stats.rejected + stats.expired,
  }
}

/** A live (not settled) request — PROPOSED/DETECTED or one of the active states. */
export function isLiveQuarantine(status: QuarantineStatus): boolean {
  return !QUARANTINE_VIEW_STATUSES.history.includes(status)
}

/** A request waiting for a decision (PROPOSED / DETECTED). */
export function isQuarantineProposal(status: QuarantineStatus): boolean {
  return QUARANTINE_VIEW_STATUSES.proposals.includes(status)
}
