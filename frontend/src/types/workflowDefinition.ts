export interface WorkflowStep {
  id: string
  agent_id: string
  config_ref?: string | null
  tools: string[]
  reviews: string[]
  model?: Record<string, unknown> | null
}

export interface WorkflowEdge {
  from: string | string[]
  to: string
  when?: Record<string, unknown> | null
  join?: 'all' | 'any' | null
}

export interface WorkflowLoop {
  from: string
  to: string
  when: Record<string, unknown>
  max_iterations: number
}

export interface WorkflowBody {
  workflow_id: string
  name: string
  description?: string | null
  base: 'offline' | 'deep' | 'live'
  steps: WorkflowStep[]
  edges: WorkflowEdge[]
  loops: WorkflowLoop[]
  retry_policy: {
    max_attempts: number
    base_seconds: number
    cap_seconds: number
  }
  review_policy: 'human_required' | 'human_required_plus_auto_reviewer'
  deadline_seconds: number
}

export interface WorkflowDefinition extends Omit<WorkflowBody, 'name' | 'description'> {
  project_id: string | null
  version: number
  steps: WorkflowStep[]
  edges: WorkflowEdge[]
  loops: WorkflowLoop[]
  retry_policy: WorkflowBody['retry_policy']
  review_policy: WorkflowBody['review_policy']
  deadline_seconds: number
}

export interface WorkflowItem {
  id: string
  workflow_id: string
  version: number
  project_id: string | null
  name: string
  description: string | null
  base: WorkflowBody['base']
  definition: WorkflowDefinition
  definition_sha256: string
  status: 'draft' | 'published'
  published_at: string | null
  eval_verdict: 'pass' | 'fail' | 'insufficient_samples' | null
  eval_coverage: number | null
  eval_gate_run_id: string | null
  evaluated_at: string | null
  eval_regression_accepted: boolean
  eval_regression_reason: string | null
  eval_regression_accepted_at: string | null
  read_only: boolean
  built_in: boolean
  created_at?: string
  updated_at?: string
  created_version?: boolean
}

export interface WorkflowValidation {
  valid: boolean
  workflow_id: string
  version: number
  errors: string[]
  validation_scope: 'semantic'
  compiler_validation: 'passed' | 'failed'
}

export interface WorkflowEvaluation {
  verdict: 'pass' | 'fail' | 'insufficient_samples'
  status: string
  reason: string
  workflow_id: string
  version: number
  sample_count: number
  measured_steps: number
  expected_steps: number
  coverage: number
  topology_measured: boolean
  manifest_checksum: string
  regressions: Array<Record<string, unknown>>
}
