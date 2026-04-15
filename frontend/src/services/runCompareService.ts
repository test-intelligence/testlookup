import { getData } from './http'

export interface RunCompareSummary {
  id: string
  project_id: string
  build_number: string | null
  branch: string | null
  commit_hash: string | null
  status: string | null
  total_tests: number
  passed_tests: number
  failed_tests: number
  broken_tests: number
  skipped_tests: number
  pass_rate: number | null
  duration_ms: number | null
  start_time: string | null
  end_time: string | null
}

export type RunCompareClassification =
  | 'new_failure'
  | 'fixed'
  | 'still_failing'
  | 'regressed'
  | 'improved'
  | 'new_test'
  | 'removed_test'
  | 'duration_spike'
  | 'renamed'

export type RunComparePairedBy = 'fingerprint' | 'fuzzy_name_match'

export interface RunCompareTestDelta {
  test_fingerprint: string
  test_name: string | null
  suite_name: string | null
  left_status: string | null
  right_status: string | null
  left_duration_ms: number | null
  right_duration_ms: number | null
  delta_duration_ms: number | null
  classification: RunCompareClassification
  // Backend adds these on the second-pass fuzzy-name matcher (run
  // compare fuzzy pairing). Legacy responses omit them — default to
  // ``"fingerprint"`` when absent.
  paired_by?: RunComparePairedBy | null
  previous_test_name?: string | null
  previous_test_fingerprint?: string | null
}

export interface RunCompareResponse {
  left: RunCompareSummary
  right: RunCompareSummary
  delta_total: number
  delta_passed: number
  delta_failed: number
  delta_broken: number
  delta_skipped: number
  delta_pass_rate: number | null
  delta_duration_ms: number | null
  new_failures: number
  fixed: number
  still_failing: number
  regressed: number
  improved: number
  new_tests: number
  removed_tests: number
  duration_spikes: number
  renamed: number
  test_deltas: RunCompareTestDelta[]
  truncated: boolean
}

export const runCompareService = {
  compare: (left: string, right: string) =>
    getData<RunCompareResponse>('/api/v1/runs/compare', {
      params: { left, right },
    }),
}
