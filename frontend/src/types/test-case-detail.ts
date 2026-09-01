import type { RunTestCase } from './runs'

/** Public client identifier for the rich test-case detail contract. */
export const TEST_CASE_DETAIL_CONTRACT = 'test-case-detail' as const
export const TEST_CASE_DETAIL_SCHEMA_VERSION = 1 as const

export interface TestCaseParameter {
  name: string
  /** Null for masked values or values not supplied by the source. */
  value: string | null
  display_value?: string | null
  mode?: string | null
  masked?: boolean
  excluded_from_history?: boolean
}

export interface TestCaseLink {
  url: string
  name?: string | null
  type?: string | null
}

export interface TestCaseDetailAttachment {
  id: string
  test_step_id?: string | null
  source_test_run_id?: string | null
  name: string
  source_ref?: string | null
  media_type?: string | null
  size_bytes?: number | null
  checksum?: string | null
  source_scope?: 'test' | 'step' | 'fixture' | 'run' | string | null
  created_at?: string | null
}

export interface TestCaseDetailStep {
  id: string
  parent_step_id?: string | null
  ordinal: number
  depth: number
  name: string
  keyword?: string | null
  category?: string | null
  status: string
  started_at?: string | null
  duration_ms?: number | null
  action?: string | null
  expected?: string | null
  actual?: string | null
  expected_value?: string | null
  actual_value?: string | null
  start_ms?: number | null
  assertion_message?: string | null
  assertion_trace?: string | null
  error_message?: string | null
  error_trace?: string | null
  parameters?: TestCaseParameter[] | Record<string, unknown>
  attachments?: TestCaseDetailAttachment[]
  links?: TestCaseLink[]
  steps: TestCaseDetailStep[]
}

export interface TestCaseIdentity {
  test_case_id?: string | null
  test_run_id?: string | null
  source_uuid?: string | null
  source_history_id?: string | null
  source_test_case_id?: string | null
  history_id?: string | null
  uuid?: string | null
  full_name?: string | null
  fingerprint?: string | null
  display_name?: string | null
}

export interface TestCaseClassificationValue {
  name: string
  source?: 'explicit' | 'mapping' | 'derived' | 'label' | 'unknown' | string | null
  confidence?: 'high' | 'medium' | 'low' | string | null
}

export interface TestCaseClassification {
  suite?: {
    parent?: string | null
    name?: string | null
    sub_suite?: string | null
    legacy_name?: string | null
  } | null
  service?: TestCaseClassificationValue | null
  components?: TestCaseClassificationValue[]
  class_name?: string | null
  suite_name?: string | null
  package_name?: string | null
  severity?: string | null
  feature?: string | null
  story?: string | null
  epic?: string | null
  owner?: string | null
  service_name?: string | null
  component_name?: string | null
  package_or_module?: string | null
  file_path?: string | null
  framework?: string | null
  language?: string | null
  tags?: string[]
  labels?: Array<{ name: string; value: string; source?: string | null }>
}

export interface TestCaseDefinition {
  id?: string | null
  version?: number | null
  title?: string | null
  description?: string | null
  objective?: string | null
  preconditions?: string | null
  expected_result?: string | null
  test_data?: string | null
  parameters?: TestCaseParameter[]
  steps?: Array<{
    step_number: number
    action: string
    expected_result?: string | null
  }> | null
  test_type?: string | null
  priority?: string | null
  severity?: string | null
  suite_name?: string | null
  tags?: string[]
}

export interface TestCaseExecution {
  status?: string | null
  stage?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  retry_count?: number | null
  is_flaky?: boolean | null
  is_flaky_run?: boolean | null
  step_count?: number | null
  steps_present?: boolean
  has_attachments?: boolean
  failure_category?: string | null
  error_message?: string | null
  stack_trace?: string | null
  parameters?: TestCaseParameter[]
  error?: {
    message?: string | null
    trace?: string | null
    category?: string | null
  } | null
}

export interface TestCaseProvenance {
  source_test_run_id?: string | null
  format?: string | null
  parser_format?: string | null
  format_version?: string | null
  source_file?: string | null
  parser_version?: string | null
  minio_s3_prefix?: string | null
  field_sources?: Record<string, string>
  warnings?: string[]
  contract_version?: string | null
}

/**
 * Versioned enriched response. Every field beyond the legacy identity is
 * optional so the client can consume the existing run endpoints during the
 * backend migration.
 */
export interface TestCaseDetailV1 {
  contract: typeof TEST_CASE_DETAIL_CONTRACT
  schema_version: typeof TEST_CASE_DETAIL_SCHEMA_VERSION
  contract_version?: string | null
  id: string
  test_name: string
  canonical_test_case_id?: string | null
  project_id?: string | null
  identity?: TestCaseIdentity | null
  classification?: TestCaseClassification | null
  definition?: TestCaseDefinition | null
  execution?: TestCaseExecution | null
  steps?: TestCaseDetailStep[] | null
  steps_present?: boolean
  attachments?: TestCaseDetailAttachment[] | null
  links?: TestCaseLink[] | null
  provenance?: TestCaseProvenance | null
  extensions?: Record<string, unknown> | null
}

/** API response during the compatibility window. */
export type TestCaseDetailResponse = RunTestCase &
  Partial<Omit<TestCaseDetailV1, 'id' | 'test_name'>> & {
    detail?: Partial<TestCaseDetailV1> | null
  }

/** Runtime-safe model consumed by the rich detail components. */
export interface NormalizedTestCaseDetail {
  source: 'legacy' | 'enriched'
  contract: typeof TEST_CASE_DETAIL_CONTRACT | null
  schema_version: number | null
  id: string
  test_name: string
  canonical_test_case_id: string | null
  project_id: string | null
  identity: TestCaseIdentity | null
  classification: TestCaseClassification | null
  definition: TestCaseDefinition | null
  execution: TestCaseExecution | null
  /** Null means the source did not supply steps; [] means it supplied none. */
  steps: TestCaseDetailStep[] | null
  steps_present: boolean | null
  attachments: TestCaseDetailAttachment[] | null
  links: TestCaseLink[] | null
  provenance: TestCaseProvenance | null
  extensions: Record<string, unknown> | null
}
