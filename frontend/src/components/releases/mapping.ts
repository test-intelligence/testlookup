/**
 * Derive the handoff's release shape from the existing ``Release`` rows the
 * backend already returns. Anything the backend doesn't expose (coverage,
 * flake rate, owner, blockers, compliance packs) lands as ``null`` /
 * ``[]`` and the UI shows the appropriate empty treatment.
 *
 * This is intentionally pure — no fetching, no side effects — so the page
 * can re-derive on every render without churn.
 */
import type { Release, ReleasePhase } from '@/types/releases'
import type {
  DerivedBlocker,
  DerivedGate,
  DerivedPhase,
  DerivedRelease,
  GateDecision,
  PhaseState,
  ReleaseStage,
} from './types'

/** Backend release.status string → DerivedRelease.stage. */
function mapStage(s: string): ReleaseStage {
  const lower = (s ?? '').toLowerCase()
  if (lower === 'released' || lower === 'shipped' || lower === 'complete' || lower === 'completed') return 'released'
  if (lower === 'cancelled' || lower === 'canceled' || lower === 'archived') return 'cancelled'
  if (lower === 'in_progress' || lower === 'in-progress' || lower === 'active' || lower === 'running') return 'in_progress'
  return 'planning'
}

/** Gate thresholds — handoff §5.5. Lower = stricter on composite/coverage, inverted on flake. */
function decideGate(composite: number | null, coverage: number | null, flakePct: number | null, stage: ReleaseStage): GateDecision {
  if (stage === 'cancelled') return 'cancelled'
  // Need at least one signal to evaluate.
  if (composite == null && coverage == null && flakePct == null) return 'not_evaluated'

  type Tone = 'go' | 'warn' | 'fail'
  const compTone: Tone = composite == null ? 'go' : composite >= 95 ? 'go' : composite >= 90 ? 'warn' : 'fail'
  const covTone:  Tone = coverage  == null ? 'go' : coverage  >= 80 ? 'go' : coverage  >= 70 ? 'warn' : 'fail'
  const flakeTone: Tone = flakePct == null ? 'go' : flakePct <= 1.5 ? 'go' : flakePct <= 3.0 ? 'warn' : 'fail'
  const worst = [compTone, covTone, flakeTone].reduce<Tone>((acc, t) => {
    if (acc === 'fail' || t === 'fail') return 'fail'
    if (acc === 'warn' || t === 'warn') return 'warn'
    return 'go'
  }, 'go')
  return worst === 'fail' ? 'no_go' : worst === 'warn' ? 'conditional' : 'go'
}

/** Phase-name → handoff key. Anything unrecognised falls back to ``e2e``. */
const PHASE_KEYS: DerivedPhase['key'][] = ['smoke', 'regression', 'e2e', 'perf', 'uat', 'sign']
const PHASE_LABELS: Record<DerivedPhase['key'], string> = {
  smoke: 'Smoke',
  regression: 'Regression',
  e2e: 'E2E suites',
  perf: 'Perf',
  uat: 'UAT sign-off',
  sign: 'Sign & ship',
}
function classifyPhase(name: string, phaseType: string | null): DerivedPhase['key'] {
  const blob = `${name} ${phaseType ?? ''}`.toLowerCase()
  if (/smoke/.test(blob)) return 'smoke'
  if (/regress/.test(blob)) return 'regression'
  if (/perf/.test(blob)) return 'perf'
  if (/uat|user.?accept/.test(blob)) return 'uat'
  if (/sign|ship|release|deploy/.test(blob)) return 'sign'
  return 'e2e'
}

function mapPhaseState(status: string): PhaseState {
  const s = (status ?? '').toLowerCase()
  if (s === 'done' || s === 'complete' || s === 'completed' || s === 'passed') return 'done'
  if (s === 'failed' || s === 'fail') return 'failed'
  if (s === 'skipped' || s === 'skip') return 'skipped'
  if (s === 'active' || s === 'in_progress' || s === 'running' || s === 'in-progress') return 'active'
  return 'idle'
}

/**
 * Build six phase chips, prefilled from any matching backend phases. Phases
 * the backend hasn't recorded yet render as ``idle`` so the pipeline always
 * shows all six steps regardless of where the release is.
 */
function buildPhases(phases: ReleasePhase[]): DerivedPhase[] {
  const slots: Record<DerivedPhase['key'], DerivedPhase> = Object.fromEntries(
    PHASE_KEYS.map(k => [k, { key: k, label: PHASE_LABELS[k], state: 'idle' as PhaseState }]),
  ) as Record<DerivedPhase['key'], DerivedPhase>

  for (const p of phases ?? []) {
    const key = classifyPhase(p.name, p.phase_type)
    const next = mapPhaseState(p.status)
    // A more advanced state wins over idle so two backend phases collapsing
    // onto the same chip don't drop signal.
    const RANK: Record<PhaseState, number> = { idle: 0, skipped: 1, active: 2, done: 3, failed: 4 }
    if (RANK[next] >= RANK[slots[key].state]) {
      slots[key] = { ...slots[key], state: next }
    }
  }

  return PHASE_KEYS.map(k => slots[k])
}

/** Owner initials/name from the project name if no real owner data is present. */
function ownerFromProject(name?: string | null): { initials: string; name: string } {
  const base = name?.trim() || 'TestLookup'
  const parts = base.split(/[\s/_-]+/).filter(Boolean)
  const initials = ((parts[0]?.[0] ?? base[0] ?? '?').toUpperCase()
    + (parts[1]?.[0] ?? '').toUpperCase()).slice(0, 2) || '??'
  return { initials, name: base }
}

/**
 * Derive blockers from per-release signals. Pure UI shimming — every entry
 * carries a destination action so the row stays useful even when the
 * dedicated blockers endpoint hasn't shipped yet.
 */
function buildBlockers(release: Release, gate: DerivedGate): DerivedBlocker[] {
  const blockers: DerivedBlocker[] = []
  // Failed phases each become a red blocker.
  for (const p of release.phases ?? []) {
    if (mapPhaseState(p.status) === 'failed') {
      blockers.push({
        id: `phase-${p.id}`,
        severity: 'red',
        title: `${p.name} failed`,
        context: p.notes || PHASE_LABELS[classifyPhase(p.name, p.phase_type)],
        action: { label: 'Open phase →', href: `/releases/${release.id}#phase-${p.id}` },
      })
    }
  }
  // Composite below cap → warn-level blocker against the gate.
  if (gate.composite != null && gate.composite < 95 && gate.composite >= 90) {
    blockers.push({
      id: `gate-composite-${release.id}`,
      severity: 'warn',
      title: `Composite ${gate.composite.toFixed(1)}% below ≥ 95% cap`,
      context: 'release gate',
      action: { label: 'Review gate →', href: `/release-gate/${release.id}` },
    })
  }
  if (gate.flakePct != null && gate.flakePct > 1.5) {
    blockers.push({
      id: `gate-flake-${release.id}`,
      severity: gate.flakePct > 3 ? 'red' : 'warn',
      title: `Flake rate ${gate.flakePct.toFixed(1)}% over cap`,
      context: 'flake budget',
      action: { label: 'View flaky tests →', href: `/flaky-coach` },
    })
  }
  return blockers
}

/** Map one Release into the renderer-ready DerivedRelease shape. */
export function deriveRelease(release: Release): DerivedRelease {
  const stage = mapStage(release.status)
  const owner = ownerFromProject(release.project_name)
  // Coverage + flake aren't on the Release list endpoint today; pulled from
  // the detail endpoint when the user expands a card. List view shows ``—``.
  const gate: DerivedGate = {
    decision: 'not_evaluated',
    composite: null,
    coverage: null,
    flakePct: null,
    updatedAt: release.updated_at,
  }
  gate.decision = decideGate(gate.composite, gate.coverage, gate.flakePct, stage)
  const phases = buildPhases(release.phases ?? [])
  const blockers = buildBlockers(release, gate)
  return {
    source: release,
    id: release.id,
    name: release.name,
    version: release.version ?? '',
    stage,
    ownerInitials: owner.initials,
    ownerName: owner.name,
    dueAt: release.planned_date,
    gate,
    totals: null,
    phases,
    blockers,
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
