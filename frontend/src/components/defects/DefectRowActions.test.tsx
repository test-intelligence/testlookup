/**
 * W06 DEF-01 §2.2-11/12 (dialog half): the row's "Defect actions" menu is a
 * QA engineer's (and above): a viewer and a tester get no menu, and a row with
 * no project to change it in gets none either. A successful change hands the
 * server's row to `onUpdated` exactly once.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DefectItem } from '@/types/analytics'

const state = vi.hoisted(() => ({ role: 'QA_ENGINEER' as string }))

// The REAL usePermissions, driven by the signed-in user's role.
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (s: { user: { role: string } }) => unknown) => selector({ user: { role: state.role } }),
}))

const updateDefect = vi.hoisted(() => vi.fn())
vi.mock('@/services/defectService', () => ({ updateDefect }))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: () => ({ metadata: undefined, isLoading: true, isError: false }),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import DefectRowActions from './DefectRowActions'

const ROW: DefectItem = {
  id: 'd-1',
  project_id: 'p-1',
  title: 'Checkout fails',
  resolution_status: 'OPEN',
  created_at: '2026-10-01T00:00:00Z',
  test_name: 'test_checkout',
}

function renderActions(defect: DefectItem = ROW, onUpdated = vi.fn()) {
  render(
    <MemoryRouter>
      <DefectRowActions defect={defect} onUpdated={onUpdated} />
    </MemoryRouter>,
  )
  return onUpdated
}

const menuButton = () => screen.queryByRole('button', { name: 'Defect actions' })
const menuItems = () => screen.getAllByRole('menuitem').map((item) => item.textContent)

beforeEach(() => {
  state.role = 'QA_ENGINEER'
  updateDefect.mockReset()
})

describe('DefectRowActions', () => {
  it.each(['VIEWER', 'TESTER'])('renders nothing for a %s', (role) => {
    state.role = role
    renderActions()
    expect(menuButton()).toBeNull()
  })

  it.each(['QA_ENGINEER', 'QA_LEAD', 'ADMIN'])('offers the three actions to a %s', (role) => {
    state.role = role
    renderActions()
    fireEvent.click(menuButton() as HTMLElement)
    expect(menuItems()).toEqual(['Change status…', 'Assign…', 'Link Jira issue…'])
  })

  it('says "Change Jira issue…" once the defect is linked', () => {
    renderActions({ ...ROW, jira_ticket_id: 'ABC-1' })
    fireEvent.click(menuButton() as HTMLElement)
    expect(menuItems()).toEqual(['Change status…', 'Assign…', 'Change Jira issue…'])
  })

  it('renders nothing for a row without its project (an older payload)', () => {
    renderActions({ ...ROW, project_id: undefined })
    expect(menuButton()).toBeNull()
  })

  it('opens each dialog from its item', () => {
    renderActions()
    fireEvent.click(menuButton() as HTMLElement)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Assign…' }))
    expect(screen.getByRole('dialog', { name: 'Assign defect' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(menuButton() as HTMLElement)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Link Jira issue…' }))
    expect(screen.getByRole('dialog', { name: 'Link a Jira issue' })).toBeInTheDocument()
  })

  it("hands the server's row to onUpdated once after a status change", async () => {
    const serverRow: DefectItem = { ...ROW, resolution_status: 'IN_PROGRESS' }
    updateDefect.mockResolvedValue({ defect: serverRow, changed_fields: ['resolution_status'], jira: null, write_back: null })
    const onUpdated = renderActions()
    fireEvent.click(menuButton() as HTMLElement)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Change status…' }))
    fireEvent.click(screen.getByRole('radio', { name: 'In progress' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    })
    expect(updateDefect).toHaveBeenCalledTimes(1)
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { resolution_status: 'IN_PROGRESS' }, { suppressToast: true })
    expect(onUpdated).toHaveBeenCalledTimes(1)
    expect(onUpdated).toHaveBeenCalledWith(serverRow)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
