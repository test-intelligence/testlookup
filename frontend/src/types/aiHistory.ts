/**
 * W03 "ai-history" (AIH-02/03/04): snake_case mirrors of the API shapes pinned in
 * `docs/deferred-scope-2026-10-13/work/W03-contract.md` §2 (the digest), §3 (the diff) and §6 (the routes).
 *
 * Every enum-like field is a plain `string` here as on the server (an unknown value must render, never 422 or
 * crash). `null` means "not measured / not known", never 0. The digest payload of an OLDER stored version may lack
 * fields added later, so readers treat every nested block as optional.
 *
 * Kept in its own file (the W02 `types/executionMetrics.ts` precedent) so the shared `types/runs.ts` is not edited.
 */

/** `analysis_digest` (the per-run AI digest, §2) or `run_summary` (the summary agent's text, §1.4). */
export type AIHistoryKind = 'analysis_digest' | 'run_summary'

/** The E8.3 human-review block a version (or a whole response) carries. */
export interface AIReviewBlock {
  state: string
  message: string
  review_id?: string | null
  reviewed_at?: string | null
}

/** The E8.3 envelope fields every list / get / diff / compare response carries. */
export interface AIEnvelopeFields {
  requires_human_review?: boolean
  review?: AIReviewBlock | null
  ai_disclaimer?: string | null
  ai_disclaimer_version?: string | null
}

export interface DecisionReportRef {
  report_id: string
  report_version: number
}

/** One stored version, as listed (`GET /runs/{id}/ai-history`) and as the head of `GET .../ai-history/{version_id}`. */
export interface AIVersionSummary {
  version_id: string
  kind: string
  version: number
  /** `pipeline` | `backfill` */
  trigger: string
  pipeline_run_id?: string | null
  generated_at?: string | null
  created_at?: string | null
  /** Digest: `rules` | `ml` | `llm` | `auto` | `mixed`; summary: `llm` | `deterministic`. */
  analysis_mode?: string | null
  llm_provider?: string | null
  llm_model?: string | null
  fallback_used: boolean
  deterministic: boolean
  verdict?: string | null
  risk_score?: number | null
  /** Digest: the analysed tests. */
  failing_tests?: number | null
  categories?: Record<string, number> | null
  cluster_count?: number | null
  /** <= 200 chars (summary: its executive summary; digest: null). */
  headline?: string | null
  content_sha256: string
  decision_report_ref?: DecisionReportRef | null
  /** Digest rows: the `run_summary` version produced by the same pipeline. */
  summary_version?: number | null
  review?: AIReviewBlock | null
  is_latest: boolean
}

/** `GET /runs/{id}/ai-history/{version_id}`: the summary plus the stored payload. */
export interface AIVersion<P = AIDigest | AIRunSummaryPayload> extends AIVersionSummary, AIEnvelopeFields {
  payload: P
}

/** `GET /runs/{id}/ai-history?kind=&limit=` */
export interface AIHistoryList extends AIEnvelopeFields {
  run_id: string
  kind: string
  total: number
  limit: number
  truncated: boolean
  latest_version: number | null
  /** `available`: the run has at least one AI analysis row or a published decision report (a live view exists). */
  live: { available: boolean }
  /** Newest first. */
  versions: AIVersionSummary[]
}

// ── The digest (§2) ─────────────────────────────────────────────────────────

export interface AIDigestRun {
  run_id: string
  project_id: string
  build_number: string | null
  branch: string | null
  primary_suite_name: string | null
  total_tests: number
  failed_tests: number
  broken_tests: number
  pass_rate: number | null
}

export interface AIDigestVerdict {
  recommendation: string | null
  risk_score: number | null
  composite_risk: number | null
  /** `decision_report` | `release_decision` | null */
  source: string | null
  decision_report_version: number | null
  overridden: boolean
}

export interface AIDigestCluster {
  signature: string
  cluster_id: string
  label: string
  size: number
  member_count: number
  member_fingerprints: string[]
  representative_error: string | null
}

export interface AIDigestTest {
  test_fingerprint: string
  /** This run's `test_cases.id`. */
  test_id: string
  test_name: string
  suite_name: string | null
  status: string
  category: string | null
  confidence: number | null
  root_cause_sha: string | null
  root_cause_excerpt: string
  is_flaky: boolean
  requires_human_review: boolean
  analysis_mode: string | null
}

export interface AIDigestProvenance {
  pipeline_run_id: string | null
  /** `stored` | `live` */
  source: string
  captured_at: string | null
  /** `llm` | `ml` | `rules` | `auto` | `mixed` | null */
  engine: string | null
  mode_counts: Record<string, number>
  llm_provider: string | null
  llm_model: string | null
  fallback_used: boolean
  fallback_count: number
  deterministic: boolean
  summary_fallback_used: boolean | null
  carried_over_tests: number | null
  stage_quality: string | null
  prompt_versions: Record<string, string>
  partial: string[]
}

export interface AIDigest {
  schema_version: number
  kind: string
  run: AIDigestRun
  verdict: AIDigestVerdict
  analysed_tests: number
  categories: Record<string, number>
  avg_confidence: number | null
  flaky_count: number
  requires_review_count: number
  clusters: AIDigestCluster[]
  tests: AIDigestTest[]
  actions: string[]
  actions_source: string | null
  provenance: AIDigestProvenance
  limits: { tests_total: number; tests_truncated: boolean; clusters_total: number; clusters_truncated: boolean }
  content_sha256: string
}

/** The `run_summary` payload (§1.4): exactly what the summary agent stores. */
export interface AIRunSummaryPayload {
  executive_summary?: unknown
  markdown_report?: string | null
  layer1_executive_summary?: unknown
  layer2_incident_view?: unknown
  layer3_evidence_pack?: unknown
  layer4_action_plan?: unknown
  executive_panel?: unknown
  anomaly_count?: number | null
  is_regression?: boolean | null
  analysis_count?: number | null
  fallback_used?: boolean | null
  citations?: unknown[] | null
  summary_provenance?: Record<string, unknown> | null
  /** Backfilled rows: the stored markdown was the decision report's, so it was dropped. */
  markdown_source?: string | null
}

// ── The diff (§3) ───────────────────────────────────────────────────────────

/** `versions`: two digests of the SAME run; `cross_run`: two runs (only the labels differ). */
export type AIDiffMode = 'versions' | 'cross_run'

export interface FromTo<T> {
  from: T
  to: T
}

export interface AIDiffProvenance {
  changed: boolean
  engine: FromTo<string | null> | null
  model: FromTo<string | null> | null
  fallback_used: FromTo<boolean> | null
  deterministic: FromTo<boolean> | null
  mode_counts: FromTo<Record<string, number>> | null
  engine_only: boolean
  note: string | null
}

export interface AIDiffVerdict {
  changed: boolean
  from: string | null
  to: string | null
  /** `worse` | `better` | `same` | `unknown` */
  direction: string
  from_risk: number | null
  to_risk: number | null
  risk_delta: number | null
}

export interface AIDiffCategoryChange {
  category: string
  from: number
  to: number
  delta: number
}

export interface AIDiffCluster {
  signature: string
  label: string
  size: number
  member_count: number
}

export interface AIDiffClusterMatch {
  signature_from: string
  signature_to: string
  label_from: string
  label_to: string
  size_from: number
  size_to: number
  delta: number
  /** `signature` | `overlap` */
  matched_by: string
  jaccard: number | null
  resized: boolean
}

export interface AIDiffTestRef {
  test_fingerprint: string
  test_id: string | null
  test_name: string | null
  suite_name: string | null
  category: string | null
  confidence: number | null
}

export interface AIDiffTestChange {
  test_fingerprint: string
  /** The `to` side's test. */
  test_id: string | null
  from_test_id?: string | null
  test_name: string | null
  suite_name: string | null
  /** `category` | `confidence` | `flaky` | `root_cause` */
  kinds: string[]
  material: boolean
  category: FromTo<string | null> | null
  confidence: (FromTo<number | null> & { delta: number | null }) | null
  flaky: FromTo<boolean> | null
  root_cause_changed: boolean
  root_cause_excerpt: FromTo<string | null> | null
}

export interface AIDigestDiff {
  schema_version: number
  mode: string
  identical: boolean
  has_changes: boolean
  provenance: AIDiffProvenance
  verdict: AIDiffVerdict
  categories: { changed: AIDiffCategoryChange[]; from_total: number; to_total: number }
  clusters: {
    added: AIDiffCluster[]
    removed: AIDiffCluster[]
    matched: AIDiffClusterMatch[]
    counts: { added: number; removed: number; matched: number; resized: number }
  }
  tests: {
    added: AIDiffTestRef[]
    removed: AIDiffTestRef[]
    changed: AIDiffTestChange[]
    persisting_unchanged: number
    counts: { added: number; removed: number; changed: number; material_changed: number }
    truncated: boolean
  }
  actions: { added: string[]; removed: string[] }
}

/** One side of a diff or compare. */
export interface AIVersionRef {
  /** A version id or `live`. */
  ref: string
  /** `stored` | `live` */
  source: string
  version?: number | null
  kind?: string
  generated_at?: string | null
  pipeline_run_id?: string | null
  content_sha256?: string | null
  analysis_mode?: string | null
  llm_model?: string | null
  run_id: string
  build_number?: string | null
}

/** `GET /runs/{id}/ai-history/diff?from=&to=` (the sides are emitted under the JSON keys `from` / `to`). */
export interface AIDigestDiffResponse extends AIEnvelopeFields {
  run_id: string
  from: AIVersionRef
  to: AIVersionRef
  diff: AIDigestDiff
}

// ── Previous run + cross-run compare (§6.2, §6.5, §6.7) ─────────────────────

export interface RunBrief {
  run_id: string
  build_number: string | null
  run_seq: number | null
  branch: string | null
  primary_suite_name: string | null
  status: string | null
  created_at: string | null
  /** `coalesce(end_time, created_at)` */
  finished_at: string | null
}

/** `null` = there is a baseline; otherwise why there is none. */
export type PreviousRunReason = 'no_primary_suite' | 'run_lacks_suite' | 'no_previous_run' | (string & {})

/** `GET /runs/{id}/previous?suite_name=` (D42: same project, suite and branch, finished before this run). */
export interface AIPreviousRun {
  run_id: string
  rule: {
    scope: string
    suite_name: string | null
    branch: string | null
    candidate_status: string
    order: string
  }
  current: RunBrief
  baseline: RunBrief | null
  reason: PreviousRunReason | null
  /** A sentence safe to toast. */
  message: string | null
}

export interface AICompareNarrative {
  /** `ready` (a cached run_compare_v2 report exists) | `deterministic` (built from the diff) */
  status: string
  /** `llm` | `deterministic` */
  source: string
  /** `AI narrative` | `deterministic summary`: printed as is. */
  label: string
  fallback_used: boolean
  executive_summary: string
  ai_changes: string[]
  risk_level: string | null
  prompt_version: string
  model_name: string | null
  generated_at: string | null
  /** `ready` | `queued` | `failed` | `absent` */
  report_status: string
}

/** A compact per-side summary for the Run Compare columns. */
export interface DigestSide {
  verdict: string | null
  risk_score: number | null
  composite_risk: number | null
  analysed_tests: number
  categories: Record<string, number>
  avg_confidence: number | null
  /** <= 5, by size. */
  top_clusters: { label: string; size: number; signature: string }[]
  engine: string | null
  llm_provider: string | null
  llm_model: string | null
  deterministic: boolean
  fallback_used: boolean
  decision_report_version: number | null
  /** null for a live side. */
  generated_at: string | null
}

export type AICompareReason =
  | 'no_primary_suite'
  | 'run_lacks_suite'
  | 'no_previous_run'
  | 'baseline_not_analysed'
  | 'current_not_analysed'
  | (string & {})

export interface AICompareBaseline extends RunBrief {
  /** `default` | `explicit` */
  selection: string
  selection_reason: string
  branch_mismatch: boolean
}

/** `GET /runs/{id}/ai-compare?baseline_run_id=&suite_name=&version_id=&baseline_version_id=` */
export interface AICompare extends AIEnvelopeFields {
  run_id: string
  project_id: string
  baseline: AICompareBaseline | null
  reason: AICompareReason | null
  message: string | null
  current_ref: AIVersionRef | null
  baseline_ref: AIVersionRef | null
  sides: { baseline: DigestSide | null; current: DigestSide | null } | null
  /** Mode `cross_run`; null whenever `reason` is set. */
  diff: AIDigestDiff | null
  /** null whenever `diff` is null. */
  narrative: AICompareNarrative | null
}

/** The bounded AI diff the Run Compare payload carries (`RunCompareResponse.ai_analysis_diff`, §7.1). */
export interface AIAnalysisDiffBrief {
  available: boolean
  /** `baseline_not_analysed` | `current_not_analysed` | `ai_history_unavailable` | `different_projects` */
  reason?: string | null
  left_ref?: string | null
  right_ref?: string | null
  verdict?: AIDiffVerdict | null
  provenance?: Pick<AIDiffProvenance, 'changed' | 'engine_only' | 'note' | 'engine'> | null
  counts?: {
    clusters_added: number
    clusters_removed: number
    clusters_resized: number
    tests_added: number
    tests_removed: number
    tests_changed: number
    categories_changed: number
  } | null
  clusters_added?: { label: string; size: number }[]
  clusters_removed?: { label: string; size: number }[]
  test_changes?: {
    test_name: string | null
    suite_name: string | null
    category_from: string | null
    category_to: string | null
    confidence_from: number | null
    confidence_to: number | null
  }[]
  ai_changes?: string[]
}
