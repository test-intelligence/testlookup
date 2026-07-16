/**
 * Types for the Fixer agent (Agentic plan AI-2 — Wave D).
 *
 * These mirror the pinned Wave-D API contract verbatim — the backend
 * implements the same shapes. Do not extend without reconciling with
 * `backend` (see docs/AGENTIC_AI_PLAN_2026_07.md).
 */

// ── Config ────────────────────────────────────────────────────────────────

/**
 * Fixer trust-ladder mode. Deliberately narrower than the governance
 * `AgentMode` — the Fixer contract only admits shadow and suggest; "act"
 * (autonomous merge) does not exist for this agent.
 */
export type FixerMode = 'shadow' | 'suggest'

export type FixerRunnerType = 'docker' | 'workflow_dispatch' | 'none'

export type FixerSchedule = 'daily' | 'weekly' | 'off'

export interface FixerRunner {
  type: FixerRunnerType
  /** Docker image used to validate candidate patches (type=docker). */
  runner_image: string | null
  /** Command template run inside the image, e.g. `pytest {test}` (type=docker). */
  command_template: string | null
  /** Workflow reference, e.g. `.github/workflows/fixer.yml@main` (type=workflow_dispatch). */
  workflow_ref: string | null
}

export interface FixerBudgets {
  max_tests_per_run: number
  max_attempts_per_test: number
  validation_reruns: number
  max_concurrent_open_prs: number
}

export interface FixerConfig {
  enabled: boolean
  mode: FixerMode
  runner: FixerRunner
  test_globs: string[]
  budgets: FixerBudgets
  schedule: FixerSchedule
}

/** Contract defaults: disabled, shadow, no runner, test globs, budgets 3/2/5/2, off. */
export const DEFAULT_FIXER_CONFIG: FixerConfig = {
  enabled: false,
  mode: 'shadow',
  runner: { type: 'none', runner_image: null, command_template: null, workflow_ref: null },
  test_globs: ['tests/**', '**/*.spec.*', '**/*.test.*'],
  budgets: {
    max_tests_per_run: 3,
    max_attempts_per_test: 2,
    validation_reruns: 5,
    max_concurrent_open_prs: 2,
  },
  schedule: 'off',
}

// ── Runs + attempts ───────────────────────────────────────────────────────

/** 202 body from POST /projects/{id}/fixer/run. */
export interface StartFixerRunResponse {
  fixer_run_id: string
}

export type FixAttemptStatus =
  | 'selected'
  | 'diagnosing'
  | 'generating'
  | 'validating'
  | 'validated'
  | 'rejected_globs'
  | 'failed_validation'
  | 'pr_opened'
  | 'error'
  | 'skipped_budget'

export interface FixAttemptValidation {
  reruns: number
  passed: number
}

export interface FixAttempt {
  id: string
  fixer_run_id: string
  test_fingerprint: string
  test_name: string
  status: FixAttemptStatus
  attempt_no: number
  patch_summary: string | null
  validation: FixAttemptValidation | null
  pr_url: string | null
  reason: string | null
  created_at: string
  completed_at: string | null
}

export interface FixAttemptListResponse {
  items: FixAttempt[]
  total: number
}

/** GET /fixer/attempts/{attempt_id} — the attempt plus its heavyweight fields. */
export interface FixAttemptDetail extends FixAttempt {
  /** Unified-diff text of the candidate patch. */
  patch: string | null
  runner_log_digest: string | null
  /** Governance-ledger AgentRunEntry id this attempt was recorded under. */
  ledger_run_id: string | null
}

/** Statuses during which the attempts list keeps polling (work in flight). */
export const ACTIVE_FIX_ATTEMPT_STATUSES: readonly FixAttemptStatus[] = [
  'selected',
  'diagnosing',
  'generating',
  'validating',
]

export function isFixAttemptActive(status: FixAttemptStatus | undefined | null): boolean {
  return status != null && ACTIVE_FIX_ATTEMPT_STATUSES.includes(status)
}
