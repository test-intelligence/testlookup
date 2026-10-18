/**
 * W28 TCM-03: the bulk bar's pure model. The retire moves stay QA_LEAD or
 * above (the contract's AMENDMENT): pinned per role below.
 */
import { describe, expect, it } from 'vitest'
import type { ManagedTestCase, TestCaseBulkResponse } from '@/types/test-management'
import {
  BULK_TRANSITIONS,
  UNASSIGNED,
  applyBlocker,
  bulkActionOptions,
  bulkRequestFor,
  bulkTransitionOptions,
  canRetireRole,
  isSelectable,
  needsReason,
  parseTags,
  pruneSelection,
  skippedLines,
  summarize,
} from './bulkActionModel'

function tc(id: string, over: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id,
    project_id: 'p-1',
    title: `Case ${id}`,
    test_type: 'functional',
    priority: 'medium',
    severity: 'major',
    test_suite_id: null,
    status: 'draft',
    version: 1,
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  }
}

function response(results: TestCaseBulkResponse['results'], over: Partial<TestCaseBulkResponse> = {}): TestCaseBulkResponse {
  const count = (outcome: string) => results.filter((r) => r.outcome === outcome).length
  return {
    action: 'transition',
    requested: results.length,
    changed: count('changed'),
    unchanged: count('unchanged'),
    skipped: count('skipped'),
    not_found: count('not_found'),
    results,
    ...over,
  }
}

const A = tc('aaaaaaaa-0000-4000-8000-000000000001', { title: 'Login works', version: 3 })
const B = tc('bbbbbbbb-0000-4000-8000-000000000002', { title: 'Refund flow', version: 1, status: 'approved' })
const C = tc('cccccccc-0000-4000-8000-000000000003', { title: 'Old checkout', version: 7, status: 'deprecated' })

describe('canRetireRole and the Change status options, per role (the AMENDMENT: QA_LEAD or above)', () => {
  const ALL = ['Make active', 'Back to draft', 'Needs update', 'Request review', 'Deprecate', 'Archive', 'Reinstate']
  const NO_RETIRE = ['Make active', 'Back to draft', 'Needs update', 'Request review']
  it.each([
    ['VIEWER', false, NO_RETIRE],
    ['TESTER', false, NO_RETIRE],
    ['QA_ENGINEER', false, NO_RETIRE],
    ['QA_LEAD', true, ALL],
    ['ADMIN', true, ALL],
    ['UserRole.QA_LEAD', true, ALL],
    ['UserRole.QA_ENGINEER', false, NO_RETIRE],
    [null, false, NO_RETIRE],
    ['SUPERUSER', false, NO_RETIRE],
  ])('%s → canRetire %s', (role, can, labels) => {
    expect(canRetireRole(role)).toBe(can)
    expect(bulkTransitionOptions(canRetireRole(role)).map((o) => o.label)).toEqual(labels)
  })

  it('offers the endpoint\'s seven moves in its order, labelled by outcome without the ellipsis', () => {
    expect(BULK_TRANSITIONS).toEqual(['activate', 'revise', 'flag_stale', 'request_review', 'deprecate', 'archive', 'reinstate'])
    expect(bulkTransitionOptions(true)).toEqual([
      { value: 'activate', label: 'Make active' },
      { value: 'revise', label: 'Back to draft' },
      { value: 'flag_stale', label: 'Needs update' },
      { value: 'request_review', label: 'Request review' },
      { value: 'deprecate', label: 'Deprecate' },
      { value: 'archive', label: 'Archive' },
      { value: 'reinstate', label: 'Reinstate' },
    ])
    expect(bulkTransitionOptions(false).map((o) => o.value)).toEqual(['activate', 'revise', 'flag_stale', 'request_review'])
  })
})

describe('bulkActionOptions', () => {
  it('Change status only with lifecycle v2', () => {
    expect(bulkActionOptions(true).map((o) => o.label)).toEqual(['Change status', 'Owner', 'Priority', 'Add tags', 'Remove tags', 'Move to suite'])
    expect(bulkActionOptions(false).map((o) => o.value)).toEqual(['set_owner', 'set_priority', 'add_tags', 'remove_tags', 'move_to_suite'])
  })
})

describe('isSelectable', () => {
  it.each([
    [tc('1', { source: 'managed' }), true, true],
    [tc('2'), true, true],
    [tc('3', { source: 'automation' }), true, false],
    [tc('4', { source: 'managed' }), false, false],
    [tc('5', { source: 'automation' }), false, false],
  ])('%#: authored and editable only', (row, canEdit, expected) => {
    expect(isSelectable(row, canEdit)).toBe(expected)
  })
})

describe('parseTags', () => {
  it('trims, drops empties and de-duplicates in order', () => {
    expect(parseTags(' smoke, checkout ,,smoke, regression , ')).toEqual(['smoke', 'checkout', 'regression'])
    expect(parseTags('')).toEqual([])
    expect(parseTags(undefined)).toEqual([])
  })
})

describe('applyBlocker (Apply disabled until the field is set; at 0 selected, always)', () => {
  it.each([
    [{ action: 'transition', transition: 'activate' } as const, 0, 'Select cases with the checkboxes to edit them together.'],
    [{ action: '' } as const, 2, 'Choose an action.'],
    [{ action: 'transition' } as const, 2, 'Choose a status change.'],
    [{ action: 'transition', transition: 'activate' } as const, 2, null],
    [{ action: 'transition', transition: 'deprecate' } as const, 2, 'A reason is required for this change.'],
    [{ action: 'transition', transition: 'deprecate', reason: '   ' } as const, 2, 'A reason is required for this change.'],
    [{ action: 'transition', transition: 'deprecate', reason: 'Replaced by the v2 flow' } as const, 2, null],
    [{ action: 'transition', transition: 'flag_stale', reason: '' } as const, 1, 'A reason is required for this change.'],
    [{ action: 'set_owner' } as const, 1, 'Choose an owner.'],
    [{ action: 'set_owner', owner: UNASSIGNED } as const, 1, null],
    [{ action: 'set_owner', owner: 'u-1' } as const, 1, null],
    [{ action: 'set_priority', priority: '' } as const, 1, 'Choose a priority.'],
    [{ action: 'set_priority', priority: 'urgent' } as const, 1, 'Choose a priority.'],
    [{ action: 'set_priority', priority: 'high' } as const, 1, null],
    [{ action: 'add_tags', tags: ' , ' } as const, 1, 'Name at least one tag'],
    [{ action: 'remove_tags', tags: 'x'.repeat(101) } as const, 1, 'A tag is at most 100 characters'],
    [{ action: 'add_tags', tags: Array.from({ length: 21 }, (_, i) => `t${i}`).join(',') } as const, 1, 'Name at most 20 tags'],
    [{ action: 'add_tags', tags: 'regression' } as const, 1, null],
    [{ action: 'move_to_suite', suiteName: '  ' } as const, 1, 'Name the suite to move to'],
    [{ action: 'move_to_suite', suiteName: 'Checkout' } as const, 1, null],
  ])('%# %j with %i selected → %s', (choice, count, expected) => {
    expect(applyBlocker(choice, count)).toBe(expected)
  })

  it('a reason is needed only for the four reason-required moves', () => {
    expect(['activate', 'revise', 'flag_stale', 'request_review', 'deprecate', 'archive', 'reinstate']
      .filter((t) => needsReason({ action: 'transition', transition: t as never }))).toEqual(['flag_stale', 'deprecate', 'archive', 'reinstate'])
    expect(needsReason({ action: 'set_owner', transition: 'deprecate' })).toBe(false)
  })
})

describe('bulkRequestFor (exact bodies, expected_versions keyed by id)', () => {
  const base = {
    project_id: 'p-1',
    case_ids: [A.id, B.id, C.id],
    expected_versions: { [A.id]: 3, [B.id]: 1, [C.id]: 7 },
  }

  it('transition without a reason', () => {
    expect(bulkRequestFor('p-1', [A, B, C], { action: 'transition', transition: 'activate', reason: 'ignored' }))
      .toEqual({ ...base, action: 'transition', transition: 'activate' })
  })

  it('a retire transition with its reason, trimmed', () => {
    expect(bulkRequestFor('p-1', [A, B, C], { action: 'transition', transition: 'deprecate', reason: '  Replaced by v2  ' }))
      .toEqual({ ...base, action: 'transition', transition: 'deprecate', reason: 'Replaced by v2' })
  })

  it('owner: a member id, or Unassigned as an explicit null', () => {
    expect(bulkRequestFor('p-1', [A], { action: 'set_owner', owner: 'u-7' }))
      .toEqual({ project_id: 'p-1', case_ids: [A.id], expected_versions: { [A.id]: 3 }, action: 'set_owner', assignee_id: 'u-7' })
    const unassigned = bulkRequestFor('p-1', [A], { action: 'set_owner', owner: UNASSIGNED })
    expect(unassigned).toEqual({ project_id: 'p-1', case_ids: [A.id], expected_versions: { [A.id]: 3 }, action: 'set_owner', assignee_id: null })
    expect('assignee_id' in unassigned).toBe(true)
  })

  it('priority', () => {
    expect(bulkRequestFor('p-1', [B], { action: 'set_priority', priority: 'high' }))
      .toEqual({ project_id: 'p-1', case_ids: [B.id], expected_versions: { [B.id]: 1 }, action: 'set_priority', priority: 'high' })
  })

  it('add / remove tags, parsed', () => {
    expect(bulkRequestFor('p-1', [A, B], { action: 'add_tags', tags: 'regression, smoke,regression' })).toEqual({
      project_id: 'p-1', case_ids: [A.id, B.id], expected_versions: { [A.id]: 3, [B.id]: 1 }, action: 'add_tags', tags: ['regression', 'smoke'],
    })
    expect(bulkRequestFor('p-1', [A], { action: 'remove_tags', tags: ' flaky ' })).toEqual({
      project_id: 'p-1', case_ids: [A.id], expected_versions: { [A.id]: 3 }, action: 'remove_tags', tags: ['flaky'],
    })
  })

  it('move to suite, trimmed', () => {
    expect(bulkRequestFor('p-1', [C], { action: 'move_to_suite', suiteName: '  Checkout ' })).toEqual({
      project_id: 'p-1', case_ids: [C.id], expected_versions: { [C.id]: 7 }, action: 'move_to_suite', suite_name: 'Checkout',
    })
  })

  it('a row listed twice is sent once, in list order', () => {
    const body = bulkRequestFor('p-1', [B, A, B], { action: 'set_priority', priority: 'low' })
    expect(body.case_ids).toEqual([B.id, A.id])
    expect(body.expected_versions).toEqual({ [B.id]: 1, [A.id]: 3 })
  })

  it('refuses to build a request without an action', () => {
    expect(() => bulkRequestFor('p-1', [A], { action: '' })).toThrow()
  })
})

describe('pruneSelection (keeps skipped + not_found ids)', () => {
  it('drops changed and unchanged, keeps the rest in selection order', () => {
    const result = response([
      { id: A.id, outcome: 'changed', version: 4, status: 'active' },
      { id: B.id, outcome: 'skipped', reason: 'Changed since you selected it (v1 → v2)' },
      { id: C.id, outcome: 'not_found' },
      { id: 'd', outcome: 'unchanged', version: 2 },
    ])
    expect(pruneSelection([C.id, 'd', B.id, A.id], result)).toEqual([C.id, B.id])
    expect(pruneSelection([A.id, 'd'], result)).toEqual([])
  })
})

describe('summarize', () => {
  it.each([
    [{ changed: 12, skipped: 3, unchanged: 1, not_found: 0 }, '12 changed · 3 skipped · 1 unchanged'],
    [{ changed: 3, skipped: 0, unchanged: 0, not_found: 0 }, '3 changed'],
    [{ changed: 2, skipped: 1, unchanged: 0, not_found: 0 }, '2 changed · 1 skipped'],
    [{ changed: 0, skipped: 1, unchanged: 0, not_found: 2 }, '1 skipped · 2 not found'],
    [{ changed: 0, skipped: 0, unchanged: 4, not_found: 0 }, '4 unchanged'],
    [{ changed: 0, skipped: 0, unchanged: 0, not_found: 0 }, 'Nothing changed'],
  ])('%j → %s', (counts, text) => {
    expect(summarize(response([], counts))).toBe(text)
  })
})

describe('skippedLines', () => {
  it('a skipped case by the title the reader saw with its reason; a not-found id by its first 8 characters', () => {
    const result = response([
      { id: A.id, outcome: 'changed' },
      { id: B.id, outcome: 'skipped', reason: 'Not available from deprecated' },
      { id: C.id, outcome: 'not_found' },
      { id: 'eeeeeeee-0000-4000-8000-000000000009', outcome: 'skipped', reason: null },
    ])
    expect(skippedLines(result, [A, B, C])).toEqual([
      { id: B.id, label: 'Refund flow', reason: 'Not available from deprecated' },
      { id: C.id, label: 'cccccccc', reason: 'no longer in this project' },
      { id: 'eeeeeeee-0000-4000-8000-000000000009', label: 'eeeeeeee', reason: 'This case could not be changed' },
    ])
  })
})
