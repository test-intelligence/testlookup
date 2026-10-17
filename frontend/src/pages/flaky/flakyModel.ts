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

// ── The quarantine policy (W09 FLK-02) ───────────────────────────────────────

/**
 * The Quarantined tab's warning past the policy's cap (`max_active_quarantined`,
 * 0 = no limit): `12 tests are quarantined; this project's policy warns past 8.`
 * `null` when there is no cap, no count yet, or the count is within it. The
 * cap warns and never blocks (refusing a broken test's quarantine would push
 * the noise back into the build).
 */
export function overCapText(active: number | undefined, cap: number | undefined): string | null {
  if (active === undefined || cap === undefined || !Number.isFinite(active) || !Number.isFinite(cap)) return null
  if (cap <= 0 || active <= cap) return null
  return `${active} test${active === 1 ? ' is' : 's are'} quarantined; this project's policy warns past ${cap}.`
}

/** `0 of 8 active now.` beside the cap field (no limit: `3 active now; no limit.`); null without a count. */
export function activeNowText(active: number | undefined, cap: number | null): string | null {
  if (active === undefined || !Number.isFinite(active)) return null
  if (cap === null || !Number.isFinite(cap)) return `${active} active now.`
  return cap <= 0 ? `${active} active now; no limit.` : `${active} of ${cap} active now.`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * `SLA due 23 Oct` for a quarantined request whose SLA has not passed, from
 * its `stale_at` (UTC day; the year only when it is not `now`'s). `null` for
 * no date, an unparseable one, or one already past (the row's existing
 * `stale — N days over SLA` says that).
 */
export function slaDueText(staleAt: string | null | undefined, now: Date = new Date()): string | null {
  if (!staleAt) return null
  const due = new Date(staleAt)
  if (Number.isNaN(due.getTime()) || due.getTime() <= now.getTime()) return null
  const year = due.getUTCFullYear() === now.getUTCFullYear() ? '' : ` ${due.getUTCFullYear()}`
  return `SLA due ${due.getUTCDate()} ${MONTHS[due.getUTCMonth()]}${year}`
}

/** A 0-1 flip rate as the card's whole-ish percent (`0.2` → `20`, `0.125` → `12.5`). */
export function rateToPercent(rate: number): number {
  return Math.round(rate * 1000) / 10
}

/** The card's percent back to the 0-1 rate the server stores (`35` → `0.35`). */
export function percentToRate(percent: number): number {
  return Math.round(percent * 10) / 1000
}

/**
 * `Using defaults: SLA 14 days, promote after 20 passes, detection 20 % over 10 runs.`
 * Built from the answer (the project has no row yet), never from literals.
 */
export function policyDefaultsText(policy: {
  sla_days: number
  promote_after_passes: number
  detection_flip_rate_threshold: number
  detection_min_runs: number
}): string {
  return (
    `Using defaults: SLA ${policy.sla_days} days, promote after ${policy.promote_after_passes} passes, ` +
    `detection ${rateToPercent(policy.detection_flip_rate_threshold)} % over ${policy.detection_min_runs} runs.`
  )
}
