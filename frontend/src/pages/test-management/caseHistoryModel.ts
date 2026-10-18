/**
 * What changed between two versions of a test case (W28 TCM-04): the History
 * tab's "What changed" rows and its "Compare with current" block. Pure: no
 * React, no request.
 *
 * Steps are compared by position (added / removed / the action or the
 * expected result changed). A masked parameter's value is never printed: the
 * server sends it as null, and any change touching one reads "masked value
 * changed". An empty or missing value prints as "—".
 */
import type { ManagedTestCase, TestCaseParameter, TestCaseVersion, TestStep } from '@/types/test-management'
import { statusLabel } from './caseStatusModel'

export type HistoryFieldKey =
  | 'title' | 'status' | 'assignee_id' | 'description' | 'objective' | 'preconditions' | 'steps' | 'expected_result'
  | 'parameters' | 'test_data' | 'test_type' | 'priority' | 'severity' | 'feature_area' | 'suite_name' | 'tags'
  | 'estimated_duration_minutes' | 'is_automated' | 'automation_status'

/** The restorable fields + status + owner, labelled, in reading order. */
export const HISTORY_FIELDS: Array<{ key: keyof TestCaseVersion & HistoryFieldKey; label: string }> = [
  { key: 'title', label: 'Title' },
  { key: 'status', label: 'Status' },
  { key: 'assignee_id', label: 'Owner' },
  { key: 'description', label: 'Description' },
  { key: 'objective', label: 'Objective' },
  { key: 'preconditions', label: 'Preconditions' },
  { key: 'steps', label: 'Steps' },
  { key: 'expected_result', label: 'Expected result' },
  { key: 'parameters', label: 'Parameters' },
  { key: 'test_data', label: 'Test data' },
  { key: 'test_type', label: 'Test type' },
  { key: 'priority', label: 'Priority' },
  { key: 'severity', label: 'Severity' },
  { key: 'feature_area', label: 'Feature area' },
  { key: 'suite_name', label: 'Suite' },
  { key: 'tags', label: 'Tags' },
  { key: 'estimated_duration_minutes', label: 'Estimated duration' },
  { key: 'is_automated', label: 'Automated' },
  { key: 'automation_status', label: 'Automation status' },
]

const LABELS = new Map<string, string>(HISTORY_FIELDS.map((f) => [f.key, f.label]))

/** Long text: shown stacked, before over after, never side by side. */
const LONG_FIELDS: ReadonlySet<HistoryFieldKey> = new Set<HistoryFieldKey>([
  'description', 'objective', 'preconditions', 'expected_result', 'test_data',
])
const LONG_TEXT = 60

export const EMPTY = '—'

export interface StepChange {
  /** 1-based position in the list. */
  position: number
  kind: 'added' | 'removed' | 'changed'
  /** For `changed`: which part of the step. */
  part?: 'action' | 'expected'
  /** `changed`: both sides; `added`: after (the action); `removed`: before (the action). */
  before?: string
  after?: string
}

export interface ParameterChange {
  name: string
  /** `masked`: a change that touches a masked value; its values are never carried. */
  kind: 'added' | 'removed' | 'changed' | 'masked'
  before?: string
  after?: string
}

export interface FieldChange {
  key: HistoryFieldKey
  label: string
  /** Display text ("—" when empty) for a plain field; absent for steps / parameters. */
  before?: string
  after?: string
  /** Stack before over after. */
  long?: boolean
  steps?: StepChange[]
  parameters?: ParameterChange[]
}

type Side = TestCaseVersion | ManagedTestCase

export interface DiffOptions {
  /** A user id's name for the Owner row (the case's owner, the people who changed it); else "user <first 8>". */
  nameOf?: (userId: string) => string | null | undefined
}

function isVersion(side: Side): side is TestCaseVersion {
  return 'change_type' in side
}

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

function shown(value: string): string {
  return value.trim() ? value : EMPTY
}

/** A parameter whose value the server masks: `masked: true`, or mode `masked` / `hidden`. */
export function isMaskedParameter(p: TestCaseParameter | null | undefined): boolean {
  if (!p) return false
  return p.masked === true || p.mode === 'masked' || p.mode === 'hidden'
}

/** Steps by position: added | removed | changed (action / expected). */
export function diffSteps(before: TestStep[] | undefined, after: TestStep[] | undefined): StepChange[] {
  const a = Array.isArray(before) ? before : []
  const b = Array.isArray(after) ? after : []
  const changes: StepChange[] = []
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const old = a[i]
    const now = b[i]
    const position = i + 1
    if (!old && now) {
      changes.push({ position, kind: 'added', after: shown(text(now.action)) })
    } else if (old && !now) {
      changes.push({ position, kind: 'removed', before: shown(text(old.action)) })
    } else if (old && now) {
      if (text(old.action) !== text(now.action)) {
        changes.push({ position, kind: 'changed', part: 'action', before: shown(text(old.action)), after: shown(text(now.action)) })
      }
      if (text(old.expected_result) !== text(now.expected_result)) {
        changes.push({
          position, kind: 'changed', part: 'expected',
          before: shown(text(old.expected_result)), after: shown(text(now.expected_result)),
        })
      }
    }
  }
  return changes
}

/** Parameters by name (the before side's order, then the new ones); masked values never carried. */
export function diffParameters(
  before: TestCaseParameter[] | null | undefined,
  after: TestCaseParameter[] | null | undefined,
): ParameterChange[] {
  const a = (Array.isArray(before) ? before : []).filter((p) => p && typeof p === 'object')
  const b = (Array.isArray(after) ? after : []).filter((p) => p && typeof p === 'object')
  const byName = (list: TestCaseParameter[]) => new Map(list.map((p) => [text(p.name), p]))
  const oldMap = byName(a)
  const newMap = byName(b)
  const names = [...oldMap.keys(), ...[...newMap.keys()].filter((name) => !oldMap.has(name))]
  const changes: ParameterChange[] = []
  for (const name of names) {
    const old = oldMap.get(name)
    const now = newMap.get(name)
    const masked = isMaskedParameter(old) || isMaskedParameter(now)
    if (!old && now) {
      changes.push(masked ? { name, kind: 'masked' } : { name, kind: 'added', after: shown(text(now.value)) })
    } else if (old && !now) {
      changes.push({ name, kind: 'removed' })
    } else if (old && now) {
      if (isMaskedParameter(old) !== isMaskedParameter(now)) {
        changes.push({ name, kind: 'masked' })
      } else if (masked) {
        // Both masked: the server sends null for both; a value it did send is compared, never shown.
        if (text(old.value) !== text(now.value)) changes.push({ name, kind: 'masked' })
      } else if (text(old.value) !== text(now.value)) {
        changes.push({ name, kind: 'changed', before: shown(text(old.value)), after: shown(text(now.value)) })
      }
    }
  }
  return changes
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

function tagsOf(side: Side): string[] {
  return Array.isArray(side.tags) ? side.tags : []
}

/**
 * Whether this side records its owner. A case always does; a version written
 * before 0222 (no `source`) does not, so its null owner is "not recorded",
 * never "Unassigned".
 */
function recordsOwner(side: Side): boolean {
  return !isVersion(side) || (typeof side.source === 'string' && side.source.length > 0)
}

function ownerText(id: string | null | undefined, options: DiffOptions): string {
  if (!id) return 'Unassigned'
  const name = options.nameOf?.(id)?.trim()
  return name || `user ${id.slice(0, 8)}`
}

function plain(key: HistoryFieldKey, before: string, after: string): FieldChange {
  const long = LONG_FIELDS.has(key) || before.length > LONG_TEXT || after.length > LONG_TEXT
  return { key, label: LABELS.get(key) ?? key, before: shown(before), after: shown(after), ...(long ? { long: true } : {}) }
}

/**
 * Every field that differs from `before` to `after`, in HISTORY_FIELDS order.
 * When `after` is a version whose own changed_fields name `parameters` but no
 * visible parameter differs, and a masked parameter is involved, one
 * `masked` line says so (its value is never read).
 */
export function diffVersions(before: Side, after: Side, options: DiffOptions = {}): FieldChange[] {
  const changes: FieldChange[] = []
  for (const { key } of HISTORY_FIELDS) {
    switch (key) {
      case 'steps': {
        const steps = diffSteps(before.steps, after.steps)
        if (steps.length) changes.push({ key, label: 'Steps', steps })
        break
      }
      case 'parameters': {
        const parameters = diffParameters(before.parameters, after.parameters)
        const anyMasked = [...(before.parameters ?? []), ...(after.parameters ?? [])].filter(isMaskedParameter)
        if (!parameters.length && anyMasked.length && isVersion(after) && (after.changed_fields ?? []).includes('parameters')) {
          parameters.push({ name: text(anyMasked[0].name), kind: 'masked' })
        }
        if (parameters.length) changes.push({ key, label: 'Parameters', parameters })
        break
      }
      case 'tags': {
        if (!sameJson(tagsOf(before), tagsOf(after))) changes.push(plain(key, tagsOf(before).join(', '), tagsOf(after).join(', ')))
        break
      }
      case 'assignee_id': {
        if (!recordsOwner(before) || !recordsOwner(after)) break
        const old = before.assignee_id ?? null
        const now = after.assignee_id ?? null
        if (old !== now) changes.push(plain(key, ownerText(old, options), ownerText(now, options)))
        break
      }
      case 'status': {
        if (text(before.status) !== text(after.status)) {
          changes.push(plain(key, before.status ? statusLabel(before.status) : '', after.status ? statusLabel(after.status) : ''))
        }
        break
      }
      case 'is_automated': {
        const old = before.is_automated
        const now = after.is_automated
        if (old === undefined || now === undefined) break
        if (Boolean(old) !== Boolean(now)) changes.push(plain(key, old ? 'Yes' : 'No', now ? 'Yes' : 'No'))
        break
      }
      case 'estimated_duration_minutes': {
        const old = before.estimated_duration_minutes ?? null
        const now = after.estimated_duration_minutes ?? null
        if (old !== now) changes.push(plain(key, old === null ? '' : `${old} min`, now === null ? '' : `${now} min`))
        break
      }
      default: {
        const old = text((before as unknown as Record<string, unknown>)[key])
        const now = text((after as unknown as Record<string, unknown>)[key])
        if (old !== now) changes.push(plain(key, old, now))
      }
    }
  }
  return changes
}

/** "Steps", "Expected result": a version's changed_fields as labels (the oldest card has no predecessor). */
export function changedFieldLabels(fields: string[] | undefined): string[] {
  return (fields ?? []).map((field) => LABELS.get(field) ?? field.replace(/_/g, ' '))
}

/** Whether a side holds a masked parameter (Compare cannot see masked values). */
export function hasMaskedParameter(side: Side): boolean {
  return (side.parameters ?? []).some(isMaskedParameter)
}
