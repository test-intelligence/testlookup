/**
 * W28 TCM-04: what changed between two versions. Case tables: every field,
 * steps by position, masked parameters (never their values), "—" for empty.
 */
import { describe, expect, it } from 'vitest'
import type { ManagedTestCase, TestCaseVersion } from '@/types/test-management'
import {
  EMPTY,
  HISTORY_FIELDS,
  changedFieldLabels,
  diffParameters,
  diffSteps,
  diffVersions,
  hasMaskedParameter,
  isMaskedParameter,
} from './caseHistoryModel'

function version(n: number, over: Partial<TestCaseVersion> = {}): TestCaseVersion {
  return {
    id: `v-${n}`,
    test_case_id: 'tc-1',
    version: n,
    title: 'Login works',
    description: 'Signs in',
    objective: '',
    preconditions: 'A user exists',
    steps: [{ step_number: 1, action: 'Open /login', expected_result: 'The form shows' }],
    parameters: [],
    expected_result: 'Signed in',
    test_data: '',
    test_type: 'functional',
    priority: 'medium',
    severity: 'major',
    feature_area: 'Auth',
    suite_name: 'Smoke',
    tags: ['smoke'],
    estimated_duration_minutes: 5,
    is_automated: false,
    automation_status: 'not_automated',
    status: 'draft',
    change_type: 'updated',
    created_at: '2026-10-10T10:00:00Z',
    source: 'person',
    assignee_id: null,
    ...over,
  }
}

function current(over: Partial<ManagedTestCase> = {}): ManagedTestCase {
  const { id: _id, test_case_id: _tc, change_type: _ct, source: _s, changed_fields: _cf, ...rest } = version(3)
  return {
    ...rest,
    id: 'tc-1',
    project_id: 'p-1',
    test_type: 'functional',
    priority: 'medium',
    severity: 'major',
    test_suite_id: null,
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    updated_at: '2026-10-11T10:00:00Z',
    source: 'managed',
    assignee_id: undefined,
    ...over,
  } as ManagedTestCase
}

describe('HISTORY_FIELDS', () => {
  it('lists the restorable fields plus status and owner, labelled', () => {
    expect(HISTORY_FIELDS.map((f) => f.key).sort()).toEqual([
      'assignee_id', 'automation_status', 'description', 'estimated_duration_minutes', 'expected_result', 'feature_area',
      'is_automated', 'objective', 'parameters', 'preconditions', 'priority', 'severity', 'status', 'steps', 'suite_name',
      'tags', 'test_data', 'test_type', 'title',
    ])
    expect(HISTORY_FIELDS.find((f) => f.key === 'suite_name')?.label).toBe('Suite')
    expect(HISTORY_FIELDS.find((f) => f.key === 'assignee_id')?.label).toBe('Owner')
  })
})

describe('diffVersions — every plain field', () => {
  it.each([
    ['title', { title: 'Login works with SSO' }, 'Title', 'Login works', 'Login works with SSO', false],
    ['description', { description: 'Signs in with a password' }, 'Description', 'Signs in', 'Signs in with a password', true],
    ['objective', { objective: 'Cover the happy path' }, 'Objective', EMPTY, 'Cover the happy path', true],
    ['preconditions', { preconditions: '' }, 'Preconditions', 'A user exists', EMPTY, true],
    ['expected_result', { expected_result: 'The dashboard shows' }, 'Expected result', 'Signed in', 'The dashboard shows', true],
    ['test_data', { test_data: 'user=alice' }, 'Test data', EMPTY, 'user=alice', true],
    ['test_type', { test_type: 'regression' }, 'Test type', 'functional', 'regression', false],
    ['priority', { priority: 'high' }, 'Priority', 'medium', 'high', false],
    ['severity', { severity: 'critical' }, 'Severity', 'major', 'critical', false],
    ['feature_area', { feature_area: undefined }, 'Feature area', 'Auth', EMPTY, false],
    ['suite_name', { suite_name: 'Checkout' }, 'Suite', 'Smoke', 'Checkout', false],
    ['tags', { tags: ['smoke', 'regression'] }, 'Tags', 'smoke', 'smoke, regression', false],
    ['tags emptied', { tags: [] }, 'Tags', 'smoke', EMPTY, false],
    ['estimated_duration_minutes', { estimated_duration_minutes: 12 }, 'Estimated duration', '5 min', '12 min', false],
    ['is_automated', { is_automated: true }, 'Automated', 'No', 'Yes', false],
    ['automation_status', { automation_status: 'in_progress' }, 'Automation status', 'not_automated', 'in_progress', false],
    ['status', { status: 'review_requested' }, 'Status', 'Draft', 'Review requested', false],
  ] as const)('%s', (_name, change, label, before, after, long) => {
    const changes = diffVersions(version(1), version(2, change as Partial<TestCaseVersion>))
    expect(changes).toEqual([{ key: expect.any(String), label, before, after, ...(long ? { long: true } : {}) }])
  })

  it('nothing differs → no change; field order follows HISTORY_FIELDS', () => {
    expect(diffVersions(version(1), version(2))).toEqual([])
    const changes = diffVersions(version(1), version(2, { tags: [], title: 'New', priority: 'low', status: 'active' }))
    expect(changes.map((c) => c.label)).toEqual(['Title', 'Status', 'Priority', 'Tags'])
  })

  it('a long value of a short field is stacked too', () => {
    const [change] = diffVersions(version(1), version(2, { title: 'x'.repeat(61) }))
    expect(change.long).toBe(true)
  })

  it('a missing value prints as —', () => {
    const [change] = diffVersions(version(1, { feature_area: undefined }), version(2, { feature_area: 'Auth' }))
    expect(change).toMatchObject({ label: 'Feature area', before: '—', after: 'Auth' })
  })
})

describe('diffVersions — owner', () => {
  const nameOf = (id: string) => ({ 'u-priya': 'Priya Shah' } as Record<string, string>)[id]

  it('names a known owner, says Unassigned for none, and an unknown id by its first 8 characters', () => {
    expect(diffVersions(version(1), version(2, { assignee_id: 'u-priya' }), { nameOf })).toEqual([
      { key: 'assignee_id', label: 'Owner', before: 'Unassigned', after: 'Priya Shah' },
    ])
    expect(diffVersions(version(1, { assignee_id: 'u-priya' }), version(2, { assignee_id: 'abcdef12-0000-4000' }), { nameOf }))
      .toEqual([{ key: 'assignee_id', label: 'Owner', before: 'Priya Shah', after: 'user abcdef12' }])
  })

  it('a version written before 0222 (no source) does not record its owner: no owner row', () => {
    expect(diffVersions(version(1, { source: null, assignee_id: null }), version(2, { assignee_id: 'u-priya' }))).toEqual([])
    expect(diffVersions(version(1, { source: null }), current({ assignee_id: 'u-priya' }))).toEqual([])
  })

  it('against the current case (which always records its owner)', () => {
    expect(diffVersions(version(1, { assignee_id: 'u-priya' }), current({ assignee_id: undefined }), { nameOf }))
      .toEqual([{ key: 'assignee_id', label: 'Owner', before: 'Priya Shah', after: 'Unassigned' }])
  })
})

describe('diffSteps — by position', () => {
  const s = (n: number, action: string, expected_result?: string) => ({ step_number: n, action, expected_result })

  it.each([
    ['unchanged', [s(1, 'Open')], [s(1, 'Open')], []],
    ['renumbered only', [s(1, 'Open')], [s(7, 'Open')], []],
    ['added', [s(1, 'Open')], [s(1, 'Open'), s(2, 'Submit')], [{ position: 2, kind: 'added', after: 'Submit' }]],
    ['removed', [s(1, 'Open'), s(2, 'Submit'), s(3, 'Log out')], [s(1, 'Open'), s(2, 'Submit')], [{ position: 3, kind: 'removed', before: 'Log out' }]],
    ['action changed', [s(1, 'Open /login')], [s(1, 'Open /sign-in')], [{ position: 1, kind: 'changed', part: 'action', before: 'Open /login', after: 'Open /sign-in' }]],
    ['expected changed', [s(1, 'Open', 'Form')], [s(1, 'Open', 'The form')], [{ position: 1, kind: 'changed', part: 'expected', before: 'Form', after: 'The form' }]],
    ['expected emptied', [s(1, 'Open', 'Form')], [s(1, 'Open')], [{ position: 1, kind: 'changed', part: 'expected', before: 'Form', after: '—' }]],
    ['both parts', [s(1, 'a', 'b')], [s(1, 'c', 'd')], [
      { position: 1, kind: 'changed', part: 'action', before: 'a', after: 'c' },
      { position: 1, kind: 'changed', part: 'expected', before: 'b', after: 'd' },
    ]],
    ['from none', undefined, [s(1, 'Open')], [{ position: 1, kind: 'added', after: 'Open' }]],
    ['to none', [s(1, 'Open')], undefined, [{ position: 1, kind: 'removed', before: 'Open' }]],
    ['a blank action added', [], [s(1, '')], [{ position: 1, kind: 'added', after: '—' }]],
  ])('%s', (_name, before, after, expected) => {
    expect(diffSteps(before, after)).toEqual(expected)
  })

  it('reaches diffVersions as one Steps change', () => {
    const changes = diffVersions(version(1), version(2, { steps: [s(1, 'Open /login', 'The form shows'), s(2, 'Submit')] }))
    expect(changes).toEqual([{ key: 'steps', label: 'Steps', steps: [{ position: 2, kind: 'added', after: 'Submit' }] }])
  })
})

describe('masked parameters — never their values', () => {
  const plain = (name: string, value: string | null) => ({ name, value })
  const masked = (name: string, value: string | null = null) => ({ name, value, masked: true })

  it.each([
    [{ name: 'a', masked: true }, true],
    [{ name: 'a', mode: 'masked' }, true],
    [{ name: 'a', mode: 'hidden' }, true],
    [{ name: 'a', mode: 'plain' }, false],
    [{ name: 'a' }, false],
    [null, false],
  ])('isMaskedParameter(%j) → %s', (p, expected) => {
    expect(isMaskedParameter(p as never)).toBe(expected)
  })

  it.each([
    ['plain value changed', [plain('env', 'staging')], [plain('env', 'prod')], [{ name: 'env', kind: 'changed', before: 'staging', after: 'prod' }]],
    ['plain added', [], [plain('env', 'prod')], [{ name: 'env', kind: 'added', after: 'prod' }]],
    ['removed', [plain('env', 'prod')], [], [{ name: 'env', kind: 'removed' }]],
    ['masked added', [], [masked('token')], [{ name: 'token', kind: 'masked' }]],
    ['masked removed', [masked('token')], [], [{ name: 'token', kind: 'removed' }]],
    ['plain became masked', [plain('token', 'abc')], [masked('token')], [{ name: 'token', kind: 'masked' }]],
    ['masked became plain', [masked('token')], [plain('token', 'abc')], [{ name: 'token', kind: 'masked' }]],
    ['both masked, both null (cannot tell)', [masked('token')], [masked('token')], []],
    ['both masked, values sent and differ', [masked('token', 'one')], [masked('token', 'two')], [{ name: 'token', kind: 'masked' }]],
    ['non-dict rows ignored', [null, 'x'], [plain('env', '1')], [{ name: 'env', kind: 'added', after: '1' }]],
  ])('%s', (_name, before, after, expected) => {
    const changes = diffParameters(before as never, after as never)
    expect(changes).toEqual(expected)
    // A masked change never carries a value.
    for (const c of changes) if (c.kind === 'masked') expect(c).toEqual({ name: c.name, kind: 'masked' })
  })

  it('a version whose changed_fields name parameters, with only masked rows, says a masked value changed', () => {
    const before = version(1, { parameters: [masked('token')] })
    const after = version(2, { parameters: [masked('token')], changed_fields: ['parameters'] })
    expect(diffVersions(before, after)).toEqual([{ key: 'parameters', label: 'Parameters', parameters: [{ name: 'token', kind: 'masked' }] }])
    expect(JSON.stringify(diffVersions(before, after))).not.toMatch(/"(before|after)"/)
    expect(diffVersions(before, version(2, { parameters: [masked('token')] }))).toEqual([])
  })

  it('hasMaskedParameter', () => {
    expect(hasMaskedParameter(version(1, { parameters: [masked('t')] }))).toBe(true)
    expect(hasMaskedParameter(version(1, { parameters: [plain('t', 'x')] }))).toBe(false)
    expect(hasMaskedParameter(current({ parameters: undefined }))).toBe(false)
  })
})

describe('diffVersions against the current case (Compare with current)', () => {
  it('compares a version to the case, ignoring the version-only fields', () => {
    const changes = diffVersions(version(1, { title: 'Old title', status: 'draft' }), current({ title: 'Login works', status: 'active' }))
    expect(changes.map((c) => [c.label, c.before, c.after])).toEqual([
      ['Title', 'Old title', 'Login works'],
      ['Status', 'Draft', 'Active'],
    ])
  })
})

describe('changedFieldLabels', () => {
  it('labels known fields and spaces unknown ones', () => {
    expect(changedFieldLabels(['steps', 'expected_result', 'suite_name', 'custom_thing'])).toEqual(['Steps', 'Expected result', 'Suite', 'custom thing'])
    expect(changedFieldLabels(undefined)).toEqual([])
  })
})
