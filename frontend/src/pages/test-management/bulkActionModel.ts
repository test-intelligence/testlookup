/**
 * The bulk bar's pure model (W28 TCM-03): which rows can be selected, the one
 * request an Apply sends, what to keep selected afterwards, and the sentence
 * the bar shows. No React, no request.
 *
 * The retire moves (Deprecate / Archive / Reinstate) stay QA_LEAD or above
 * (W28 contract AMENDMENT, 2026-10-17: OR-04 as corrected; the bulk endpoint
 * refuses them below QA_LEAD for the whole request). `canRetireRole` is the
 * one place the bar's rule lives; the bar shows them only when the host says
 * `canRetire`.
 */
import type {
  ManagedTestCase,
  TestCaseBulkAction,
  TestCaseBulkRequest,
  TestCaseBulkResponse,
  TestCaseTransitionAction,
} from '@/types/test-management'
import { STATUS_MOVE_LABELS, moveTitle } from './caseStatusModel'

/** The status changes the bulk endpoint accepts (mirrors the backend's BULK_TRANSITIONS), in the select's order. */
export const BULK_TRANSITIONS = [
  'activate',
  'revise',
  'flag_stale',
  'request_review',
  'deprecate',
  'archive',
  'reinstate',
] as const satisfies readonly TestCaseTransitionAction[]

export type BulkTransition = (typeof BULK_TRANSITIONS)[number]

/** QA_LEAD or above only (the AMENDMENT). */
export const BULK_RETIRE_TRANSITIONS: ReadonlySet<BulkTransition> = new Set<BulkTransition>(['deprecate', 'archive', 'reinstate'])

/** A reason is required for these (the lifecycle's REASON_REQUIRED_ACTIONS). */
export const BULK_REASON_TRANSITIONS: ReadonlySet<BulkTransition> = new Set<BulkTransition>([
  'flag_stale',
  'deprecate',
  'archive',
  'reinstate',
])

/** The four priorities the endpoint accepts (its PRIORITIES). */
export const BULK_PRIORITIES = ['critical', 'high', 'medium', 'low'] as const

/** The Owner select's "Unassigned" (never a user id: ids are UUIDs). */
export const UNASSIGNED = 'unassigned'

/** The endpoint's limits (each tag 1..100 characters, at most 20 per request). */
export const MAX_TAG_LENGTH = 100
export const MAX_TAGS = 20

/** The Action select, in order; `transition` only with lifecycle v2. */
export const BULK_ACTION_LABELS: Record<TestCaseBulkAction, string> = {
  transition: 'Change status',
  set_owner: 'Owner',
  set_priority: 'Priority',
  add_tags: 'Add tags',
  remove_tags: 'Remove tags',
  move_to_suite: 'Move to suite',
}

const ACTION_ORDER: TestCaseBulkAction[] = ['transition', 'set_owner', 'set_priority', 'add_tags', 'remove_tags', 'move_to_suite']

/** What the reader chose in the bar. Every field is the control's raw value ('' = not chosen). */
export interface BulkChoice {
  action: TestCaseBulkAction | ''
  /** Change status: one of BULK_TRANSITIONS. */
  transition?: BulkTransition | ''
  /** Change status: required for BULK_REASON_TRANSITIONS. */
  reason?: string
  /** Owner: a user id, or UNASSIGNED. */
  owner?: string
  priority?: string
  /** Add / Remove tags: comma-separated. */
  tags?: string
  /** Move to suite. */
  suiteName?: string
}

const ROLE_ORDER = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN'] as const

function normalizedRole(role: string | null | undefined): (typeof ROLE_ORDER)[number] {
  const raw = String(role ?? 'VIEWER').trim()
  const name = raw.startsWith('UserRole.') ? raw.slice('UserRole.'.length) : raw
  return (ROLE_ORDER as readonly string[]).includes(name) ? (name as (typeof ROLE_ORDER)[number]) : 'VIEWER'
}

/** Whether this role may Deprecate / Archive / Reinstate: QA leads and administrators only (the AMENDMENT). */
export function canRetireRole(role: string | null | undefined): boolean {
  return ROLE_ORDER.indexOf(normalizedRole(role)) >= ROLE_ORDER.indexOf('QA_LEAD')
}

/** The Action select's options. */
export function bulkActionOptions(lifecycleV2: boolean): Array<{ value: TestCaseBulkAction; label: string }> {
  return ACTION_ORDER
    .filter((action) => action !== 'transition' || lifecycleV2)
    .map((action) => ({ value: action, label: BULK_ACTION_LABELS[action] }))
}

/** The Change status select's options: the bulk moves labelled by outcome, the retire ones only when canRetire. */
export function bulkTransitionOptions(canRetire: boolean): Array<{ value: BulkTransition; label: string }> {
  return BULK_TRANSITIONS
    .filter((transition) => canRetire || !BULK_RETIRE_TRANSITIONS.has(transition))
    .map((transition) => ({ value: transition, label: moveTitle(STATUS_MOVE_LABELS[transition]) }))
}

/** A row can be selected when the viewer can edit and the case is authored (an automation row has no catalog record). */
export function isSelectable(tc: ManagedTestCase, canEdit: boolean): boolean {
  return canEdit && tc.source !== 'automation'
}

/** Comma-separated tags: trimmed, empties dropped, de-duplicated (first spelling kept), in order. */
export function parseTags(text: string | undefined): string[] {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const part of (text ?? '').split(',')) {
    const tag = part.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(tag)
  }
  return tags
}

/** Whether the chosen status change needs a reason. */
export function needsReason(choice: BulkChoice): boolean {
  return choice.action === 'transition' && !!choice.transition && BULK_REASON_TRANSITIONS.has(choice.transition)
}

/**
 * Why Apply is not available yet, as one sentence, or null when it is. Apply
 * is never available at 0 selected.
 */
export function applyBlocker(choice: BulkChoice, selectedCount: number): string | null {
  if (selectedCount <= 0) return 'Select cases with the checkboxes to edit them together.'
  switch (choice.action) {
    case '':
      return 'Choose an action.'
    case 'transition':
      if (!choice.transition) return 'Choose a status change.'
      if (needsReason(choice) && !(choice.reason ?? '').trim()) return 'A reason is required for this change.'
      return null
    case 'set_owner':
      return choice.owner ? null : 'Choose an owner.'
    case 'set_priority':
      return choice.priority && (BULK_PRIORITIES as readonly string[]).includes(choice.priority) ? null : 'Choose a priority.'
    case 'add_tags':
    case 'remove_tags': {
      const tags = parseTags(choice.tags)
      if (tags.length === 0) return 'Name at least one tag'
      if (tags.some((tag) => tag.length > MAX_TAG_LENGTH)) return 'A tag is at most 100 characters'
      if (tags.length > MAX_TAGS) return 'Name at most 20 tags'
      return null
    }
    case 'move_to_suite':
      return (choice.suiteName ?? '').trim() ? null : 'Name the suite to move to'
    default:
      return 'Choose an action.'
  }
}

/**
 * The one request an Apply sends: the selected ids (list order, de-duplicated),
 * each one's version as the reader saw it (a case changed since is skipped by
 * the server, never overwritten), the action and only its own field.
 */
export function bulkRequestFor(projectId: string, selected: ManagedTestCase[], choice: BulkChoice): TestCaseBulkRequest {
  const caseIds: string[] = []
  const expectedVersions: Record<string, number> = {}
  for (const tc of selected) {
    if (expectedVersions[tc.id] !== undefined) continue
    caseIds.push(tc.id)
    expectedVersions[tc.id] = tc.version
  }
  const base = { project_id: projectId, case_ids: caseIds, expected_versions: expectedVersions }
  switch (choice.action) {
    case 'transition': {
      const transition = choice.transition as BulkTransition
      return needsReason(choice)
        ? { ...base, action: 'transition', transition, reason: (choice.reason ?? '').trim() }
        : { ...base, action: 'transition', transition }
    }
    case 'set_owner':
      return { ...base, action: 'set_owner', assignee_id: !choice.owner || choice.owner === UNASSIGNED ? null : choice.owner }
    case 'set_priority':
      return { ...base, action: 'set_priority', priority: choice.priority ?? '' }
    case 'add_tags':
    case 'remove_tags':
      return { ...base, action: choice.action, tags: parseTags(choice.tags) }
    case 'move_to_suite':
      return { ...base, action: 'move_to_suite', suite_name: (choice.suiteName ?? '').trim() }
    default:
      throw new Error('bulkRequestFor: no action chosen')
  }
}

/** After an Apply: keep selected only what the server skipped or could not find (in selection order). */
export function pruneSelection(selected: string[], result: TestCaseBulkResponse): string[] {
  const keep = new Set(
    result.results.filter((r) => r.outcome === 'skipped' || r.outcome === 'not_found').map((r) => r.id),
  )
  return selected.filter((id) => keep.has(id))
}

/** "12 changed · 3 skipped · 1 unchanged" (zero parts left out); "Nothing changed" when every count is zero. */
export function summarize(result: TestCaseBulkResponse): string {
  const parts: string[] = []
  if (result.changed > 0) parts.push(`${result.changed} changed`)
  if (result.skipped > 0) parts.push(`${result.skipped} skipped`)
  if (result.unchanged > 0) parts.push(`${result.unchanged} unchanged`)
  if (result.not_found > 0) parts.push(`${result.not_found} not found`)
  return parts.length ? parts.join(' · ') : 'Nothing changed'
}

export interface SkippedLine {
  id: string
  /** A skipped case's title as the reader selected it (else its id's first 8 characters); a not-found id's first 8. */
  label: string
  reason: string
}

/**
 * The bar's Skipped list, in result order: a skipped case by the title the
 * reader saw, with the server's one sentence; a not-found id by its first 8
 * characters, `no longer in this project` (§2.3).
 */
export function skippedLines(result: TestCaseBulkResponse, selected: ManagedTestCase[]): SkippedLine[] {
  const titles = new Map(selected.map((tc) => [tc.id, tc.title]))
  const lines: SkippedLine[] = []
  for (const r of result.results) {
    if (r.outcome === 'not_found') {
      lines.push({ id: r.id, label: r.id.slice(0, 8), reason: 'no longer in this project' })
    } else if (r.outcome === 'skipped') {
      lines.push({
        id: r.id,
        label: titles.get(r.id)?.trim() || r.id.slice(0, 8),
        reason: (r.reason ?? '').trim() || 'This case could not be changed',
      })
    }
  }
  return lines
}
