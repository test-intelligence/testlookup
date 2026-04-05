export interface ReleasePhase {
  id: string
  release_id: string
  name: string
  phase_type: string
  status: string
  description: string | null
  order_index: number
  planned_start: string | null
  planned_end: string | null
  actual_start: string | null
  actual_end: string | null
  exit_criteria: Record<string, unknown> | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface Release {
  id: string
  project_id: string
  project_name?: string | null
  name: string
  version: string | null
  description: string | null
  status: string
  planned_date: string | null
  released_at: string | null
  created_at: string
  updated_at: string
  phases: ReleasePhase[]
  test_run_count?: number
}

export interface LinkedRun {
  id: string
  build_number: string | null
  status: string
  total_tests: number
  passed_tests: number
  failed_tests: number
  broken_tests: number
  skipped_tests: number
  pass_rate: number | null
  created_at: string
  phase_id: string | null
}

export interface ReleaseMetrics {
  total_runs: number
  total_tests: number
  total_passed: number
  total_failed: number
  avg_pass_rate: number | null
}

export interface ReleaseDetail extends Release {
  linked_runs: LinkedRun[]
  metrics: ReleaseMetrics
}
