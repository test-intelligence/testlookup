/**
 * W06 DEF-01 §2.2-14: the link dialog takes a Jira issue key (upper-cased as
 * typed, Save only for a real key shape), says whether the key will be checked
 * against Jira or saved without a check (and why), toasts what the server did,
 * removes a link with `jira_key: null`, and never renders an unsafe link.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { JiraDefectMetadata } from '@/services/defectJiraService'
import type { DefectItem } from '@/types/analytics'

const updateDefect = vi.hoisted(() => vi.fn())
vi.mock('@/services/defectService', () => ({ updateDefect }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const metadata = vi.hoisted(() => ({ current: undefined as Partial<JiraDefectMetadata> | undefined, calls: [] as unknown[] }))
vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: (projectId: unknown) => {
    metadata.calls.push(projectId)
    return { metadata: metadata.current, isLoading: metadata.current === undefined, isError: false }
  },
}))

import LinkJiraIssueDialog from './LinkJiraIssueDialog'

const ROW: DefectItem = {
  id: 'd-1', project_id: 'p-1', title: 'Checkout fails', resolution_status: 'OPEN',
  created_at: '2026-10-01T00:00:00Z', test_name: 'test_checkout',
}
const OFFLINE = { available: false, reason: 'offline_mode', projects: [], issue_types: [], default_project_key: null, webhook_available: false }
const ONLINE = { ...OFFLINE, available: true, reason: null }

function renderDialog(defect: DefectItem = ROW) {
  const onClose = vi.fn()
  const onUpdated = vi.fn()
  render(<LinkJiraIssueDialog defect={defect} onClose={onClose} onUpdated={onUpdated} />)
  return { onClose, onUpdated, dialog: screen.getByRole('dialog', { name: 'Link a Jira issue' }) }
}

const keyInput = () => screen.getByLabelText('Jira issue key')
const save = () => screen.getByRole('button', { name: 'Save' })
const type = (value: string) => fireEvent.change(keyInput(), { target: { value } })
const click = async (button: HTMLElement) => {
  await act(async () => {
    fireEvent.click(button)
  })
}

beforeEach(() => {
  updateDefect.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
  metadata.current = OFFLINE
  metadata.calls = []
})

describe('LinkJiraIssueDialog', () => {
  it("reads the defect's project's Jira availability", () => {
    renderDialog()
    expect(metadata.calls).toContain('p-1')
    expect(keyInput()).toHaveAttribute('placeholder', 'ABC-123')
  })

  it('upper-cases the key as typed and enables Save only for a key shape', () => {
    renderDialog()
    type('abc-1x')
    expect(keyInput()).toHaveValue('ABC-1X')
    expect(save()).toBeDisabled()
    type('abc-123')
    expect(keyInput()).toHaveValue('ABC-123')
    expect(save()).toBeEnabled()
    type('123-4')
    expect(save()).toBeDisabled()
  })

  it('says why Save is disabled for a malformed key, and nothing for an empty or good one', () => {
    renderDialog()
    expect(screen.queryByText(/Not a Jira issue key/)).toBeNull()
    type('not a key')
    expect(screen.getByText('Not a Jira issue key (expected something like ABC-123).')).toBeInTheDocument()
    type('abc-123')
    expect(screen.queryByText(/Not a Jira issue key/)).toBeNull()
  })

  it('says the key is saved without a check offline, naming the reason', () => {
    renderDialog()
    expect(screen.getByText('Jira is not reachable from here (offline mode), so the key is saved without a check.')).toBeInTheDocument()
  })

  it.each([
    ['disabled', 'Jira integration disabled'],
    ['not_configured', 'Jira not configured'],
  ])('labels the %s reason', (reason, label) => {
    metadata.current = { ...OFFLINE, reason }
    renderDialog()
    expect(screen.getByText(`Jira is not reachable from here (${label}), so the key is saved without a check.`)).toBeInTheDocument()
  })

  it('says the key is checked against Jira when Jira is available', () => {
    metadata.current = ONLINE
    renderDialog()
    expect(screen.getByText('The key is checked against Jira before it is saved.')).toBeInTheDocument()
    expect(screen.queryByText(/saved without a check/)).toBeNull()
  })

  it('sends { jira_key } and toasts "saved locally" with the reason', async () => {
    updateDefect.mockResolvedValue({
      defect: { ...ROW, jira_ticket_id: 'ABC-123', jira_ticket_url: 'https://jira.example.test/browse/ABC-123' },
      changed_fields: ['jira'],
      jira: { status: 'saved_locally', reason: 'offline_mode', jira_key: 'ABC-123', jira_url: 'https://jira.example.test/browse/ABC-123' },
    })
    const { onUpdated, onClose } = renderDialog()
    type('abc-123')
    await click(save())
    expect(updateDefect).toHaveBeenCalledTimes(1)
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { jira_key: 'ABC-123' }, { suppressToast: true })
    expect(onUpdated).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Linked to ABC-123 — saved locally (offline mode)')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("toasts a validated key with Jira's summary, as text", async () => {
    metadata.current = ONLINE
    updateDefect.mockResolvedValue({
      defect: { ...ROW, jira_ticket_id: 'ABC-123' },
      changed_fields: ['jira'],
      jira: { status: 'validated', jira_key: 'ABC-123', summary: 'Checkout <b>500</b>' },
    })
    renderDialog()
    type('ABC-123')
    await click(save())
    expect(toast.success).toHaveBeenCalledWith('Linked to ABC-123 — checked against Jira: "Checkout <b>500</b>"')
  })

  it("shows Jira's refusal as one toast and saves nothing", async () => {
    metadata.current = ONLINE
    updateDefect.mockRejectedValue({ response: { status: 422, data: { detail: 'Jira has no issue ABC-999.' } } })
    const { onUpdated, onClose } = renderDialog()
    type('ABC-999')
    await click(save())
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error).toHaveBeenCalledWith('Jira has no issue ABC-999.')
    expect(onUpdated).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('removes a link with jira_key: null', async () => {
    updateDefect.mockResolvedValue({ defect: { ...ROW, jira_ticket_id: undefined }, changed_fields: ['jira'], jira: { status: 'unlinked' } })
    const { onUpdated } = renderDialog({ ...ROW, jira_ticket_id: 'ABC-7', jira_ticket_url: 'https://jira.example.test/browse/ABC-7' })
    await click(screen.getByRole('button', { name: 'Remove link' }))
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { jira_key: null }, { suppressToast: true })
    expect(onUpdated).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Jira link removed')
  })

  it('has no Remove link while the defect is not linked', () => {
    renderDialog()
    expect(screen.queryByRole('button', { name: 'Remove link' })).toBeNull()
  })

  it('does not re-save the key the defect already has', () => {
    renderDialog({ ...ROW, jira_ticket_id: 'ABC-7' })
    type('abc-7')
    expect(save()).toBeDisabled()
  })

  it('links the current issue only through a safe URL', () => {
    renderDialog({ ...ROW, jira_ticket_id: 'ABC-7', jira_ticket_url: 'https://jira.example.test/browse/ABC-7' })
    expect(screen.getByRole('link', { name: 'ABC-7' })).toHaveAttribute('href', 'https://jira.example.test/browse/ABC-7')
  })

  it('renders a javascript: URL as plain text, never a link', () => {
    const { dialog } = renderDialog({ ...ROW, jira_ticket_id: 'ABC-7', jira_ticket_url: 'javascript:alert(1)' })
    expect(screen.queryByRole('link')).toBeNull()
    expect(dialog.querySelector('[data-current-jira-link]')).toHaveTextContent('Linked to ABC-7')
  })
})
