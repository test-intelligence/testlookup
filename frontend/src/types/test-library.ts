/**
 * W13 "test-library" wire shapes (TCL-01, TCL-03, TCL-04, TCL-05, TCL-06).
 *
 * Mirrors the backend's `app.models.schemas` models of the same names. Every
 * enum-like field is a plain `string` (the vocabularies live in ONE backend
 * module, `core/test_library_vocab.py`); every text field is rendered as React
 * TEXT, never as HTML, an `href` or a `style`.
 */

// ── TCL-03: library health (GET /api/v1/test-management/health) ────────────

export interface LibraryHealthComponent {
  /** `automation` | `stale_drafts` | `review_freshness` | `retired_in_open_plans` */
  key: string
  label: string
  weight: number
  /** False when its denominator is 0 (then value / numerator are null). */
  measured: boolean
  /** The component's 0..100 score. */
  value?: number | null
  numerator?: number | null
  denominator?: number | null
  /** One sentence with both numbers, e.g. "13 of 34 cases automated (target 60 %)". */
  detail: string
}

export interface LibraryCaseRow {
  id: string
  title: string
  status: string
  /** Full name, else username; null = nobody. */
  owner?: string | null
  /** Review queue: days in review; stale lists: days since the last change. */
  age_days?: number | null
  /** "flaky score 0.72 (high confidence)" / "owner deactivated" / "unassigned". */
  detail?: string | null
}

export interface LibraryCaseList {
  /** Every match: a count, not the page length. */
  total: number
  /** At most five (the preview). */
  items: LibraryCaseRow[]
  measured?: boolean
  /** When not measured: the server's sentence. */
  reason?: string | null
}

export interface ReviewQueueSummary extends LibraryCaseList {
  /** Null when the queue is empty. */
  oldest_age_days?: number | null
}

export interface StrategyGapRead {
  kind: string
  /** `critical` | `warn` */
  severity: string
  title: string
  detail: string
  /** The pill's number (cases; for automation_below_target the % points gap). */
  count: number
  pill: string
}

export interface RequirementGap {
  /** The key as first written (a link's typed key, else the extracted id). */
  key: string
  normalized_key: string
  /** `no_link` | `related_only` | `partly_verified` */
  reason: string
  /** `link` | `generation_batch` | `knowledge_source` */
  sources: string[]
  linked_cases?: number
  batch_claimed_cases?: number
}

export interface RequirementCoverageSummary {
  /** False when the project knows no requirement key. */
  measured: boolean
  known: number
  verified: number
  partly_verified: number
  gaps: number
  /** A source held more than 5,000 keys: the counts are lower bounds. */
  truncated?: boolean
  reason?: string | null
  top_gaps?: RequirementGap[]
}

export interface LibraryHealth {
  project_id: string
  computed_at: string
  /** False when the project has no non-retired authored case. */
  measured: boolean
  total_cases: number
  active_cases: number
  retired_cases: number
  automated_cases: number
  automation_target_pct: number
  stale_drafts: number
  retired_in_open_plans: number
  avg_age_days?: number | null
  older_than_180_days?: number
  older_than_180_pct?: number | null
  /** 0..100; null when not measured. */
  score?: number | null
  /** `Healthy` | `Needs attention` | `At risk`; null when not measured. */
  tag?: string | null
  components: LibraryHealthComponent[]
  review_queue: ReviewQueueSummary
  strategy_gaps: StrategyGapRead[]
  flaky_active: LibraryCaseList
  no_owner: LibraryCaseList
  /** The authored suite names (the Suite filter's options), sorted, at most 500. */
  suite_names: string[]
  suite_names_total: number
  /** Null when the traceability flag is off for the project. */
  requirements?: RequirementCoverageSummary | null
}

// ── TCL-04: import (POST /api/v1/test-management/cases/import) ─────────────

export interface ImportHeaderReport {
  recognised: string[]
  ignored: string[]
  unknown: string[]
}

export interface ImportDuplicateOf {
  case_id?: string | null
  title?: string | null
  /** An earlier row of the same file. */
  row?: number | null
}

export interface ImportRowResult {
  /** The spreadsheet row number (the header is row 1). */
  row: number
  /** `create` | `duplicate` | `invalid` | `created` */
  outcome: string
  title?: string | null
  reasons?: string[]
  warnings?: string[]
  duplicate_of?: ImportDuplicateOf | null
  /** Outcome `created` only. */
  case_id?: string | null
}

export interface ImportSummary {
  rows: number
  create: number
  duplicate: number
  invalid: number
  created?: number
}

export interface TestCaseImportResponse {
  project_id: string
  dry_run: boolean
  file_name: string
  /** `csv` | `xlsx` */
  format: string
  headers: ImportHeaderReport
  summary: ImportSummary
  rows: ImportRowResult[]
}

// ── TCL-05: the case timeline (GET /cases/{case_id}/timeline) ───────────────

export interface TimelineLink {
  /** `run_result` | `run` | `plan` | `defect` */
  type: string
  run_id?: string | null
  /** A per-run RESULT id (run_result). */
  test_case_id?: string | null
  plan_id?: string | null
  defect_id?: string | null
  jira_key?: string | null
}

export interface TimelineEntry {
  id: string
  /** `version` | `review_requested` | `review_decided` | `comment` | `plan_execution` | `automated_execution` | `defect` | `requirement` */
  kind: string
  occurred_at: string
  actor_id?: string | null
  /** Null = nobody recorded (shown "not recorded", never blank). */
  actor_name?: string | null
  /** `person` | `ai` | `api` on version rows; `automation` on runs. */
  actor_source?: string | null
  title: string
  detail?: string | null
  status?: string | null
  version?: number | null
  link?: TimelineLink | null
}

export interface TestCaseTimeline {
  case_id: string
  kinds: string[]
  items: TimelineEntry[]
  /** Null = the start of the timeline (a stream is paged, not counted). */
  next_cursor?: string | null
  automation_linked?: boolean
}

// ── TCL-06: requirement links and gaps ──────────────────────────────────────

export interface RequirementLink {
  id: string
  test_case_id: string
  requirement_key: string
  normalized_key: string
  requirement_title?: string | null
  /** `verifies` | `partially_verifies` | `related` */
  link_type: string
  created_by_name?: string | null
  created_at: string
}

export interface RequirementLinkList {
  /** Oldest first. */
  items: RequirementLink[]
  total: number
}

export interface TraceabilityGaps {
  project_id: string
  summary: RequirementCoverageSummary
  gaps: RequirementGap[]
  gaps_total: number
  page: number
  size: number
  cases_without_requirements_total: number
  cases_without_requirements: LibraryCaseRow[]
}

// ── TCL-01: a saved case view (a saved_views row's `filters`) ───────────────

export interface CaseViewFilters {
  kind: 'test_case_view'
  statuses?: string[]
  priorities?: string[]
  types?: string[]
  /** `me` (the READER, also in a shared view) or a user id. */
  owner?: string | null
  /** `automated` | `not_automated` */
  automation?: string | null
  suite?: string | null
  q?: string | null
  ai_generated?: boolean | null
  requirement?: string | null
}
