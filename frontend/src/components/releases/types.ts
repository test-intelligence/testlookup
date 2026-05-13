/**
 * Local types for the /releases redesign.
 *
 * These mirror the handoff's data model (``ReleasesPage`` in README §7) but
 * stay loose enough to derive from the existing ``Release`` / ``LinkedRun``
 * shapes without backend changes. Anything missing from the backend renders
 * as ``null`` and the components fall back to ``—``.
 */
import type { Release } from '@/types/releases'

export type GateDecision =
  | 'go'
  | 'conditional'
  | 'no_go'
  | 'not_evaluated'
  | 'cancelled'

export type ReleaseStage =
  | 'planning'
  | 'in_progress'
  | 'released'
  | 'cancelled'

export type PhaseState =
  | 'idle'
  | 'done'
  | 'active'
  | 'failed'
  | 'skipped'

export interface DerivedGate {
  decision: GateDecision
  composite: number | null
  coverage: number | null
  flakePct: number | null
  updatedAt: string | null
}

export interface DerivedPhase {
  /**
   * Phase key — six canonical buckets the handoff prescribes. Anything
   * outside this set collapses into the closest match or is dropped from the
   * pipeline chip row.
   */
  key: 'smoke' | 'regression' | 'e2e' | 'perf' | 'uat' | 'sign'
  label: string
  state: PhaseState
  /** Optional inline note ("–6%", "Skipped — owner deferred"). */
  note?: string | null
}

export interface DerivedBlocker {
  id: string
  severity: 'warn' | 'red' | 'resolved'
  title: string
  context: string
  linkedTicketId?: string | null
  ownerInitials?: string | null
  etaAt?: string | null
  action: { label: string; href: string }
}

export interface DerivedRelease {
  source: Release           // pass-through for click handlers + edit/delete
  id: string
  name: string
  version: string
  stage: ReleaseStage
  ownerInitials: string
  ownerName: string
  dueAt: string | null
  gate: DerivedGate
  totals: { passed: number; failed: number; flaky: number; skipped: number } | null
  phases: DerivedPhase[]
  blockers: DerivedBlocker[]
}
