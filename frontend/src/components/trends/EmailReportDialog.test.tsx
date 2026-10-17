/**
 * TRND-01: the Email report dialog. Members only (no service, synthetic or
 * inactive account), Send disabled until a recipient is chosen and the relay
 * can deliver, the body the route reads, and every refusal shown as text —
 * the availability one BEFORE any click.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const availability = vi.hoisted(() => ({
  value: { available: true, code: null, reason: null } as { available: boolean; code: string | null; reason: string | null } | null,
  isLoading: false,
  error: null as unknown,
  asked: [] as boolean[],
}))
vi.mock('@/hooks/useEmailAvailability', () => ({
  useEmailAvailability: (open: boolean) => {
    availability.asked.push(open)
    return { availability: availability.value, isLoading: availability.isLoading, error: availability.error }
  },
}))

const members = vi.hoisted(() => ({
  data: [] as Record<string, unknown>[],
  asked: [] as (string | null)[],
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: (projectId: string | null) => {
    members.asked.push(projectId)
    return { data: members.data, isLoading: false, error: undefined }
  },
}))

vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: [{ id: 'r-24', name: '2.4' }, { id: 'r-25', name: '2.5' }] } }),
}))

const role = vi.hoisted(() => ({ isAdmin: false }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: role.isAdmin, isQaLead: true, isQaEngineer: true }) }))

const service = vi.hoisted(() => ({ emailTrends: vi.fn() }))
vi.mock('@/services/trendsReportService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/trendsReportService')>()
  return { ...actual, emailTrends: service.emailTrends }
})

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), toasts) }))

import EmailReportDialog, { mailableMembers, releaseText, reportSummaryLine } from './EmailReportDialog'

const member = (id: string, over: Record<string, unknown> = {}) => ({
  id: `m-${id}`,
  user_id: id,
  project_id: 'p1',
  role: 'QA_ENGINEER',
  created_at: '2026-01-01T00:00:00Z',
  email: `${id}@example.com`,
  username: id,
  full_name: `User ${id}`,
  is_service_account: false,
  is_synthetic: false,
  is_active: true,
  ...over,
})

function renderDialog(props: Partial<Parameters<typeof EmailReportDialog>[0]> = {}) {
  const onClose = vi.fn()
  render(
    <MemoryRouter>
      <EmailReportDialog
        projectId="p1"
        days={14}
        suiteFilter="payments"
        release="r-24"
        suiteLabel="payments"
        releaseLabel="2.4"
        onClose={onClose}
        {...props}
      />
    </MemoryRouter>,
  )
  return { onClose, dialog: screen.getByRole('region', { name: 'Email report' }) }
}

const send = () => screen.getByRole('button', { name: 'Send' })

beforeEach(() => {
  availability.value = { available: true, code: null, reason: null }
  availability.isLoading = false
  availability.error = null
  availability.asked = []
  members.asked = []
  members.data = [
    member('ada'),
    member('bob'),
    member('svc', { is_service_account: true }),
    member('lead-bot', { is_synthetic: true }),
    member('gone', { is_active: false }),
  ]
  role.isAdmin = false
  service.emailTrends.mockReset()
  toasts.success.mockReset()
})

describe('EmailReportDialog', () => {
  it('states what will be sent and lists real, active members only', () => {
    const { dialog } = renderDialog()
    expect(within(dialog).getByText('Trends · last 14 days · suite payments · release 2.4')).toBeInTheDocument()
    const boxes = dialog.querySelectorAll('[data-email-member]')
    expect([...boxes].map((b) => b.getAttribute('data-email-member'))).toEqual(['ada', 'bob'])
    expect(within(dialog).getByText(/ada@example\.com/)).toBeInTheDocument()
    expect(members.asked.every((id) => id === 'p1')).toBe(true)
    expect(availability.asked.every(Boolean)).toBe(true)
  })

  it('keeps Send disabled until a recipient is chosen', () => {
    renderDialog()
    expect(send()).toBeDisabled()
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    expect(send()).toBeEnabled()
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    expect(send()).toBeDisabled()
  })

  it('sends the chosen members and the typed addresses in the route’s body, then says how many', async () => {
    service.emailTrends.mockResolvedValue({ status: 'sent', sent_to: 3, recipient: 'ada@example.com', window_days: 14 })
    const { onClose } = renderDialog()
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    fireEvent.click(document.querySelector('[data-email-member="bob"]') as HTMLElement)
    const input = screen.getByLabelText('Add an address')
    fireEvent.change(input, { target: { value: 'not-an-address' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByText('That is not an email address.')).toBeInTheDocument()
    fireEvent.change(input, { target: { value: ' Ops@Example.org ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    fireEvent.click(send())
    await waitFor(() => expect(service.emailTrends).toHaveBeenCalledTimes(1))
    expect(service.emailTrends).toHaveBeenCalledWith({
      project_id: 'p1',
      days: 14,
      release_id: 'r-24',
      suite_name: ['payments'],
      recipient_user_ids: ['ada', 'bob'],
      recipient_emails: ['ops@example.org'],
    })
    await waitFor(() => expect(toasts.success).toHaveBeenCalledWith('Sent to 3 recipients'))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows the server’s refusal (503, 504, 422) as text under the form', async () => {
    service.emailTrends.mockRejectedValue({ response: { status: 503, data: { detail: 'Email is not configured — Settings › Notifications.' } } })
    renderDialog()
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    fireEvent.click(send())
    expect(await screen.findByRole('alert')).toHaveTextContent('Email is not configured — Settings › Notifications.')
    expect(toasts.success).not.toHaveBeenCalled()
    // Hostile server text stays text.
    service.emailTrends.mockRejectedValue({ response: { status: 502, data: { detail: '<img src=x onerror=alert(1)>' } } })
    fireEvent.click(send())
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('<img src=x onerror=alert(1)>'))
    expect(document.querySelector('[data-email-refusal] img')).toBeNull()
  })

  it('availability false: the reason is shown before any click and Send stays disabled', () => {
    availability.value = { available: false, code: 'egress_blocked', reason: 'Offline mode: the relay relay.example.com is outside the allow-list.' }
    renderDialog()
    expect(screen.getByRole('status')).toHaveTextContent('Offline mode: the relay relay.example.com is outside the allow-list.')
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    expect(send()).toBeDisabled()
    expect(screen.queryByRole('link', { name: /Settings › Notifications/ })).toBeNull()
  })

  it('not configured: an ADMIN also gets the link to Settings › Notifications', () => {
    availability.value = { available: false, code: 'not_configured', reason: 'Email is not configured — Settings › Notifications.' }
    role.isAdmin = true
    renderDialog()
    expect(screen.getByRole('link', { name: 'Open Settings › Notifications' })).toHaveAttribute('href', '/settings/notifications')
  })

  it('a malformed or failed availability read disables Send and says so', () => {
    availability.value = null
    availability.error = new Error('boom')
    renderDialog()
    expect(screen.getByRole('status')).toHaveTextContent(/could not be checked/)
    fireEvent.click(document.querySelector('[data-email-member="ada"]') as HTMLElement)
    expect(send()).toBeDisabled()
  })
})

describe('pure helpers', () => {
  it('mailableMembers guards a non-list', () => {
    expect(mailableMembers(undefined)).toEqual([])
    expect(mailableMembers({} as unknown as [])).toEqual([])
  })

  it('the release is named from the project’s release list when the page gives no label', () => {
    render(
      <MemoryRouter>
        <EmailReportDialog projectId="p1" days={14} suiteFilter={null} release={['r-24', 'r-xyz12345678']} suiteLabel={null} onClose={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Trends · last 14 days · release 2.4, r-xyz123')).toBeInTheDocument()
    expect(releaseText(null, [])).toBeNull()
  })

  it('reportSummaryLine names only what is set', () => {
    expect(reportSummaryLine(1, null, null)).toBe('Trends · last 24 hours')
    expect(reportSummaryLine(30, 'checkout', null)).toBe('Trends · last 30 days · suite checkout')
  })
})
