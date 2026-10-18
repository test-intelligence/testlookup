/**
 * The in-page test-case edit form's model (W28 TCM-01). Pure: no React, no
 * request.
 *
 *  - formFromCase: the form prefilled from a case. A masked parameter's value
 *    is NEVER prefilled (the server sends it as null; the form shows an empty
 *    password field that, left empty, keeps the stored value).
 *  - payloadFrom(base, form): ONLY the fields whose value differs from the
 *    case as opened (steps / parameters / tags compared as normalised JSON),
 *    so a Save sends what the reader changed and nothing else.
 *  - differingFields(base, fresh): what someone else changed (the 409 notice).
 *  - rebaseForm(base, mine, theirs): "Keep editing" after a conflict — a field
 *    the reader did not touch takes theirs, a field the reader changed keeps
 *    the reader's value.
 *  - the step operations: add / move / remove / update; steps are numbered by
 *    position (1..n) on every change, and a step with a blank action is
 *    dropped on Save.
 */
import type {
  ManagedTestCase,
  ManagedTestCaseUpdateBody,
  TestCaseParameter,
  TestStep,
} from '@/types/test-management'

export interface StepForm {
  /** React key only; never sent. */
  key: number
  action: string
  expected_result: string
}

export interface ParameterForm {
  key: number
  name: string
  /** Empty for a masked row as opened: "Unchanged (masked)". */
  value: string
  masked: boolean
  mode?: string
  /** The row came masked from the server: its stored value is kept unless a new one is typed. */
  storedMasked?: boolean
}

export interface CaseForm {
  title: string
  objective: string
  description: string
  preconditions: string
  steps: StepForm[]
  expected_result: string
  test_data: string
  parameters: ParameterForm[]
  test_type: string
  priority: string
  severity: string
  feature_area: string
  suite_name: string
  /** Comma-separated as typed. */
  tags: string
  /** '' = Unassigned. */
  assignee_id: string
  /** '' = none. */
  estimated_duration_minutes: string
  is_automated: boolean
  automation_status: string
  change_summary: string
}

/** The PATCH's changeable fields (everything but expected_version and change_summary). */
export type CaseChanges = Omit<ManagedTestCaseUpdateBody, 'expected_version' | 'change_summary'>
export type CaseField = keyof CaseChanges

/** Every field the form edits, in the form's order (the PATCH body's key names). */
export const CASE_FIELDS: readonly CaseField[] = [
  'title', 'objective', 'description', 'preconditions', 'steps', 'expected_result', 'test_data', 'parameters',
  'test_type', 'priority', 'severity', 'feature_area', 'suite_name', 'tags', 'assignee_id',
  'estimated_duration_minutes', 'is_automated', 'automation_status',
]

export const FIELD_LABELS: Record<CaseField | 'status', string> = {
  title: 'Title',
  objective: 'Objective',
  description: 'Description',
  preconditions: 'Preconditions',
  steps: 'Steps',
  expected_result: 'Expected result',
  test_data: 'Test data',
  parameters: 'Parameters',
  test_type: 'Test type',
  priority: 'Priority',
  severity: 'Severity',
  feature_area: 'Feature area',
  suite_name: 'Suite',
  tags: 'Tags',
  assignee_id: 'Owner',
  estimated_duration_minutes: 'Estimated duration',
  is_automated: 'Automated',
  automation_status: 'Automation status',
  status: 'Status',
}

/** Create's ten values. */
export const TEST_TYPES = [
  'functional', 'integration', 'e2e', 'regression', 'smoke', 'performance', 'security', 'usability', 'accessibility', 'api',
] as const
export const PRIORITIES = ['critical', 'high', 'medium', 'low'] as const
/** The model's vocabulary (`models/postgres.py`). */
export const SEVERITIES = ['blocker', 'critical', 'major', 'minor', 'trivial'] as const
export const AUTOMATION_STATUSES = ['not_automated', 'in_progress', 'automated', 'broken'] as const

export const TITLE_TOO_SHORT = 'Title must be at least 3 characters'
export const NO_CHANGES = 'No changes to save'

let nextKey = 1
function key(): number {
  nextKey += 1
  return nextKey
}

const MASKED_MODES = new Set(['masked', 'hidden'])

/** A parameter row the server masks: `masked: true`, or mode masked | hidden. */
export function isMaskedParameter(parameter: Pick<TestCaseParameter, 'masked' | 'mode'>): boolean {
  return parameter.masked === true || (typeof parameter.mode === 'string' && MASKED_MODES.has(parameter.mode))
}

function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value : ''
}

export function formFromCase(caseItem: ManagedTestCase): CaseForm {
  return {
    title: text(caseItem.title),
    objective: text(caseItem.objective),
    description: text(caseItem.description),
    preconditions: text(caseItem.preconditions),
    steps: (caseItem.steps ?? []).map((step) => ({
      key: key(),
      action: text(step.action),
      expected_result: text(step.expected_result),
    })),
    expected_result: text(caseItem.expected_result),
    test_data: text(caseItem.test_data),
    parameters: (caseItem.parameters ?? [])
      .filter((parameter): parameter is TestCaseParameter => Boolean(parameter) && typeof parameter === 'object')
      .map((parameter) => {
        const masked = isMaskedParameter(parameter)
        return {
          key: key(),
          name: text(parameter.name),
          // Never prefilled: whatever the answer carried, a masked value stays out of the DOM.
          value: masked ? '' : text(parameter.value),
          masked,
          ...(parameter.mode ? { mode: parameter.mode } : {}),
          ...(masked ? { storedMasked: true } : {}),
        }
      }),
    test_type: text(caseItem.test_type),
    priority: text(caseItem.priority),
    severity: text(caseItem.severity),
    feature_area: text(caseItem.feature_area),
    suite_name: text(caseItem.suite_name),
    tags: (caseItem.tags ?? []).join(', '),
    assignee_id: text(caseItem.assignee_id),
    estimated_duration_minutes:
      typeof caseItem.estimated_duration_minutes === 'number' ? String(caseItem.estimated_duration_minutes) : '',
    is_automated: caseItem.is_automated === true,
    automation_status: text(caseItem.automation_status),
    change_summary: '',
  }
}

// ── Normalisation (what a Save would send for each field) ───────────────────

function normText(value: string | null | undefined): string | null {
  const trimmed = text(value).trim()
  return trimmed ? trimmed : null
}

function normSteps(steps: Array<{ action?: string | null; expected_result?: string | null }>): TestStep[] {
  return steps
    .filter((step) => text(step.action).trim())
    .map((step, index) => ({
      step_number: index + 1,
      action: text(step.action).trim(),
      expected_result: text(step.expected_result).trim(),
    }))
}

function normParameterRow(row: { name?: string | null; value?: string | null; masked?: boolean; mode?: string }): TestCaseParameter | null {
  const name = text(row.name).trim()
  if (!name) return null
  const masked = isMaskedParameter(row)
  const raw = text(row.value)
  return {
    name,
    // A masked row left empty is sent as null: the server keeps the stored value.
    value: masked ? (raw === '' ? null : raw) : raw,
    masked,
    ...(row.mode ? { mode: row.mode } : {}),
  }
}

function normParameters(rows: Array<{ name?: string | null; value?: string | null; masked?: boolean; mode?: string }>): TestCaseParameter[] {
  return rows.map(normParameterRow).filter((row): row is TestCaseParameter => row !== null)
}

/** The case's rows as the form saw them: a masked value was never prefilled, whatever the answer carried. */
function asOpened(rows: TestCaseParameter[]): TestCaseParameter[] {
  return rows
    .filter((row) => Boolean(row) && typeof row === 'object')
    .map((row) => (isMaskedParameter(row) ? { ...row, value: '' } : row))
}

function tagList(parts: Iterable<string>): string[] {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const part of parts) {
    const tag = typeof part === 'string' ? part.trim() : ''
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(tag)
  }
  return tags
}

/** Tags as typed: trimmed, empties dropped, de-duplicated (first spelling kept, order kept). */
export function parseTags(value: string): string[] {
  return tagList(value.split(','))
}

/** Rounded, at least 1; empty (or not a number) = none. */
export function parseDuration(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const number = Number(trimmed)
  if (!Number.isFinite(number)) return null
  return Math.max(1, Math.round(number))
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Only the fields whose value differs from `base` (the case as opened). An
 * unchanged severity outside the vocabulary is not sent (§0.23): it is
 * compared as written.
 */
export function payloadFrom(base: ManagedTestCase, form: CaseForm): CaseChanges {
  const changes: CaseChanges = {}

  const title = form.title.trim()
  if (title !== text(base.title).trim()) changes.title = title

  for (const field of ['objective', 'description', 'preconditions', 'expected_result', 'test_data', 'feature_area', 'suite_name'] as const) {
    const mine = normText(form[field])
    if (mine !== normText(base[field])) changes[field] = mine
  }

  const steps = normSteps(form.steps)
  if (!same(steps, normSteps(base.steps ?? []))) changes.steps = steps

  const parameters = normParameters(form.parameters)
  if (!same(parameters, normParameters(asOpened(base.parameters ?? [])))) changes.parameters = parameters

  for (const field of ['test_type', 'priority', 'severity', 'automation_status'] as const) {
    if (form[field] !== text(base[field])) changes[field] = form[field]
  }

  const tags = parseTags(form.tags)
  if (!same(tags, tagList(base.tags ?? []))) changes.tags = tags

  const assignee = form.assignee_id || null
  if (assignee !== (base.assignee_id || null)) changes.assignee_id = assignee

  const duration = parseDuration(form.estimated_duration_minutes)
  const baseDuration = typeof base.estimated_duration_minutes === 'number' ? base.estimated_duration_minutes : null
  if (duration !== baseDuration) changes.estimated_duration_minutes = duration

  if (form.is_automated !== (base.is_automated === true)) changes.is_automated = form.is_automated

  return changes
}

/** The fields the reader changed in `form` against `base`, in the form's order. */
export function touchedFields(base: ManagedTestCase, form: CaseForm): CaseField[] {
  const changes = payloadFrom(base, form)
  return CASE_FIELDS.filter((field) => field in changes)
}

/** What changed between the case as opened and the fresh one (form fields, then `status`). */
export function differingFields(base: ManagedTestCase, fresh: ManagedTestCase): Array<CaseField | 'status'> {
  const fields: Array<CaseField | 'status'> = touchedFields(base, formFromCase(fresh))
  if (base.status !== fresh.status) fields.push('status')
  return fields
}

/**
 * "Keep editing": a field the reader did not touch (against `base`) takes the
 * fresh value; a field the reader changed keeps the reader's value. The change
 * summary is the reader's.
 */
export function rebaseForm(base: ManagedTestCase, mine: CaseForm, theirs: ManagedTestCase): CaseForm {
  const touched = new Set<CaseField>(touchedFields(base, mine))
  const fresh = formFromCase(theirs)
  const next: CaseForm = { ...fresh, change_summary: mine.change_summary }
  const assign = <K extends keyof CaseForm>(field: K) => {
    next[field] = mine[field]
  }
  for (const field of CASE_FIELDS) {
    if (touched.has(field)) assign(field as keyof CaseForm)
  }
  return next
}

/** "Priya Shah", "Priya Shah and 1 other", "Priya Shah and 2 others", or "someone else". */
export function changedByNames(names: Array<string | null | undefined>): string {
  const distinct: string[] = []
  for (const name of names) {
    const value = typeof name === 'string' ? name.trim() : ''
    if (value && !distinct.includes(value)) distinct.push(value)
  }
  if (distinct.length === 0) return 'someone else'
  if (distinct.length === 1) return distinct[0]
  const others = distinct.length - 1
  return `${distinct[0]} and ${others} other${others === 1 ? '' : 's'}`
}

export function fieldLabels(fields: ReadonlyArray<CaseField | 'status'>): string {
  return fields.map((field) => FIELD_LABELS[field]).join(', ')
}

// ── Step operations (steps are numbered by position) ────────────────────────

export function addStep(steps: StepForm[]): StepForm[] {
  return [...steps, { key: key(), action: '', expected_result: '' }]
}

export function removeStep(steps: StepForm[], index: number): StepForm[] {
  return steps.filter((_, i) => i !== index)
}

export function moveStep(steps: StepForm[], index: number, direction: -1 | 1): StepForm[] {
  const target = index + direction
  if (index < 0 || index >= steps.length || target < 0 || target >= steps.length) return steps
  const next = [...steps]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  return next
}

export function updateStep(steps: StepForm[], index: number, field: 'action' | 'expected_result', value: string): StepForm[] {
  return steps.map((step, i) => (i === index ? { ...step, [field]: value } : step))
}

// ── Parameter operations ────────────────────────────────────────────────────

export function addParameter(parameters: ParameterForm[]): ParameterForm[] {
  return [...parameters, { key: key(), name: '', value: '', masked: false }]
}

export function removeParameter(parameters: ParameterForm[], index: number): ParameterForm[] {
  return parameters.filter((_, i) => i !== index)
}

export function updateParameter(
  parameters: ParameterForm[],
  index: number,
  change: Partial<Pick<ParameterForm, 'name' | 'value' | 'masked'>>,
): ParameterForm[] {
  return parameters.map((row, i) => {
    if (i !== index) return row
    const next: ParameterForm = { ...row, ...change }
    // Unmasking a row the mode masked: drop the mode, or the server would keep masking it.
    if (change.masked === false && next.mode && MASKED_MODES.has(next.mode)) delete next.mode
    return next
  })
}

/** The Severity options: the vocabulary, plus the case's current value when outside it (shown as written). */
export function severityOptions(current: string): string[] {
  const options: string[] = [...SEVERITIES]
  if (current && !options.includes(current)) options.push(current)
  return options
}
