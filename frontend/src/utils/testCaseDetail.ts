import type {
  NormalizedTestCaseDetail,
  TestCaseClassification,
  TestCaseClassificationValue,
  TestCaseDefinition,
  TestCaseDetailAttachment,
  TestCaseDetailStep,
  TestCaseExecution,
  TestCaseIdentity,
  TestCaseLink,
  TestCaseParameter,
  TestCaseProvenance,
} from '@/types/test-case-detail'

type UnknownRecord = Record<string, unknown>

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const stringValue = (value: unknown): string | null =>
  typeof value === 'string' && value.trim().length > 0 ? value : null

const nullableString = (value: unknown): string | null =>
  typeof value === 'string' ? value : null

const numberValue = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const booleanValue = (value: unknown): boolean | null =>
  typeof value === 'boolean' ? value : null

const stringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

const recordArray = (value: unknown): UnknownRecord[] =>
  Array.isArray(value) ? value.filter(isRecord) : []

const hasOwn = (record: UnknownRecord, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(record, key)

function displayValue(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return '[unavailable]'
  }
}

function normalizeParameter(raw: UnknownRecord, index: number): TestCaseParameter {
  const mode = nullableString(raw.mode)
  const masked = raw.masked === true || ['masked', 'hidden', 'redacted'].includes(mode?.toLowerCase() ?? '')
  return {
    name: stringValue(raw.name) ?? `Parameter ${index + 1}`,
    // Never retain the source value once the producer marked it sensitive.
    value: masked ? null : displayValue(raw.value),
    display_value: masked ? 'Masked' : displayValue(raw.display_value ?? raw.value),
    mode,
    masked,
    excluded_from_history: raw.excluded_from_history === true,
  }
}

export function normalizeParameters(value: unknown): TestCaseParameter[] {
  if (Array.isArray(value)) return recordArray(value).map(normalizeParameter)
  if (isRecord(value)) {
    return Object.entries(value).map(([name, item], index) => normalizeParameter({ name, value: item }, index))
  }
  return []
}

function normalizeAttachment(raw: UnknownRecord, index: number): TestCaseDetailAttachment {
  return {
    id: stringValue(raw.id) ?? `attachment-${index + 1}`,
    test_step_id: nullableString(raw.test_step_id),
    source_test_run_id: nullableString(raw.source_test_run_id),
    name: stringValue(raw.name) ?? `Attachment ${index + 1}`,
    source_ref: nullableString(raw.source_ref ?? raw.url),
    media_type: nullableString(raw.media_type ?? raw.content_type),
    size_bytes: numberValue(raw.size_bytes ?? raw.size),
    checksum: nullableString(raw.checksum),
    source_scope: nullableString(raw.source_scope ?? raw.scope),
    created_at: nullableString(raw.created_at),
  }
}

export function normalizeAttachments(value: unknown): TestCaseDetailAttachment[] {
  return recordArray(value).map(normalizeAttachment)
}

function normalizeLink(raw: UnknownRecord): TestCaseLink | null {
  const url = stringValue(raw.url ?? raw.href)
  if (!url) return null
  return {
    url,
    name: nullableString(raw.name ?? raw.display_name),
    type: nullableString(raw.type ?? raw.link_type),
  }
}

export function normalizeLinks(value: unknown): TestCaseLink[] {
  return recordArray(value)
    .map(raw => normalizeLink(raw))
    .filter((link): link is TestCaseLink => link !== null)
}

function normalizeStep(raw: unknown, index: number, depth: number, parentId: string | null): TestCaseDetailStep | null {
  if (!isRecord(raw)) return null
  const id = stringValue(raw.id) ?? `step-${depth}-${index + 1}`
  const childrenValue = hasOwn(raw, 'steps') ? raw.steps : raw.children
  const steps = recordArray(childrenValue)
    .map((child, childIndex) => normalizeStep(child, childIndex, depth + 1, id))
    .filter((child): child is TestCaseDetailStep => child !== null)

  return {
    id,
    parent_step_id: nullableString(raw.parent_step_id) ?? parentId,
    ordinal: numberValue(raw.ordinal) ?? index,
    depth: numberValue(raw.depth) ?? depth,
    name: stringValue(raw.name ?? raw.title) ?? `Step ${index + 1}`,
    keyword: nullableString(raw.keyword),
    category: nullableString(raw.category),
    status: stringValue(raw.status)?.toUpperCase() ?? 'UNKNOWN',
    started_at: nullableString(raw.started_at),
    duration_ms: numberValue(raw.duration_ms),
    action: nullableString(raw.action),
    expected: nullableString(raw.expected ?? raw.expected_value),
    actual: nullableString(raw.actual ?? raw.actual_value),
    expected_value: nullableString(raw.expected_value ?? raw.expected),
    actual_value: nullableString(raw.actual_value ?? raw.actual),
    start_ms: numberValue(raw.start_ms),
    assertion_message: nullableString(raw.assertion_message),
    assertion_trace: nullableString(raw.assertion_trace),
    error_message: nullableString(raw.error_message),
    error_trace: nullableString(raw.error_trace),
    parameters: hasOwn(raw, 'parameters') ? normalizeParameters(raw.parameters) : undefined,
    attachments: hasOwn(raw, 'attachments') ? normalizeAttachments(raw.attachments) : undefined,
    links: hasOwn(raw, 'links') ? normalizeLinks(raw.links) : undefined,
    steps,
  }
}

export function normalizeSteps(value: unknown): TestCaseDetailStep[] {
  return recordArray(value)
    .map((step, index) => normalizeStep(step, index, 0, null))
    .filter((step): step is TestCaseDetailStep => step !== null)
}

function normalizeIdentity(record: UnknownRecord): TestCaseIdentity | null {
  const identity = isRecord(record.identity) ? record.identity : {}
  const result: TestCaseIdentity = {
    test_case_id: stringValue(identity.test_case_id ?? identity.source_test_case_id ?? record.test_case_id ?? record.source_test_case_id),
    test_run_id: stringValue(identity.test_run_id ?? record.test_run_id),
    source_uuid: stringValue(identity.source_uuid ?? record.source_uuid),
    source_history_id: stringValue(identity.source_history_id ?? record.source_history_id),
    source_test_case_id: stringValue(identity.source_test_case_id ?? record.source_test_case_id),
    history_id: stringValue(identity.history_id ?? identity.source_history_id ?? record.history_id ?? record.source_history_id),
    uuid: stringValue(identity.uuid ?? identity.source_uuid ?? record.uuid ?? record.source_uuid),
    full_name: stringValue(identity.full_name ?? record.full_name),
    fingerprint: stringValue(identity.fingerprint ?? identity.test_fingerprint ?? record.test_fingerprint ?? record.fingerprint),
    display_name: stringValue(identity.display_name ?? identity.test_name ?? record.test_name ?? record.name),
  }
  return Object.values(result).some(value => value !== null) ? result : null
}

function classificationValue(value: unknown, fallbackSource?: string | null): TestCaseClassificationValue | null {
  if (typeof value === 'string') return { name: value, source: fallbackSource ?? 'explicit' }
  if (!isRecord(value)) return null
  const name = stringValue(value.name)
  return name
    ? {
        name,
        source: nullableString(value.source) ?? fallbackSource,
        confidence: nullableString(value.confidence),
      }
    : null
}

function normalizeClassification(record: UnknownRecord): TestCaseClassification | null {
  const source = isRecord(record.classification) ? record.classification : {}
  const suite = isRecord(source.suite)
    ? {
        parent: nullableString(source.suite.parent),
        name: nullableString(source.suite.name),
        sub_suite: nullableString(source.suite.sub_suite),
        legacy_name: nullableString(source.suite.legacy_name),
      }
    : stringValue(source.suite_name ?? record.suite_name)
      ? { name: stringValue(source.suite_name ?? record.suite_name) }
      : null
  const service = classificationValue(source.service ?? source.service_name ?? record.service_name)
  const components = recordArray(source.components)
    .map(value => classificationValue(value))
    .filter((value): value is TestCaseClassificationValue => value !== null)
  const component = classificationValue(source.component ?? source.component_name ?? record.component_name)
  if (component && !components.some(value => value.name === component.name)) components.push(component)
  const labels = recordArray(source.labels)
    .map(label => {
      const name = stringValue(label.name)
      const value = stringValue(label.value)
      return name && value ? { name, value, source: nullableString(label.source) } : null
    })
    .filter((label): label is { name: string; value: string; source: string | null } => label !== null)
  const result: TestCaseClassification = {
    suite,
    service,
    components,
    class_name: stringValue(source.class_name ?? record.class_name),
    suite_name: stringValue(source.suite_name ?? record.suite_name),
    package_name: stringValue(source.package_name ?? record.package_name),
    severity: stringValue(source.severity ?? record.severity),
    feature: stringValue(source.feature ?? record.feature),
    story: stringValue(source.story ?? record.story),
    epic: stringValue(source.epic ?? record.epic),
    owner: stringValue(source.owner ?? record.owner),
    service_name: stringValue(source.service_name ?? record.service_name),
    component_name: stringValue(source.component_name ?? record.component_name),
    package_or_module: stringValue(source.package_or_module ?? source.package_name ?? record.package_or_module ?? record.package_name),
    file_path: stringValue(source.file_path),
    framework: stringValue(source.framework),
    language: stringValue(source.language),
    tags: stringArray(source.tags ?? record.tags),
    labels,
  }
  const hasValue = Boolean(
    result.suite || result.service || (result.components?.length ?? 0) || result.class_name ||
      result.package_or_module || result.file_path || result.framework || result.language ||
      (result.tags?.length ?? 0) || (result.labels?.length ?? 0),
  )
  return hasValue ? result : null
}

function normalizeDefinition(record: UnknownRecord): TestCaseDefinition | null {
  const value = isRecord(record.definition) ? record.definition : {}
  const result: TestCaseDefinition = {
    id: stringValue(value.id),
    version: numberValue(value.version),
    title: nullableString(value.title),
    description: nullableString(value.description),
    objective: nullableString(value.objective),
    preconditions: nullableString(value.preconditions),
    expected_result: nullableString(value.expected_result),
    test_data: nullableString(value.test_data),
    parameters: hasOwn(value, 'parameters') ? normalizeParameters(value.parameters) : undefined,
    steps: Array.isArray(value.steps)
      ? value.steps.filter(isRecord).map(step => ({
          step_number: numberValue(step.step_number) ?? 0,
          action: stringValue(step.action) ?? '',
          expected_result: nullableString(step.expected_result),
        }))
      : null,
    test_type: nullableString(value.test_type),
    priority: nullableString(value.priority),
    severity: nullableString(value.severity),
    suite_name: nullableString(value.suite_name),
    tags: stringArray(value.tags),
  }
  return Object.values(result).some(item => item != null && (!Array.isArray(item) || item.length > 0)) ? result : null
}

function normalizeExecution(record: UnknownRecord): TestCaseExecution | null {
  const value = isRecord(record.execution) ? record.execution : {}
  const error = isRecord(value.error)
    ? {
        message: nullableString(value.error.message),
        trace: nullableString(value.error.trace),
        category: nullableString(value.error.category),
      }
    : null
  const result: TestCaseExecution = {
    status: nullableString(value.status ?? record.status),
    stage: nullableString(value.stage),
    started_at: nullableString(value.started_at),
    finished_at: nullableString(value.finished_at),
    duration_ms: numberValue(value.duration_ms ?? record.duration_ms),
    retry_count: numberValue(value.retry_count),
    is_flaky: booleanValue(value.is_flaky ?? value.is_flaky_run),
    is_flaky_run: booleanValue(value.is_flaky_run ?? value.is_flaky),
    step_count: numberValue(value.step_count ?? record.step_count),
    steps_present: booleanValue(value.steps_present ?? record.steps_present) ?? undefined,
    has_attachments: booleanValue(value.has_attachments ?? record.has_attachments) ?? undefined,
    failure_category: nullableString(value.failure_category ?? record.failure_category),
    error_message: nullableString(value.error_message ?? record.error_message),
    stack_trace: nullableString(value.stack_trace ?? record.stack_trace),
    parameters: hasOwn(value, 'parameters') ? normalizeParameters(value.parameters) : undefined,
    error,
  }
  return Object.values(result).some(item => item != null) ? result : null
}

function normalizeProvenance(record: UnknownRecord): TestCaseProvenance | null {
  const value = isRecord(record.provenance) ? record.provenance : {}
  const fieldSources = isRecord(value.field_sources)
    ? Object.fromEntries(Object.entries(value.field_sources).filter(([, item]) => typeof item === 'string')) as Record<string, string>
    : {}
  const result: TestCaseProvenance = {
    source_test_run_id: nullableString(value.source_test_run_id),
    format: nullableString(value.format ?? value.parser_format),
    parser_format: nullableString(value.parser_format),
    format_version: nullableString(value.format_version),
    source_file: nullableString(value.source_file),
    parser_version: nullableString(value.parser_version),
    minio_s3_prefix: nullableString(value.minio_s3_prefix),
    field_sources: fieldSources,
    warnings: stringArray(value.warnings),
    contract_version: nullableString(value.contract_version ?? record.contract_version),
  }
  return Object.values(result).some(item => item != null && (!Array.isArray(item) || item.length > 0) && (!isRecord(item) || Object.keys(item).length > 0))
    ? result
    : null
}

/**
 * Convert either the legacy run response or the v1 enriched response into a
 * render-safe shape. This is intentionally tolerant: a malformed optional
 * child is skipped, while the test case itself remains visible.
 */
export function normalizeTestCaseDetail(payload: unknown): NormalizedTestCaseDetail {
  const outer = isRecord(payload) ? payload : {}
  const detail = isRecord(outer.detail) ? outer.detail : {}
  const record: UnknownRecord = { ...outer, ...detail }
  const hasEnrichedFields = typeof record.contract === 'string' || hasOwn(record, 'contract_version') || hasOwn(outer, 'detail') ||
    hasOwn(record, 'identity') || hasOwn(record, 'classification') || hasOwn(record, 'provenance') ||
    hasOwn(record, 'extensions') || hasOwn(record, 'steps_present')
  const stepsSupplied = hasOwn(record, 'steps')
  const attachmentsSupplied = hasOwn(record, 'attachments')
  const linksSupplied = hasOwn(record, 'links')
  const schemaVersion = numberValue(record.schema_version) ?? (record.contract_version === '1' ? 1 : null)
  const normalizedSteps = stepsSupplied ? normalizeSteps(record.steps) : null

  return {
    source: hasEnrichedFields ? 'enriched' : 'legacy',
    contract: record.contract === 'test-case-detail' || hasOwn(record, 'contract_version') ? 'test-case-detail' : null,
    schema_version: schemaVersion,
    id: stringValue(record.id) ?? '',
    test_name: stringValue(record.test_name ?? record.name) ?? 'Unnamed test case',
    canonical_test_case_id: stringValue(record.canonical_test_case_id),
    project_id: stringValue(record.project_id),
    identity: normalizeIdentity(record),
    classification: normalizeClassification(record),
    definition: normalizeDefinition(record),
    execution: normalizeExecution(record),
    steps: normalizedSteps,
    steps_present: typeof record.steps_present === 'boolean' ? record.steps_present : normalizedSteps ? normalizedSteps.length > 0 : null,
    attachments: attachmentsSupplied ? normalizeAttachments(record.attachments) : null,
    links: linksSupplied ? normalizeLinks(record.links) : null,
    provenance: normalizeProvenance(record),
    extensions: isRecord(record.extensions) ? record.extensions : null,
  }
}
