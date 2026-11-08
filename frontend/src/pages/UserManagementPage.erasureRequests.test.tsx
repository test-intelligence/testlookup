/**
 * W38 (W31 F-1, contract §4) on /users?tab=erasure-requests, through the REAL SWR hooks (the subject-rights service
 * mocked, JOB_POLL_MS 40 ms): the admin-only tab after "Pending invitations"; the Pending filter by default; a pending
 * row's Review opens the person's EXISTING Personal data section under the row (the preview names the request) and
 * its erasure re-reads the queue while the row stays drawn as accepted; Decline → the four reasons → ONE POST with
 * the code → the queue re-reads; a refusal reads inline; the reader's own request offers only the note; decided rows.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  erasurePreviewFixture,
  erasureRequestList,
  erasureRequestRowFixture,
  REQUEST_ID,
  SUBJECT_DISPLAY,
  SUBJECT_USER_ID,
  SUBJECT_USERNAME,
  subjectErrorBody,
  subjectJobFixture,
  subjectJobList,
  subjectRefusal,
} from '@/test/subjectRightsMocks'
import { DECLINE_SENTENCES, ERROR_SENTENCES } from '@/types/subjectRights'
import { formatDay } from '@/utils/formatters'

const perms = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  refreshUsers: vi.fn(),
  refreshInvitations: vi.fn(),
  useInvitations: () => ({ data: [], isLoading: false, error: null }),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: perms.isAdmin, canManageUsers: true }) }))
vi.mock('@/services/projectsService', () => ({ projectsService: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))
vi.mock('@/types/subjectRights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/types/subjectRights')>()),
  JOB_POLL_MS: 40,
}))
const service = vi.hoisted(() => ({
  listErasureRequests: vi.fn(), declineErasureRequest: vi.fn(), listUserJobs: vi.fn(), getErasurePreview: vi.fn(),
  getJob: vi.fn(), startUserExport: vi.fn(), eraseUser: vi.fn(), downloadExport: vi.fn(),
}))
vi.mock('@/services/subjectRightsService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/subjectRightsService')>()),
  subjectRightsService: service,
}))

import { refreshUsers } from '@/hooks/useUserManagement'
import { useAuthStore } from '@/store/authStore'
import UserManagementPage from './UserManagementPage'

const PENDING = erasureRequestRowFixture()
const requestRow = (id = REQUEST_ID) => document.querySelector(`tr[data-erasure-request="${id}"]`) as HTMLElement
const table = () => screen.getByTestId('erasure-requests')

function renderPage(path = '/users?tab=erasure-requests') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={[path]}><UserManagementPage /></MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  perms.isAdmin = true
  for (const fn of Object.values(service)) fn.mockReset()
  vi.mocked(refreshUsers).mockReset()
  service.listErasureRequests.mockResolvedValue(erasureRequestList([PENDING]))
  service.listUserJobs.mockResolvedValue(subjectJobList([]))
  service.getErasurePreview.mockResolvedValue(
    erasurePreviewFixture({ pending_request: { id: REQUEST_ID, requested_at: PENDING.requested_at } }),
  )
  act(() => useAuthStore.setState({ user: { id: 'admin-id', username: 'admin', email: 'admin@example.test', full_name: 'Admin', role: 'ADMIN', is_active: true, must_change_password: false, avatar_color: null } }))
})
afterEach(() => act(() => useAuthStore.setState({ user: null })))

describe('Users › Erasure requests (W38)', () => {
  it('admin: the tab sits after "Pending invitations"; the table opens on Pending (Person, Asked, Status, Decided)', async () => {
    renderPage()
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent)
    expect(tabs.slice(-2)).toEqual(['Pending invitations', 'Erasure requests'])
    expect(await screen.findByText(SUBJECT_DISPLAY)).toBeInTheDocument()
    expect(service.listErasureRequests).toHaveBeenCalledWith('pending')
    expect(within(table()).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Person', 'Asked', 'Status', 'Decided', 'Actions'])
    expect(screen.getByRole('button', { name: 'Pending' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(requestRow()).getByText('Pending')).toBeInTheDocument()
    expect(within(requestRow()).getByText(SUBJECT_USERNAME)).toBeInTheDocument()
  })

  it('not an admin: no tab, and ?tab=erasure-requests shows the Users tab without asking for the queue', () => {
    perms.isAdmin = false
    renderPage()
    expect(screen.queryByRole('tab', { name: 'Erasure requests' })).toBeNull()
    expect(screen.queryByTestId('erasure-requests')).toBeNull()
    expect(service.listErasureRequests).not.toHaveBeenCalled()
  })

  it('Review opens the person\'s Personal data under the row; erasing re-reads the queue and the row stays, accepted', async () => {
    const lockout = subjectJobFixture({ kind: 'erasure', status: 'queued', subject_user_id: SUBJECT_USER_ID })
    service.eraseUser.mockResolvedValue(lockout)
    service.getJob.mockResolvedValue(lockout)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: `Review the erasure request of ${SUBJECT_DISPLAY}` }))
    const section = await screen.findByRole('region', { name: `Personal data for ${SUBJECT_DISPLAY}` })
    expect(requestRow().nextElementSibling?.contains(section)).toBe(true)
    expect(await within(section).findByText(`This person asked for erasure on ${formatDay(PENDING.requested_at)}.`)).toBeInTheDocument()
    expect(service.getErasurePreview).toHaveBeenCalledWith(SUBJECT_USER_ID)
    // The server closed the request in the erasure's commit (D-5): the Pending re-read no longer lists it.
    service.listErasureRequests.mockResolvedValue(erasureRequestList([]))
    fireEvent.change(within(section).getByLabelText(`Type ${SUBJECT_USERNAME} to confirm`), { target: { value: SUBJECT_USERNAME } })
    fireEvent.click(within(section).getByRole('button', { name: 'Erase permanently' }))
    await waitFor(() => expect(service.eraseUser).toHaveBeenCalledWith(SUBJECT_USER_ID, SUBJECT_USERNAME))
    await waitFor(() => expect(service.listErasureRequests).toHaveBeenCalledTimes(2))
    expect(refreshUsers).toHaveBeenCalled()
    await waitFor(() => expect(within(requestRow()).getByText('Accepted — erasure started')).toBeInTheDocument())
    expect(screen.getByRole('region', { name: `Personal data for ${SUBJECT_DISPLAY}` })).toBeInTheDocument()
    expect(within(requestRow()).queryByRole('button', { name: /Review the erasure request/ })).toBeNull()
  })

  it('Decline: the four reasons, disabled until one is chosen, the person\'s sentence shown, ONE POST with the code', async () => {
    service.declineErasureRequest.mockResolvedValue(erasureRequestRowFixture({ status: 'declined', decline_code: 'identity_unconfirmed' }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: `Decline the erasure request of ${SUBJECT_DISPLAY}` }))
    const form = await screen.findByRole('region', { name: `Decline the erasure request of ${SUBJECT_DISPLAY}` })
    const select = within(form).getByLabelText('Reason') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['', 'legal_obligation', 'active_contract', 'identity_unconfirmed', 'other'])
    const send = within(form).getByRole('button', { name: 'Decline request' })
    expect(send).toBeDisabled()
    fireEvent.change(select, { target: { value: 'identity_unconfirmed' } })
    expect(within(form).getByText(`They will read: “${DECLINE_SENTENCES.identity_unconfirmed}”`)).toBeInTheDocument()
    service.listErasureRequests.mockResolvedValue(erasureRequestList([]))
    fireEvent.click(send)
    await waitFor(() => expect(service.declineErasureRequest).toHaveBeenCalledTimes(1))
    expect(service.declineErasureRequest).toHaveBeenCalledWith(REQUEST_ID, 'identity_unconfirmed')
    await waitFor(() => expect(screen.queryByRole('region', { name: /Decline the erasure request/ })).toBeNull())
    expect(await screen.findByText('No erasure requests are waiting for review.')).toBeInTheDocument()
  })

  it('a refused decline reads its sentence inline and keeps the form', async () => {
    service.declineErasureRequest.mockRejectedValue(subjectRefusal(409, subjectErrorBody('request_not_pending')))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: `Decline the erasure request of ${SUBJECT_DISPLAY}` }))
    const form = await screen.findByRole('region', { name: `Decline the erasure request of ${SUBJECT_DISPLAY}` })
    fireEvent.change(within(form).getByLabelText('Reason'), { target: { value: 'other' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Decline request' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(ERROR_SENTENCES.request_not_pending)
    expect(screen.queryByText(/Request failed with status code/)).toBeNull()
  })

  it('the reader\'s own request: no Review, no Decline — only the own_request note', async () => {
    service.listErasureRequests.mockResolvedValue(erasureRequestList([erasureRequestRowFixture({ requester_is_you: true })]))
    renderPage()
    expect(await within(await screen.findByTestId('erasure-requests')).findByText(ERROR_SENTENCES.own_request)).toBeInTheDocument()
    expect(within(requestRow()).queryAllByRole('button')).toHaveLength(0)
  })

  it('Decided: the decided rows — accepted, declined with its reason and decider, an erased person as "Erased user"', async () => {
    const accepted = erasureRequestRowFixture({ id: '00000000-0000-4000-8000-0000000e5a03', status: 'accepted', subject_display: 'Erased user', subject_username: 'erased-00000000', subject_is_erased: true })
    const declined = erasureRequestRowFixture({ id: '00000000-0000-4000-8000-0000000e5a04', status: 'declined', decline_code: 'legal_obligation' })
    renderPage()
    await screen.findByText(SUBJECT_DISPLAY)
    service.listErasureRequests.mockResolvedValue(erasureRequestList([accepted, declined]))
    fireEvent.click(screen.getByRole('button', { name: 'Decided' }))
    await waitFor(() => expect(service.listErasureRequests).toHaveBeenLastCalledWith('decided'))
    expect(await within(requestRow(accepted.id)).findByText('Accepted — erasure started')).toBeInTheDocument()
    expect(within(requestRow(accepted.id)).getByText('Erased user')).toBeInTheDocument()
    expect(within(requestRow(declined.id)).getByText('Declined — Legal obligation')).toBeInTheDocument()
    expect(within(requestRow(declined.id)).getByText(/ by Morgan Admin$/)).toBeInTheDocument()
    expect(within(table()).queryAllByRole('button')).toHaveLength(0)
  })

  it('the read fails: "Could not load erasure requests."', async () => {
    service.listErasureRequests.mockRejectedValue(new Error('boom'))
    renderPage()
    expect(await screen.findByText('Could not load erasure requests.')).toBeInTheDocument()
  })
})
