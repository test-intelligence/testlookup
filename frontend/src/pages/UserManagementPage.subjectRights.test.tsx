/**
 * W31 STRAT-12 on /users (contract §4.1, §4.2, §4.4): the "Personal data" row action — absent on a service /
 * synthetic row and on the admin's OWN row; the erased row ("Erased user" over the pseudonym, the "Erased" badge,
 * the role as text, Personal data its only action, never the @erased.invalid e-mail); the User cell truncated at
 * 185 px; the table's load asks nothing of the subject-rights API; opening the section issues its two GETs, an
 * export starts for that person, and closing the section stops the job polling.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { subjectJobFixture, subjectJobList, erasurePreviewFixture } from '@/test/subjectRightsMocks'
import { UI_SENTENCES } from '@/types/subjectRights'

const users = vi.hoisted(() => ({ rows: [] as unknown[] }))
vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: users.rows, total: users.rows.length }, isLoading: false }),
  refreshUsers: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: true, canManageUsers: true }) }))
vi.mock('@/services/projectsService', () => ({ projectsService: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: { updateUserRole: vi.fn(), updateUserStatus: vi.fn(), getUserMemberships: vi.fn().mockResolvedValue([]) },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))
vi.mock('@/types/subjectRights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/types/subjectRights')>()),
  JOB_POLL_MS: 40,
}))
const service = vi.hoisted(() => ({
  listUserJobs: vi.fn(), getErasurePreview: vi.fn(), getJob: vi.fn(), startUserExport: vi.fn(), eraseUser: vi.fn(), downloadExport: vi.fn(),
}))
vi.mock('@/services/subjectRightsService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/subjectRightsService')>()),
  subjectRightsService: service,
}))

import { useAuthStore } from '@/store/authStore'
import UserManagementPage from './UserManagementPage'

const row = (id: string, username: string, fullName: string, extra: Record<string, unknown> = {}) => ({
  id, username, full_name: fullName, email: `${username}@example.test`, role: 'TESTER', is_active: true, created_at: '2026-01-01T00:00:00Z', ...extra,
})
const ADMIN = row('admin-id', 'admin', 'Admin', { role: 'ADMIN' })
const QUINN = row('quinn-id', 'quinn.tester', 'Quinn Tester')
const ROBOT = row('robot-id', 'ci-robot', 'CI Robot', { is_service_account: true })
const SYNTH = row('synth-id', 'qa-lead-checkout', 'Checkout QA lead', { is_synthetic: true })
const ERASED = row('erased-id', 'erased-0a1b2c3d', 'Erased user', {
  email: 'erased-0a1b2c3d@erased.invalid', is_active: false, is_erased: true, erased_at: '2026-11-04T10:00:00Z', role: 'QA_ENGINEER',
})

const rowOf = (text: string) => screen.getByText(text, { exact: true }).closest('tr') as HTMLElement
const personalData = (username: string) => screen.queryByRole('button', { name: `Personal data for ${username}` })

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={['/users']}><UserManagementPage /></MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  users.rows = [ADMIN, QUINN, ROBOT, SYNTH, ERASED]
  for (const fn of Object.values(service)) fn.mockReset()
  service.listUserJobs.mockResolvedValue(subjectJobList([]))
  service.getErasurePreview.mockResolvedValue(erasurePreviewFixture())
  act(() => useAuthStore.setState({ user: { ...ADMIN, must_change_password: false, avatar_color: null } }))
})
afterEach(() => act(() => useAuthStore.setState({ user: null })))

describe('UserManagementPage — Personal data', () => {
  it('the action is on a person\'s row and an erased row; never on a service / synthetic row or the admin\'s own', () => {
    renderPage()
    expect(personalData('quinn.tester')).toBeInTheDocument()
    expect(personalData('erased-0a1b2c3d')).toBeInTheDocument()
    expect(personalData('ci-robot')).toBeNull()
    expect(personalData('qa-lead-checkout')).toBeNull()
    expect(personalData('admin')).toBeNull()
    // The table's load asks nothing of the subject-rights API.
    expect(service.listUserJobs).not.toHaveBeenCalled()
    expect(service.getErasurePreview).not.toHaveBeenCalled()
  })

  it('an erased row: "Erased user" over the pseudonym, the badge, the role as text, Personal data only, no e-mail', () => {
    renderPage()
    const erased = rowOf('Erased user')
    expect(within(erased).getByText('erased-0a1b2c3d')).toBeInTheDocument()
    expect(within(erased).getByText(UI_SENTENCES.erased_badge)).toHaveAttribute('data-erased-badge')
    expect(within(erased).getByText('QA_ENGINEER').tagName).toBe('SPAN')
    expect(within(erased).queryByRole('combobox')).toBeNull()
    expect(within(erased).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Personal data for erased-0a1b2c3d'])
    expect(erased.textContent).not.toMatch(/erased\.invalid|Inactive|Deactivate|Activate/)
    expect(document.body.textContent).not.toContain('@erased.invalid')
  })

  it('the User cell truncates at 185 px, name and e-mail each with its full text as the title', () => {
    renderPage()
    const name = screen.getByText('Quinn Tester', { exact: true })
    const email = screen.getByText('quinn.tester@example.test', { exact: true })
    for (const [line, text] of [[name, 'Quinn Tester'], [email, 'quinn.tester@example.test']] as const) {
      expect(line.className).toContain('max-w-[185px]')
      expect(line.className).toContain('truncate')
      expect(line).toHaveAttribute('title', text)
    }
  })

  it('opening the section reads the jobs and the preview; an export starts for that person; closing stops the polling', async () => {
    const running = subjectJobFixture({ status: 'running', source: 'admin' })
    service.startUserExport.mockResolvedValue(running)
    service.getJob.mockResolvedValue(running)
    renderPage()
    fireEvent.click(personalData('quinn.tester') as HTMLElement)
    const section = await screen.findByRole('region', { name: 'Personal data for Quinn Tester' })
    expect(section.closest('td')).toHaveAttribute('colspan', '4')
    await waitFor(() => expect(service.getErasurePreview).toHaveBeenCalledWith('quinn-id'))
    expect(service.listUserJobs).toHaveBeenCalledWith('quinn-id')
    fireEvent.click(await within(section).findByRole('button', { name: "Export this person's data" }))
    await waitFor(() => expect(service.startUserExport).toHaveBeenCalledWith('quinn-id'))
    expect(await within(section).findByText('Preparing the archive…')).toBeInTheDocument()
    await waitFor(() => expect(service.getJob.mock.calls.length).toBeGreaterThanOrEqual(2))

    fireEvent.click(within(section).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Personal data for Quinn Tester' })).toBeNull()
    const polled = service.getJob.mock.calls.length
    await new Promise((resolve) => setTimeout(resolve, 250))
    expect(service.getJob.mock.calls.length).toBe(polled)
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()
  })

  it('an erased row\'s section: the outcome and its counts, read-only — no Export, no preview read', async () => {
    service.listUserJobs.mockResolvedValue(subjectJobList([subjectJobFixture({ kind: 'erasure', status: 'succeeded' })]))
    renderPage()
    fireEvent.click(personalData('erased-0a1b2c3d') as HTMLElement)
    const section = await screen.findByRole('region', { name: 'Personal data for Erased user' })
    expect(await within(section).findByText('Erased. This account now reads as “Erased user” (erased-0a1b2c3d).')).toBeInTheDocument()
    expect(within(section).getByText('project_members.user_id')).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: "Export this person's data" })).toBeNull()
    expect(within(section).queryByRole('button', { name: UI_SENTENCES.erase_button })).toBeNull()
    expect(service.getErasurePreview).not.toHaveBeenCalled()
  })
})
