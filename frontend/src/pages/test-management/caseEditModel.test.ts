import { describe, expect, it } from 'vitest'

import type { ManagedTestCase } from '@/types/test-management'
import {
  addStep,
  changedByNames,
  differingFields,
  fieldLabels,
  formFromCase,
  moveStep,
  parseDuration,
  parseTags,
  payloadFrom,
  rebaseForm,
  removeStep,
  severityOptions,
  touchedFields,
  updateParameter,
  updateStep,
  type CaseForm,
} from './caseEditModel'

function managedCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'case-1',
    project_id: 'p-1',
    title: 'Sign in with SSO',
    description: 'The SSO path',
    objective: 'Prove SSO works',
    preconditions: 'An IdP is configured',
    steps: [
      { step_number: 1, action: 'Open the login page', expected_result: 'The SSO button shows' },
      { step_number: 2, action: 'Click SSO', expected_result: 'The IdP opens' },
    ],
    parameters: [
      { name: 'user', value: 'alice', masked: false },
      { name: 'password', value: null, masked: true },
      { name: 'token', value: null, mode: 'hidden' },
    ],
    expected_result: 'Signed in',
    test_data: 'alice / secret',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    feature_area: 'Auth',
    suite_name: 'Login',
    test_suite_id: null,
    tags: ['smoke', 'sso'],
    status: 'active',
    version: 3,
    assignee_id: 'user-1',
    estimated_duration_minutes: 5,
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function edit(base: ManagedTestCase, change: (form: CaseForm) => CaseForm): CaseForm {
  return change(formFromCase(base))
}

describe('formFromCase', () => {
  it('prefills every editable field', () => {
    const form = formFromCase(managedCase())
    expect({ ...form, steps: form.steps.map(({ key: _key, ...s }) => s), parameters: form.parameters.map(({ key: _key, ...p }) => p) })
      .toEqual({
        title: 'Sign in with SSO',
        objective: 'Prove SSO works',
        description: 'The SSO path',
        preconditions: 'An IdP is configured',
        steps: [
          { action: 'Open the login page', expected_result: 'The SSO button shows' },
          { action: 'Click SSO', expected_result: 'The IdP opens' },
        ],
        expected_result: 'Signed in',
        test_data: 'alice / secret',
        parameters: [
          { name: 'user', value: 'alice', masked: false },
          { name: 'password', value: '', masked: true, storedMasked: true },
          { name: 'token', value: '', masked: true, mode: 'hidden', storedMasked: true },
        ],
        test_type: 'functional',
        priority: 'high',
        severity: 'major',
        feature_area: 'Auth',
        suite_name: 'Login',
        tags: 'smoke, sso',
        assignee_id: 'user-1',
        estimated_duration_minutes: '5',
        is_automated: false,
        automation_status: 'not_automated',
        change_summary: '',
      })
  })

  it('never prefills a masked value, even when an older answer carried it', () => {
    const form = formFromCase(managedCase({ parameters: [{ name: 'password', value: 'hunter2', masked: true }, { name: 'k', value: 'v', mode: 'masked' }] }))
    expect(form.parameters.map((p) => p.value)).toEqual(['', ''])
    expect(JSON.stringify(form)).not.toContain('hunter2')
  })

  it('reads missing fields as empty', () => {
    const form = formFromCase(managedCase({
      description: undefined, objective: undefined, steps: undefined, parameters: undefined, tags: undefined,
      assignee_id: undefined, estimated_duration_minutes: undefined, feature_area: undefined, suite_name: undefined,
    }))
    expect(form).toMatchObject({ description: '', objective: '', steps: [], parameters: [], tags: '', assignee_id: '', estimated_duration_minutes: '', feature_area: '', suite_name: '' })
  })
})

describe('payloadFrom', () => {
  it('is empty when nothing changed (masked rows included)', () => {
    const base = managedCase()
    expect(payloadFrom(base, formFromCase(base))).toEqual({})
  })

  it('sends only the changed fields', () => {
    const base = managedCase()
    const form = edit(base, (f) => ({ ...f, title: '  Sign in with SAML  ', priority: 'critical', feature_area: '' }))
    expect(payloadFrom(base, form)).toEqual({ title: 'Sign in with SAML', priority: 'critical', feature_area: null })
  })

  it('renumbers steps and drops the ones with a blank action', () => {
    const base = managedCase()
    let form = formFromCase(base)
    form = { ...form, steps: addStep(form.steps) }
    form = { ...form, steps: updateStep(form.steps, 2, 'action', '  Approve on the IdP ') }
    form = { ...form, steps: addStep(form.steps) } // left blank
    form = { ...form, steps: moveStep(form.steps, 2, -1) }
    expect(payloadFrom(base, form)).toEqual({
      steps: [
        { step_number: 1, action: 'Open the login page', expected_result: 'The SSO button shows' },
        { step_number: 2, action: 'Approve on the IdP', expected_result: '' },
        { step_number: 3, action: 'Click SSO', expected_result: 'The IdP opens' },
      ],
    })
    const removed = { ...formFromCase(base), steps: removeStep(formFromCase(base).steps, 0) }
    expect(payloadFrom(base, removed)).toEqual({
      steps: [{ step_number: 1, action: 'Click SSO', expected_result: 'The IdP opens' }],
    })
  })

  it('does not count renumbering alone as a change', () => {
    const base = managedCase({ steps: [{ step_number: 4, action: 'Only step', expected_result: 'ok' }] })
    expect(payloadFrom(base, formFromCase(base))).toEqual({})
  })

  it('trims, empties and de-duplicates tags', () => {
    const base = managedCase()
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, tags: ' smoke ,, sso, regression, smoke ' })))).toEqual({
      tags: ['smoke', 'sso', 'regression'],
    })
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, tags: 'smoke,sso' })))).toEqual({})
    expect(parseTags(' a, b ,a,,')).toEqual(['a', 'b'])
  })

  it('rounds the duration, and empties it to null', () => {
    const base = managedCase()
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, estimated_duration_minutes: '7.6' })))).toEqual({ estimated_duration_minutes: 8 })
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, estimated_duration_minutes: '' })))).toEqual({ estimated_duration_minutes: null })
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, estimated_duration_minutes: '5.2' })))).toEqual({})
    expect(parseDuration('0.2')).toBe(1)
    expect(parseDuration('abc')).toBeNull()
  })

  it('sends the owner as an id, and Unassigned as null', () => {
    const base = managedCase()
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, assignee_id: 'user-2' })))).toEqual({ assignee_id: 'user-2' })
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, assignee_id: '' })))).toEqual({ assignee_id: null })
  })

  it('does not send an unchanged severity outside the vocabulary', () => {
    const base = managedCase({ severity: 'medium' })
    expect(payloadFrom(base, formFromCase(base))).toEqual({})
    expect(payloadFrom(base, edit(base, (f) => ({ ...f, severity: 'minor' })))).toEqual({ severity: 'minor' })
    expect(severityOptions('medium')).toEqual(['blocker', 'critical', 'major', 'minor', 'trivial', 'medium'])
    expect(severityOptions('major')).toEqual(['blocker', 'critical', 'major', 'minor', 'trivial'])
  })

  it('sends a masked row left empty as value null (the server keeps it), and a typed one as typed', () => {
    const base = managedCase()
    const renamed = edit(base, (f) => ({ ...f, parameters: updateParameter(f.parameters, 0, { value: 'bob' }) }))
    expect(payloadFrom(base, renamed)).toEqual({
      parameters: [
        { name: 'user', value: 'bob', masked: false },
        { name: 'password', value: null, masked: true },
        { name: 'token', value: null, masked: true, mode: 'hidden' },
      ],
    })
    const typed = edit(base, (f) => ({ ...f, parameters: updateParameter(f.parameters, 1, { value: 'n3w' }) }))
    expect(payloadFrom(base, typed).parameters?.[1]).toEqual({ name: 'password', value: 'n3w', masked: true })
    // Unmasking a mode-masked row drops the mode, or the server would keep masking it.
    const unmasked = edit(base, (f) => ({ ...f, parameters: updateParameter(f.parameters, 2, { masked: false, value: 'x' }) }))
    expect(payloadFrom(base, unmasked).parameters?.[2]).toEqual({ name: 'token', value: 'x', masked: false })
  })

  it('treats an older answer that carried a masked value as unchanged', () => {
    const base = managedCase({ parameters: [{ name: 'password', value: 'hunter2', masked: true }] })
    expect(payloadFrom(base, formFromCase(base))).toEqual({})
  })
})

describe('differingFields and rebaseForm (the 409)', () => {
  const base = managedCase()
  const theirs = managedCase({ version: 5, title: 'Sign in with SSO (v2)', tags: ['smoke'], status: 'needs_update' })

  it('lists what someone else changed, in the form order, then the status', () => {
    expect(differingFields(base, theirs)).toEqual(['title', 'tags', 'status'])
    expect(fieldLabels(differingFields(base, theirs))).toBe('Title, Tags, Status')
    expect(differingFields(base, base)).toEqual([])
  })

  it('Keep editing: untouched fields take theirs, touched ones keep mine', () => {
    const mine = edit(base, (f) => ({ ...f, title: 'My title', description: 'My description', change_summary: 'why' }))
    expect(touchedFields(base, mine)).toEqual(['title', 'description'])
    const rebased = rebaseForm(base, mine, theirs)
    expect(rebased.title).toBe('My title')
    expect(rebased.description).toBe('My description')
    expect(rebased.tags).toBe('smoke')
    expect(rebased.change_summary).toBe('why')
    // Against the fresh case, a Save now sends exactly the reader's two fields.
    expect(payloadFrom(theirs, rebased)).toEqual({ title: 'My title', description: 'My description' })
  })
})

describe('changedByNames', () => {
  it.each([
    [['Priya Shah'], 'Priya Shah'],
    [['Priya Shah', 'Priya Shah'], 'Priya Shah'],
    [['Priya Shah', 'Ola Ade'], 'Priya Shah and 1 other'],
    [['Priya Shah', 'Ola Ade', 'Kim'], 'Priya Shah and 2 others'],
    [[null, undefined, '  '], 'someone else'],
    [[], 'someone else'],
  ])('%j → %s', (names, expected) => {
    expect(changedByNames(names)).toBe(expected)
  })
})
