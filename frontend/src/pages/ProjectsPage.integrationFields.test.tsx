/**
 * W10 INT-03 / INT-03b (contract §2.3 test 4): Edit Project's Integrations block.
 *
 * - D22: no "Jenkins Job Pattern" (nothing read it; the stored value stays).
 * - Splunk Index and OCP Namespace each say what they drive (`ep-splunk-help`,
 *   `ep-ocp-help`), and the input is described by its help.
 * - A cleared field is sent as "" (a null was dropped by the PUT, so it could
 *   never be cleared).
 * - The server's 422 sentence shows under the field, with no toast.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list: vi.fn(async () => []),
    create: vi.fn(async () => ({})),
    update: vi.fn(async () => ({})),
    delete: vi.fn(async () => ({})),
  },
}))

const storeState = {
  setActiveProject: vi.fn(),
  refreshProjects: vi.fn(async () => [] as unknown[]),
  activeProjectId: null,
  activeProject: null,
  projects: [],
}
vi.mock('@/store/projectStore', () => ({
  useProjectStore: (selector?: (s: typeof storeState) => unknown) => (selector ? selector(storeState) : storeState),
  ALL_PROJECTS_ID: 'all',
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: false, isQaLead: true }),
}))

const toastMock = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toastMock }))

import ProjectsPage from './ProjectsPage'
import { projectsService } from '@/services/projectsService'
import { OCP_NAMESPACE_HELP, SPLUNK_INDEX_HELP } from './projectIntegrationFields'

/** The server's sentence (backend `core/integration_values.SPLUNK_INDEX_SENTENCE`). */
const SPLUNK_INDEX_SENTENCE = "Splunk index may use letters, digits, '_' and '-' only (1-80 characters, starting with a letter or digit)."
const OCP_NAMESPACE_SENTENCE =
  "OpenShift namespace must be a DNS label: lowercase letters, digits and '-', at most 63 characters, starting and ending with a letter or digit."

const PAYMENTS = {
  id: 'p-pay', name: 'Payments', slug: 'payments', description: '', is_active: true, created_at: '2026-01-07T09:00:00Z', tags: [],
  jira_project_key: 'PAY', splunk_index: 'payments', ocp_namespace: 'qa-payments', jenkins_job_pattern: '**/payment-*',
}

function err422(detail: unknown) {
  return Object.assign(new Error('Request failed with status code 422'), { response: { status: 422, data: { detail } } })
}

async function openEdit() {
  storeState.refreshProjects.mockImplementation(async () => [PAYMENTS])
  render(<ProjectsPage />)
  await screen.findByText(PAYMENTS.name)
  const card = screen.getByText(PAYMENTS.name, { selector: 'p' }).closest('.card') as HTMLElement
  fireEvent.click(within(card).getByTitle('Edit'))
  return screen.getByRole('region', { name: 'Edit Project' })
}

describe('Edit Project — the Integrations block (W10)', () => {
  afterEach(() => {
    storeState.refreshProjects.mockImplementation(async () => [])
    vi.mocked(projectsService.update).mockReset()
    vi.mocked(projectsService.update).mockImplementation(async () => ({}) as never)
    toastMock.mockClear()
    toastMock.success.mockClear()
    toastMock.error.mockClear()
  })

  it('has no Jenkins Job Pattern field, and never sends it', async () => {
    const section = await openEdit()
    expect(within(section).queryByText(/Jenkins Job Pattern/i)).toBeNull()
    expect(within(section).queryByLabelText(/Jenkins/i)).toBeNull()
    expect(section.querySelector('#ep-jenkins')).toBeNull()
    expect(within(section).queryByDisplayValue('**/payment-*')).toBeNull()

    fireEvent.change(within(section).getByLabelText('Description'), { target: { value: 'Card payments' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(projectsService.update).toHaveBeenCalledTimes(1))
    expect(vi.mocked(projectsService.update).mock.calls[0][1]).toEqual({ description: 'Card payments' })
  })

  it('Splunk Index and OCP Namespace each carry their help line, which describes the input', async () => {
    const section = await openEdit()
    const index = within(section).getByLabelText('Splunk Index')
    const namespace = within(section).getByLabelText('OCP Namespace')
    const indexHelp = section.querySelector('#ep-splunk-help') as HTMLElement
    const nsHelp = section.querySelector('#ep-ocp-help') as HTMLElement
    expect(indexHelp.textContent).toBe("Used by AI log lookups for this project. Empty: the deployment's index.")
    expect(indexHelp.textContent).toBe(SPLUNK_INDEX_HELP)
    expect(nsHelp.textContent).toBe("Used when a run names a pod but no namespace. Empty: the deployment's default namespace.")
    expect(nsHelp.textContent).toBe(OCP_NAMESPACE_HELP)
    expect(index.getAttribute('aria-describedby')).toBe('ep-splunk-help')
    expect(namespace.getAttribute('aria-describedby')).toBe('ep-ocp-help')
    // Three fields in the two-column grid: the third sits alone in row two.
    const grid = index.closest('[class~="grid"]') as HTMLElement
    expect(grid.className).toContain('grid-cols-2')
    expect(grid.children).toHaveLength(3)
  })

  it('clearing Splunk Index sends { splunk_index: "" } (no global toast for its answer)', async () => {
    const section = await openEdit()
    fireEvent.change(within(section).getByLabelText('Splunk Index'), { target: { value: '' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(projectsService.update).toHaveBeenCalledTimes(1))
    expect(projectsService.update).toHaveBeenCalledWith(PAYMENTS.id, { splunk_index: '' }, { suppressToast: true })
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Project updated'))
  })

  it('clearing OCP Namespace sends { ocp_namespace: "" }', async () => {
    const section = await openEdit()
    fireEvent.change(within(section).getByLabelText('OCP Namespace'), { target: { value: '' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(projectsService.update).toHaveBeenCalledTimes(1))
    expect(projectsService.update).toHaveBeenCalledWith(PAYMENTS.id, { ocp_namespace: '' }, { suppressToast: true })
  })

  it('a 422 shows the Splunk index sentence under the field, keeps the form and the typed value, and raises no toast', async () => {
    vi.mocked(projectsService.update).mockRejectedValue(
      err422([{ loc: ['body', 'splunk_index'], msg: SPLUNK_INDEX_SENTENCE, type: 'splunk_index' }]),
    )
    const section = await openEdit()
    const index = within(section).getByLabelText('Splunk Index')
    fireEvent.change(index, { target: { value: 'main | delete' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))

    const alert = await within(section).findByRole('alert')
    expect(alert.textContent).toBe(SPLUNK_INDEX_SENTENCE)
    expect(alert.id).toBe('ep-splunk-error')
    expect(alert.getAttribute('data-field-error')).toBe('splunk_index')
    // Under the index field, after its help line.
    expect(index.parentElement?.contains(alert)).toBe(true)
    expect(index.getAttribute('aria-describedby')).toBe('ep-splunk-help ep-splunk-error')
    expect(index).toHaveValue('main | delete')
    expect(projectsService.update).toHaveBeenCalledWith(PAYMENTS.id, { splunk_index: 'main | delete' }, { suppressToast: true })
    expect(toastMock.error).not.toHaveBeenCalled()
    expect(toastMock.success).not.toHaveBeenCalled()
    expect(screen.getByRole('region', { name: 'Edit Project' })).toBeInTheDocument()

    // Typing again clears the sentence; a valid value saves exactly that field.
    vi.mocked(projectsService.update).mockReset()
    vi.mocked(projectsService.update).mockImplementation(async () => ({}) as never)
    fireEvent.change(index, { target: { value: 'payments-eu' } })
    expect(within(section).queryByRole('alert')).toBeNull()
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(projectsService.update).toHaveBeenCalledTimes(1))
    expect(projectsService.update).toHaveBeenCalledWith(PAYMENTS.id, { splunk_index: 'payments-eu' }, { suppressToast: true })
  })

  it('a 422 naming the namespace shows its sentence under OCP Namespace ("Value error, " dropped)', async () => {
    vi.mocked(projectsService.update).mockRejectedValue(
      err422([{ loc: ['body', 'ocp_namespace'], msg: `Value error, ${OCP_NAMESPACE_SENTENCE}`, type: 'value_error' }]),
    )
    const section = await openEdit()
    fireEvent.change(within(section).getByLabelText('OCP Namespace'), { target: { value: 'QA Testing' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    const alert = await within(section).findByRole('alert')
    expect(alert.textContent).toBe(OCP_NAMESPACE_SENTENCE)
    expect(alert.id).toBe('ep-ocp-error')
    expect(toastMock.error).not.toHaveBeenCalled()
  })

  it('any other failure keeps one toast with the server\'s string detail', async () => {
    vi.mocked(projectsService.update).mockRejectedValue(
      Object.assign(new Error('boom'), { response: { status: 409, data: { detail: 'A project with this name exists' } } }),
    )
    const section = await openEdit()
    fireEvent.change(within(section).getByLabelText('Splunk Index'), { target: { value: 'pay' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('A project with this name exists'))
    expect(within(section).queryByRole('alert')).toBeNull()
  })

  it('a failure whose detail is not a string falls back to "Failed to update project"', async () => {
    vi.mocked(projectsService.update).mockRejectedValue(
      Object.assign(new Error('boom'), { response: { status: 500, data: { detail: { code: 'x' } } } }),
    )
    const section = await openEdit()
    fireEvent.change(within(section).getByLabelText('OCP Namespace'), { target: { value: 'qa-x' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Failed to update project'))
  })
})

describe('New Project — the OCP Namespace 422 (W10)', () => {
  afterEach(() => {
    vi.mocked(projectsService.create).mockReset()
    vi.mocked(projectsService.create).mockImplementation(async () => ({}) as never)
    toastMock.error.mockClear()
  })

  it('shows the namespace sentence under OCP Namespace instead of "Failed to create project"', async () => {
    vi.mocked(projectsService.create).mockRejectedValue(
      err422([{ loc: ['body', 'ocp_namespace'], msg: OCP_NAMESPACE_SENTENCE, type: 'ocp_namespace' }]),
    )
    render(<ProjectsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'New Project' }))
    const section = screen.getByRole('region', { name: 'New Project' })
    fireEvent.change(within(section).getByLabelText('Project Name *'), { target: { value: 'Payments' } })
    const ns = within(section).getByLabelText('OCP Namespace')
    fireEvent.change(ns, { target: { value: 'QA_Payments' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Create Project' }))

    const alert = await within(section).findByRole('alert')
    expect(alert.textContent).toBe(OCP_NAMESPACE_SENTENCE)
    expect(alert.id).toBe('np-ocp-error')
    expect(ns.getAttribute('aria-describedby')).toBe('np-ocp-error')
    expect(ns).toHaveValue('QA_Payments')
    expect(toastMock.error).not.toHaveBeenCalledWith('Failed to create project')
    expect(projectsService.create).toHaveBeenCalledWith({ name: 'Payments', slug: 'payments', ocp_namespace: 'QA_Payments' })
  })
})
