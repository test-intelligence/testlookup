/**
 * Attribution verdicts — is this failure yours, flaky, or the environment's?
 *
 * Mirrors the backend `AttributionVerdict` enum and the
 * `GET /api/v1/runs/{run_id}/attribution` payload.
 *
 * Roughly 84% of pass→fail transitions involve a flaky test, so a raw failure
 * list is mostly noise. The verdict says what each failure appears to be, and
 * carries the five signals it was composed from — because when a verdict
 * disagrees with an engineer, the useful question is *which input was wrong*,
 * and that is unanswerable from a bare label.
 */

/**
 * The four answers. `UNCERTAIN` is a real answer, not an absence of one — the
 * backend emits it whenever the signals disagree or are too thin, rather than
 * resolving to whichever looked strongest.
 *
 * Kept as a union rather than a string so adding a member without handling it
 * everywhere is a type error. This is the vocabulary-subset defect class that
 * produced F-074, F-078 and UAT-002.
 */
export type AttributionVerdict =
  | 'LIKELY_YOUR_CHANGE'
  | 'LIKELY_FLAKY'
  | 'LIKELY_INFRA'
  | 'UNCERTAIN'

/** Every member, for exhaustive rendering and tests. */
export const ATTRIBUTION_VERDICTS: readonly AttributionVerdict[] = [
  'LIKELY_YOUR_CHANGE',
  'LIKELY_INFRA',
  'LIKELY_FLAKY',
  'UNCERTAIN',
] as const

/** The five composed signals, exactly as the verdict saw them. */
export interface AttributionInputs {
  is_new_failure: boolean
  last_green_run_id: string | null
  flaky_score: number | null
  flaky_confidence: string
  cluster_key: string | null
  cluster_cause_family: string | null
  cluster_size: number
  change_overlap: number | null
  changed_files: string[]
  calibration_mode: string
  calibration_specificity: number | null
}

export interface AttributionItem {
  test_case_id: string
  test_name: string | null
  suite_name: string | null
  verdict: AttributionVerdict
  verdict_label: string
  verdict_description: string
  /** Ranks what a human sees. Never used to hide anything. */
  confidence: number
  rationale: string
  inputs: AttributionInputs
  votes: Record<string, number>
  /** Restated by the API on every item; rendered so it cannot be missed. */
  policy: string
}

export interface RunAttributionResponse {
  items: AttributionItem[]
  total: number
}
