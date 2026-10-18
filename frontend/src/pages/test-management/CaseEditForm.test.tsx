import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { testCaseEditingService } from '@/services/testCaseEditingService'
import type { ManagedTestCase, TestCaseVersion } from '@/types/test-management'
import CaseEditForm from './CaseEditForm'

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: vi.fn(() => ({
    data: [
      { id: 'm-1', user_id: 'user-1', project_id: 'p-1', role: 'TESTER', created_at: '', email: 'a@x', username: 'alice', full_name: 'Alice Tester' },
      { id: 'm-2', user_id: 'user-2', project_id: 'p-1', role: 'QA_LEAD', created_at: '', email: 'p@x', username: 'priya', full_name: 'Priya Shah' },
    ],
    isLoading: false,
    error: undefined,
  })),
}))
vi.mock('@/services/testCaseEditingService', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/services/testCaseEditingService')>()
  return {
    ...real,
    testCaseEditingService: {
      updateCase: vi.fn(),
      getCaseFresh: vi.fn(),
      getCaseHistoryFresh: vi.fn(),
      bulkUpdate: vi.fn(),
      restoreVersion: vi.fn(),
    },
  }
})

const service = vi.mocked(testCaseEditingService)

function managedCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'case-1',
    project_id: 'p-1',
    title: 'Sign in with SSO',
    description: 'The SSO path',
    steps: [
      { step_number: 1, action: 'Open the login page', expected_result: 'The SSO button shows' },
      { step_number: 2, action: 'Click SSO', expected_result: 'The IdP opens' },
    ],
    parameters: [{ name: 'password', value: null, masked: true }],
    expected_result: 'Signed in',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    tags: ['smoke'],
    status: 'active',
    version: 1,
    assignee_id: 'user-1',
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function version(n: number, name: string | null): TestCaseVersion {
  return {
    id: `v-${n}`, test_case_id: 'case-1', version: n, title: 't', status: 'active', change_type: 'updated',
    created_at: '2026-10-02T00:00:00Z', changed_by_name: name,
  }
}

function conflict() {
  return { response: { status: 409, data: { detail: { current_version: 3, expected_version: 1, message: 'stale' } } } }
}

function setup(caseItem = managedCase()) {
  const onCancel = vi.fn()
  const onSaved = vi.fn()
  render(<CaseEditForm caseItem={caseItem} suiteOptions={['Login', 'Checkout']} onCancel={onCancel} onSaved={onSaved} />)
  const form = screen.getByRole('region', { name: 'Edit test case' })
  return { form, onCancel, onSaved }
}

const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

describe('CaseEditForm', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens prefilled with Title focused, and never puts a masked value in the DOM', () => {
    const { form } = setup(managedCase({ parameters: [{ name: 'password', value: 'hunter2', masked: true }] }))
    const title = within(form).getByRole('textbox', { name: 'Title' })
    expect(title).toHaveValue('Sign in with SSO')
    expect(title).toHaveFocus()
    expect(within(form).getByLabelText('Description')).toHaveValue('The SSO path')
    expect(within(form).getByLabelText('Step 1 action')).toHaveValue('Open the login page')
    expect(within(form).getByLabelText('Step 2 expected result')).toHaveValue('The IdP opens')
    expect(within(form).getByLabelText('Tags')).toHaveValue('smoke')
    expect(within(form).getByLabelText('Owner')).toHaveValue('user-1')
    const masked = within(form).getByLabelText('Parameter 1 value')
    expect(masked).toHaveAttribute('type', 'password')
    expect(masked).toHaveValue('')
    expect(masked).toHaveAttribute('placeholder', 'Unchanged (masked)')
    expect(document.body.innerHTML).not.toContain('hunter2')
    // Status is not in the form.
    expect(within(form).queryByLabelText(/^Status/)).toBeNull()
  })

  it('adds, moves and removes steps, numbered by position', () => {
    const { form } = setup()
    fireEvent.click(within(form).getByRole('button', { name: 'Add step' }))
    fireEvent.change(within(form).getByLabelText('Step 3 action'), { target: { value: 'Approve on the IdP' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Move step 3 up' }))
    expect(within(form).getByLabelText('Step 2 action')).toHaveValue('Approve on the IdP')
    expect(within(form).getByLabelText('Step 3 action')).toHaveValue('Click SSO')
    expect(within(form).getByRole('button', { name: 'Move step 1 up' })).toBeDisabled()
    expect(within(form).getByRole('button', { name: 'Move step 3 down' })).toBeDisabled()
    fireEvent.click(within(form).getByRole('button', { name: 'Remove step 1' }))
    expect(within(form).getByLabelText('Step 1 action')).toHaveValue('Approve on the IdP')
    expect(within(form).queryByLabelText('Step 3 action')).toBeNull()
  })

  it('saves ONE PATCH with expected_version and only the changed fields', async () => {
    const updated = managedCase({ version: 2, title: 'Sign in with SAML' })
    service.updateCase.mockResolvedValue(updated)
    const { form, onSaved } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'Sign in with SAML' } })
    fireEvent.change(within(form).getByLabelText('Step 2 expected result'), { target: { value: 'The IdP page opens' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add step' }))
    fireEvent.change(within(form).getByLabelText('Step 3 action'), { target: { value: 'Approve' } })
    fireEvent.change(within(form).getByLabelText('Tags'), { target: { value: 'smoke, sso' } })
    fireEvent.change(within(form).getByLabelText('Owner'), { target: { value: 'user-2' } })
    fireEvent.change(within(form).getByLabelText('Change summary (optional)'), { target: { value: 'SAML now' } })
    save()
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(updated))
    expect(service.updateCase).toHaveBeenCalledTimes(1)
    expect(service.updateCase).toHaveBeenCalledWith('case-1', {
      expected_version: 1,
      title: 'Sign in with SAML',
      steps: [
        { step_number: 1, action: 'Open the login page', expected_result: 'The SSO button shows' },
        { step_number: 2, action: 'Click SSO', expected_result: 'The IdP page opens' },
        { step_number: 3, action: 'Approve', expected_result: '' },
      ],
      tags: ['smoke', 'sso'],
      assignee_id: 'user-2',
      change_summary: 'SAML now',
    })
    expect(toast.success).toHaveBeenCalledWith('Test case saved (v2)')
  })

  it('sends nothing when nothing changed or the title is too short', () => {
    const { form, onSaved } = setup()
    save()
    expect(within(form).getByRole('alert')).toHaveTextContent('No changes to save')
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: ' ab ' } })
    save()
    expect(within(form).getByRole('alert')).toHaveTextContent('Title must be at least 3 characters')
    expect(service.updateCase).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('on a 409 re-reads the case and its history and names who changed what', async () => {
    service.updateCase.mockRejectedValueOnce(conflict())
    service.getCaseFresh.mockResolvedValue(managedCase({ version: 3, title: 'Sign in with SSO (lead)', priority: 'critical' }))
    service.getCaseHistoryFresh.mockResolvedValue([version(3, 'Priya Shah'), version(2, 'Priya Shah'), version(1, 'Alice Tester')])
    const { form, onSaved } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'My title' } })
    save()
    const notice = await within(form).findByText(/^Changed by/)
    expect(notice).toHaveTextContent('Changed by Priya Shah since you opened it (v1 → v3).')
    const box = notice.closest('[data-edit-conflict]') as HTMLElement
    expect(box).toHaveTextContent('Changed: Title, Priority')
    expect(box).toHaveTextContent('You also changed: Title')
    expect(service.getCaseFresh).toHaveBeenCalledWith('case-1')
    expect(service.getCaseHistoryFresh).toHaveBeenCalledWith('case-1')
    // Never a silent overwrite: one PATCH, no retry.
    expect(service.updateCase).toHaveBeenCalledTimes(1)
    expect(onSaved).not.toHaveBeenCalled()
    expect(within(form).getByRole('textbox', { name: 'Title' })).toHaveValue('My title')
  })

  it('Reload theirs resets the form to the fresh case', async () => {
    service.updateCase.mockRejectedValueOnce(conflict())
    service.getCaseFresh.mockResolvedValue(managedCase({ version: 3, title: 'Lead title' }))
    service.getCaseHistoryFresh.mockResolvedValue([version(3, null)])
    const { form } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'My title' } })
    save()
    expect(await within(form).findByText(/^Changed by someone else since you opened it/)).toBeInTheDocument()
    fireEvent.click(within(form).getByRole('button', { name: 'Reload theirs' }))
    expect(within(form).getByRole('textbox', { name: 'Title' })).toHaveValue('Lead title')
    // The notice stays until the next Save; its choice is made.
    expect(within(form).queryByRole('button', { name: 'Reload theirs' })).toBeNull()
    save()
    expect(within(form).getByText('No changes to save')).toBeInTheDocument()
  })

  it('Keep editing then Save sends the fresh expected_version and only the reader\'s fields', async () => {
    service.updateCase.mockRejectedValueOnce(conflict()).mockResolvedValueOnce(managedCase({ version: 4 }))
    service.getCaseFresh.mockResolvedValue(managedCase({ version: 3, title: 'Lead title', priority: 'critical' }))
    service.getCaseHistoryFresh.mockResolvedValue([version(3, 'Priya Shah'), version(2, 'Ola Ade')])
    const { form, onSaved } = setup()
    fireEvent.change(within(form).getByLabelText('Description'), { target: { value: 'My description' } })
    save()
    expect(await within(form).findByText(/^Changed by Priya Shah and 1 other/)).toBeInTheDocument()
    fireEvent.click(within(form).getByRole('button', { name: 'Keep editing' }))
    expect(within(form).getByRole('textbox', { name: 'Title' })).toHaveValue('Lead title')
    expect(within(form).getByLabelText('Description')).toHaveValue('My description')
    save()
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(service.updateCase).toHaveBeenLastCalledWith('case-1', { expected_version: 3, description: 'My description' })
  })

  it('when the re-read fails, says so and offers only Cancel', async () => {
    service.updateCase.mockRejectedValueOnce(conflict())
    service.getCaseFresh.mockRejectedValue(new Error('down'))
    service.getCaseHistoryFresh.mockResolvedValue([])
    const { form } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'My title' } })
    save()
    expect(await within(form).findByText(/and the new version could not be read\. Close the form and open the case again\./)).toBeInTheDocument()
    expect(within(form).queryByRole('button', { name: 'Save changes' })).toBeNull()
    expect(within(form).getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })

  it('a 500 keeps the form and its text, with the sentence', async () => {
    service.updateCase.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'Database unavailable' } } })
    const { form, onSaved } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'My title' } })
    save()
    expect(await within(form).findByRole('alert')).toHaveTextContent('Database unavailable')
    expect(within(form).getByRole('textbox', { name: 'Title' })).toHaveValue('My title')
    expect(onSaved).not.toHaveBeenCalled()
    service.updateCase.mockRejectedValueOnce(new Error('network'))
    save()
    await waitFor(() => expect(within(form).getByRole('alert')).toHaveTextContent('The test case could not be saved.'))
  })

  it('Cancel and Escape send nothing', () => {
    const { form, onCancel } = setup()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'Typed' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    fireEvent.keyDown(within(form).getByRole('textbox', { name: 'Title' }), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(2)
    expect(service.updateCase).not.toHaveBeenCalled()
  })

  it('offers the vocabulary plus an outside severity as written, and suites as suggestions', () => {
    const { form } = setup(managedCase({ severity: 'medium' }))
    const severity = within(form).getByLabelText('Severity') as HTMLSelectElement
    expect(severity.value).toBe('medium')
    expect(Array.from(severity.options).map((o) => o.value)).toEqual(['blocker', 'critical', 'major', 'minor', 'trivial', 'medium'])
    const suite = within(form).getByLabelText('Suite')
    const list = document.getElementById(suite.getAttribute('list') ?? '')
    expect(Array.from(list?.querySelectorAll('option') ?? []).map((o) => o.getAttribute('value'))).toEqual(['Login', 'Checkout'])
  })
})
