/**
 * W03 AIH-05: the AI analysis of one test across runs, and the stored versions of
 * one test result (contract: docs/deferred-scope-2026-10-13/work/W03-contract.md
 * §6.4 R5, §7.3, §9.8).
 *
 * Additive fields of existing responses live here (W02 precedent:
 * `types/executionMetrics.ts`), so `types/runs.ts` is not edited. They are absent
 * on an older server: every consumer treats a missing `ai` / `ai_changes` as
 * "nothing reported", never as "no change".
 *
 * Units: `confidence` / `confidence_score` = integer 0-100; `*_at` = ISO 8601 UTC.
 * `null` = not measured / not known (render "not reported", never 0).
 * Root causes, actor names and model names are untrusted text (LLM output over
 * CI-supplied strings): render them as React text only.
 */
import type { TestCaseHistoryPoint } from '@/types/runs'

/** The AI analysis of ONE history point (that run's own `test_cases` row). */
export interface TestHistoryAI {
  /** That point's own `test_cases.id` (for `GET /runs/{run_id}/tests/{test_id}/ai-history`). */
  test_id: string
  category: string | null
  confidence: number | null
  /** <=300 chars, whitespace-normalised, sanitized; may be "". */
  root_cause_excerpt: string
  /** 16 hex chars, or null for an empty root cause. */
  root_cause_sha: string | null
  /** rules | ml | llm | auto | other; null when the analysis did not record it. */
  analysis_mode: string | null
  model: string | null
  is_flaky: boolean
  requires_human_review: boolean
  /** `ai_analysis_versions` rows of that test result (0 before any version exists). */
  version_count: number
}

/** A point where the AI changed its category or its root cause vs the previous AI point. */
export interface TestHistoryAIChange {
  run_id: string | null
  run_label: string
  from_category: string | null
  to_category: string | null
  from_confidence: number | null
  to_confidence: number | null
  /** "category" | "root_cause" (sorted). */
  kinds: string[]
}

/** One history point with its optional AI block. */
export type TestHistoryAIPoint = TestCaseHistoryPoint & { ai?: TestHistoryAI | null }

/** The W03 fields of `GET /runs/{run_id}/tests/{test_id}/history` (use as `data as TestCaseHistory & TestHistoryAIFields`). */
export interface TestHistoryAIFields {
  history: Array<{ ai?: TestHistoryAI | null }>
  ai_changes?: TestHistoryAIChange[]
}

/** Who made a human / manual version. */
export interface AIActor {
  user_id: string
  /** users.full_name or username; null when unknown. */
  name: string | null
}

/** One stored version of a test result's AI analysis (R5). */
export interface AITestVersion {
  version_id: string
  version: number
  /** pipeline | manual_analyze | human_correction | bulk_reclassify | backfill | current */
  trigger: string
  pipeline_run_id: string | null
  actor: AIActor | null
  failure_category: string | null
  confidence_score: number | null
  analysis_mode: string | null
  llm_provider: string | null
  llm_model: string | null
  is_flaky: boolean
  requires_human_review: boolean
  /** <=300 chars, whitespace-normalised. */
  root_cause_excerpt: string
  content_sha256: string
  created_at: string
}

/** `GET /api/v1/runs/{run_id}/tests/{test_id}/ai-history` (R5). */
export interface AITestHistory {
  run_id: string
  test_id: string
  test_fingerprint: string | null
  test_name: string
  /** The live `ai_analysis` row (trigger "current"; version = latest stored or 0). */
  current: AITestVersion | null
  /** Newest first. */
  versions: AITestVersion[]
  total: number
}

/** The `ai` block of a canonical run-history item (§7.3); null when that run's test has no analysis. */
export interface CanonicalRunAI {
  category: string | null
  confidence: number | null
  analysis_mode: string | null
  model: string | null
  requires_human_review: boolean
  root_cause_excerpt: string
  version_count: number
}
