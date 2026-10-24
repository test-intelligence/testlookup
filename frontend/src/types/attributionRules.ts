/**
 * Release attribution rules (W11 REL-09): rung 3 of the attribution ladder.
 *
 * The shapes of `/api/v1/projects/{project_id}/attribution-rules` (list, CRUD,
 * `GET …/fields`, `POST …/preview?detail=true`). A rule targets a release by
 * NAME: a name no release carries yet is created by the first matching run.
 */

export interface AttributionRule {
  id: string
  project_id: string
  name: string
  /** An `AttributionMatchField` value (`ATTRIBUTION_FIELD_FALLBACK`). */
  match_field: string
  match_pattern: string
  target_release_name: string
  /** Lower runs first; ties run in creation order. */
  priority: number
  is_enabled: boolean
  created_at: string | null
}

/** `GET …/attribution-rules`: in the order the ladder tries them (priority, created_at, id). */
export interface AttributionRuleList {
  items: AttributionRule[]
  total: number
}

/** The body of create and preview (the server's `AttributionRuleIn`). */
export interface AttributionRuleInput {
  name: string
  match_field: string
  match_pattern: string
  target_release_name: string
  priority: number
  is_enabled: boolean
}

export type AttributionRuleUpdate = Partial<AttributionRuleInput>

/** One field a rule can match on (`GET …/fields`). */
export interface AttributionRuleField {
  value: string
  label: string
  /** True: `*` and `?` wildcards; false: exact, case-insensitive. */
  glob: boolean
  help: string
}

export interface AttributionRuleFieldsResponse {
  fields: AttributionRuleField[]
}

/** Where a matched run would land if the rule were saved (`PREVIEW_LANDINGS` on the server). */
export const PREVIEW_LANDINGS = ['rule', 'earlier_rule', 'explicit'] as const
export type PreviewLanding = (typeof PREVIEW_LANDINGS)[number]

export interface AttributionPreviewSample {
  run_id: string
  build_number: string | null
  branch: string | null
  environment: string | null
  /** `detail=true` only (a missing field reads its default). */
  tags?: string[]
  created_at?: string | null
  /** Its primary release today; null = Unattributed. */
  current_release_name?: string | null
  would_land?: string
}

export interface AttributionPreviewResponse {
  considered: number
  matched: number
  field_present: number
  /** The first 10 matched runs, newest first. */
  sample: AttributionPreviewSample[]
  /** `detail=true` only: matched runs an earlier enabled rule claims. */
  shadowed?: number
  /** `detail=true` only: matched runs that named their release (rung 1 / 2). */
  explicit?: number
  /** `detail=true` only: a release of the project carries the target name. */
  target_exists?: boolean
  /** `detail=true` only: of `considered`, runs with no release at all. */
  unattributed_recent?: number
}

/**
 * The fields before `GET …/fields` answers (or if it cannot): the server's
 * `ATTRIBUTION_FIELD_LABELS` / `ATTRIBUTION_FIELD_HELP` and the evaluator's
 * glob set, in `AttributionMatchField` declaration order. Pinned against the
 * server by `backend/tests/test_attribution_field_vocab.py` (this file read as
 * text: keep one entry per line pair as written) and `attributionModel.test.ts`.
 */
export const ATTRIBUTION_FIELD_FALLBACK: readonly AttributionRuleField[] = [
  { value: 'branch', label: 'Branch', glob: true,
    help: "The run's git branch. Wildcards: * and ?, e.g. release/2.5*." },
  { value: 'build_number', label: 'Build number', glob: true,
    help: "The run's build label. Wildcards: * and ?, e.g. nightly-*." },
  { value: 'environment', label: 'Environment', glob: false,
    help: "The run's environment, matched exactly (case does not matter), e.g. staging." },
  { value: 'tag', label: 'Tag', glob: true,
    help: "Any of the run's tags. Wildcards: * and ?, e.g. rc-*." },
]
