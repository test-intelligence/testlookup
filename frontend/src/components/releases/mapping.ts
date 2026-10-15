/**
 * Derive the /releases page's release shape from the ``Release`` rows the
 * backend returns. Nothing is decided here: the gate is the server's CURRENT
 * recorded verdict (``release.gate``, ``null`` = never evaluated), the phases
 * are the release's own, the owner is ``release.owner`` and the blockers are
 * the server's blocking reasons plus the release's failed phases.
 *
 * There are no thresholds in this file, on purpose. The former
 * ``decideGate`` judged a composite / coverage / flake triple against
 * 95/90 · 80/70 · 1.5/3 that nothing ever supplied, so every release read
 * "not evaluated" and "Ready to ship" was always 0; and a six-bucket phase
 * template (Smoke … Sign & ship) painted placeholders over the release's real
 * phases. Both are gone (REL-01).
 *
 * This is intentionally pure — no fetching, no side effects — so the page
 * can re-derive on every render without churn.
 */
import type { Release, ReleaseGateSummary, ReleasePhase } from '@/types/releases'
import type {
  DerivedBlocker,
  DerivedGate,
  DerivedPhase,
  DerivedRelease,
  GateDecision,
  PhaseState,
  ReleaseStage,
} from './types'

/** Server blocking reasons kept on a derived release (the card shows the first 3; `blockerCount` has the total). */
export const BLOCKER_REASON_CAP = 5

/** Backend release.status string → DerivedRelease.stage. */
function mapStage(s: string): ReleaseStage {
  const lower = (s ?? '').toLowerCase()
  if (lower === 'released' || lower === 'shipped' || lower === 'complete' || lower === 'completed') return 'released'
  if (lower === 'cancelled' || lower === 'canceled' || lower === 'archived') return 'cancelled'
  if (lower === 'in_progress' || lower === 'in-progress' || lower === 'active' || lower === 'running') return 'in_progress'
  return 'planning'
}

/**
 * The server's verdict word → the card's decision. Anything the table
 * vocabulary does not hold reads "not evaluated": a drifted word must never
 * read as a pass.
 */
export function mapVerdict(verdict: string): GateDecision {
  switch (verdict) {
    case 'GO':
      return 'go'
    case 'NO_GO':
      return 'no_go'
    case 'CONDITIONAL_GO':
      return 'conditional'
    case 'NOT_EVALUATED':
    default:
      return 'not_evaluated'
  }
}

function mapPhaseState(status: string): PhaseState {
  const s = (status ?? '').toLowerCase()
  if (s === 'done' || s === 'complete' || s === 'completed' || s === 'passed') return 'done'
  if (s === 'failed' || s === 'fail') return 'failed'
  if (s === 'skipped' || s === 'skip') return 'skipped'
  if (s === 'active' || s === 'in_progress' || s === 'running' || s === 'in-progress') return 'active'
  return 'idle'
}

/** The release's own phases, in their order: name and status, nothing invented. */
function buildPhases(phases: ReleasePhase[] | null | undefined): DerivedPhase[] {
  return [...(phases ?? [])]
    .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0))
    .map(p => ({
      key: p.id,
      label: p.name,
      phaseType: p.phase_type ?? null,
      state: mapPhaseState(p.status),
    }))
}

/** The server's recorded verdict as the card renders it; `null` = never evaluated. */
function buildGate(gate: ReleaseGateSummary | null | undefined, stage: ReleaseStage): DerivedGate {
  if (!gate) {
    return {
      decision: stage === 'cancelled' ? 'cancelled' : 'never_evaluated',
      passRate: null,
      denominator: null,
      evidence: null,
      runCount: null,
      decidedAt: null,
      measured: false,
      insufficientReason: null,
      blockingTotal: 0,
    }
  }
  const reasons = gate.blocking_reasons ?? []
  return {
    decision: stage === 'cancelled' ? 'cancelled' : mapVerdict(gate.verdict),
    passRate: gate.pass_rate ?? null,
    denominator: gate.denominator ?? null,
    evidence: gate.evidence_count ?? null,
    runCount: gate.run_count ?? null,
    decidedAt: gate.decided_at ?? null,
    measured: Boolean(gate.measured),
    insufficientReason: gate.insufficient_reason ?? null,
    blockingTotal: Math.max(gate.blocking_total ?? 0, reasons.length),
  }
}

/**
 * The status counts of the recorded verdict as the pass/fail bar's four
 * segments: FAILED → failed, BROKEN → broken, SKIPPED + UNKNOWN → skipped.
 * `null` without counts (never evaluated, or a legacy row's `{}`).
 */
function buildTotals(gate: ReleaseGateSummary | null | undefined): DerivedRelease['totals'] {
  const counts = gate?.status_counts
  if (!counts || Object.keys(counts).length === 0) return null
  const n = (k: string) => (Number.isFinite(counts[k]) ? counts[k] : 0)
  return {
    passed: n('PASSED'),
    failed: n('FAILED'),
    broken: n('BROKEN'),
    skipped: n('SKIPPED') + n('UNKNOWN'),
  }
}

/** Two letters for the owner chip: first and last word of the name, or the first two letters of a one-word name. */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/**
 * An external link only for an `http(s)://` URL (it comes from GitHub /
 * Jira): `javascript:`, `data:` or anything else — or a string with
 * whitespace / control characters, or one the URL parser refuses — is `null`
 * and renders no link.
 */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return null
  // eslint-disable-next-line no-control-regex
  if (/[\s\u0000-\u001F\u007F]/.test(url)) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url : null
  } catch {
    return null
  }
}

/**
 * The release's blockers: the server's blocking reasons (red, the first
 * `BLOCKER_REASON_CAP`, each opening the release's gate panel), then each
 * failed phase (red, opening that phase).
 */
function buildBlockers(release: Release): DerivedBlocker[] {
  const blockers: DerivedBlocker[] = []
  const reasons = release.gate?.blocking_reasons ?? []
  reasons.slice(0, BLOCKER_REASON_CAP).forEach((reason, i) => {
    blockers.push({
      id: `gate-${release.id}-${i}`,
      severity: 'red',
      title: reason,
      context: 'release gate',
      action: { label: 'Open gate →', href: `/releases/${release.id}#gate` },
    })
  })
  for (const p of release.phases ?? []) {
    if (mapPhaseState(p.status) === 'failed') {
      blockers.push({
        id: `phase-${p.id}`,
        severity: 'red',
        title: `${p.name} failed`,
        context: p.notes || p.phase_type || 'phase',
        action: { label: 'Open phase →', href: `/releases/${release.id}#phase-${p.id}` },
      })
    }
  }
  return blockers
}

/** Map one Release into the renderer-ready DerivedRelease shape. */
export function deriveRelease(release: Release): DerivedRelease {
  const stage = mapStage(release.status)
  const gate = buildGate(release.gate, stage)
  const phases = buildPhases(release.phases)
  const failedPhases = phases.filter(p => p.state === 'failed').length
  const ownerName = release.owner?.name?.trim() || null
  return {
    source: release,
    id: release.id,
    name: release.name,
    version: release.version ?? '',
    stage,
    ownerInitials: ownerName ? initialsOf(ownerName) : '–',
    ownerName: ownerName ?? 'No owner',
    hasOwner: ownerName != null,
    isActive: release.is_active === true,
    externalUrl: safeHttpUrl(release.external_url),
    sourceSystem: release.source_system ?? null,
    dueAt: release.planned_date,
    gate,
    totals: buildTotals(release.gate),
    phases,
    blockers: buildBlockers(release),
    blockerCount: gate.blockingTotal + failedPhases,
  }
}

/** Counts per stage, used by the segmented tab control. */
export interface StageCounts {
  all: number
  planning: number
  in_progress: number
  released: number
  cancelled: number
}
export function computeStageCounts(releases: DerivedRelease[]): StageCounts {
  const counts: StageCounts = { all: releases.length, planning: 0, in_progress: 0, released: 0, cancelled: 0 }
  for (const r of releases) counts[r.stage]++
  return counts
}
