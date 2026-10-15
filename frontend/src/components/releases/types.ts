/**
 * Local types for the /releases page: its own view model (release, gate,
 * phases, blockers), derived from the server's ``Release`` rows by
 * ``mapping.ts``. Nothing here is computed in the browser: the gate is the
 * server's CURRENT recorded verdict (``release.gate``), the phases are the
 * release's own, the owner is ``release.owner``. Anything the server does not
 * know is ``null`` and the components print ``—`` / "No owner" /
 * "Not evaluated yet".
 */
import type { Release } from '@/types/releases'

/**
 * The gate decision a card shows. ``not_evaluated`` = a recorded verdict
 * NOT_EVALUATED (evaluated, below the evidence floor); ``never_evaluated`` =
 * no verdict was ever recorded; ``conditional`` maps ``CONDITIONAL_GO``.
 */
export type GateDecision =
  | 'go'
  | 'conditional'
  | 'no_go'
  | 'not_evaluated'
  | 'never_evaluated'
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

/** The release's current recorded verdict, as the card renders it. */
export interface DerivedGate {
  decision: GateDecision
  /** Percent, 0-100; ``null`` = not measured. */
  passRate: number | null
  /** Tests in scope. */
  denominator: number | null
  /** Tests that carry evidence. */
  evidence: number | null
  /** Runs the verdict consumed. */
  runCount: number | null
  /** ISO-8601 of the recorded verdict; ``null`` when never evaluated. */
  decidedAt: string | null
  measured: boolean
  insufficientReason: string | null
  /** Every blocking reason the server stored (the card shows only the first few). */
  blockingTotal: number
}

export interface DerivedPhase {
  /** The phase id. */
  key: string
  /** The phase name. */
  label: string
  /** The server's ``phase_type`` (``uat``, ``regression``…), ``null`` when absent. */
  phaseType: string | null
  state: PhaseState
  /** Optional inline note for a failed phase. */
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
  /** The owner's name; "No owner" when there is none. */
  ownerName: string
  hasOwner: boolean
  isActive: boolean
  /** Only an ``http(s)://`` URL; anything else is ``null``. */
  externalUrl: string | null
  sourceSystem: string | null
  dueAt: string | null
  gate: DerivedGate
  /** FAILED -> failed, BROKEN -> broken, SKIPPED + UNKNOWN -> skipped; ``null`` without status counts. */
  totals: { passed: number; failed: number; broken: number; skipped: number } | null
  phases: DerivedPhase[]
  /** The first few blockers (server reasons, then failed phases). */
  blockers: DerivedBlocker[]
  /** Every blocker: ``gate.blockingTotal`` + failed phases. */
  blockerCount: number
}
