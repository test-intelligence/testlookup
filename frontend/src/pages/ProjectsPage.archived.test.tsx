/**
 * W17 ADM-04: an administrator finds an archived project and restores it.
 *
 * Archive hid a project with nothing to bring it back. /projects now has an
 * Active / Archived switch for an administrator; Archived lists each project
 * with "Archived on <day> by <name>" (or "date not recorded" for one archived
 * before migration 0232) and Restore, which asks in the page (saying the API
 * keys stay revoked) and only its own button sends.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({
  listWithArchived: vi.fn(),
  restore: vi.fn(),
  refreshProjects: vi.fn(async () => []),
  toastSuccess: vi.fn(),
  perms: { isAdmin: true, isQaLead: true },
}))

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list: vi.fn(async () => []),
    create: vi.fn(async () => ({})),
    update: vi.fn(async () => ({})),
    delete: vi.fn(async () => ({})),
    listWithArchived: (...a: unknown[]) => svc.listWithArchived(...a),
    restore: (...a: unknown[]) => svc.restore(...a),
  },
}))
const storeState = {
  setActiveProject: vi.fn(),
  refreshProjects: svc.refreshProjects,
  activeProjectId: null,
  activeProject: null,
  projects: [],
}
vi.mock('@/store/projectStore', () => ({
  useProjectStore: (selector?: (s: typeof storeState) => unknown) => (selector ? selector(storeState) : storeState),
  ALL_PROJECTS_ID: 'all',
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => svc.perms }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: (...a: unknown[]) => svc.toastSuccess(...a), error: vi.fn() }),
}))

import ProjectsPage from './ProjectsPage'

const base = { description: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
const CHECKOUT = {
  ...base, id: 'p-1', name: 'Checkout Service', slug: 'checkout-service', is_active: false,
  archived_at: '2026-10-20T10:00:00Z', archived_by_name: 'Ada Admin',
}
const OLD = { ...base, id: 'p-2', name: 'Legacy', slug: 'legacy', is_active: false, archived_at: null }
const LIVE = { ...base, id: 'p-3', name: 'Payments', slug: 'payments', is_active: true }

beforeEach(() => {
  svc.perms = { isAdmin: true, isQaLead: true }
  svc.listWithArchived.mockReset().mockResolvedValue([CHECKOUT, OLD, LIVE])
  svc.restore.mockReset().mockResolvedValue({ restored: true, project_id: 'p-1', api_keys_still_revoked: 2, default_qa_lead_reactivated: false })
  svc.refreshProjects.mockClear()
  svc.toastSuccess.mockReset()
})

describe('ProjectsPage: archived projects (ADM-04)', () => {
  it('lists only the archived projects with when and by whom', async () => {
    render(<ProjectsPage />)
    expect(svc.listWithArchived).not.toHaveBeenCalled() // nothing asked until Archived is picked
    fireEvent.click(screen.getByRole('button', { name: 'Archived' }))
    const grid = await screen.findByTestId('archived-projects')
    expect(within(grid).getByText('Checkout Service')).toBeInTheDocument()
    expect(within(grid).getByText(/Archived on Oct 20, 2026 by Ada Admin/)).toBeInTheDocument()
    expect(within(grid).getByText('Archived (date not recorded)')).toBeInTheDocument()
    expect(within(grid).queryByText('Payments')).toBeNull()
  })

  it('Restore asks in the page, sends once from its own button, says the keys stay revoked, re-reads the switcher', async () => {
    render(<ProjectsPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Archived' }))
    const grid = await screen.findByTestId('archived-projects')
    fireEvent.click(within(grid).getAllByRole('button', { name: 'Restore' })[0])
    expect(svc.restore).not.toHaveBeenCalled()
    expect(screen.getByText(/Its API keys stay revoked/)).toBeInTheDocument()
    const confirm = screen.getAllByRole('button', { name: 'Restore' }).find(b => !grid.querySelector('.card')?.contains(b) && b.closest('[data-in-page-row]'))
    fireEvent.click(confirm as HTMLElement)
    await waitFor(() => expect(svc.restore).toHaveBeenCalledWith('p-1'))
    expect(svc.restore).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(svc.toastSuccess).toHaveBeenCalledWith('Checkout Service restored. 2 API keys stay revoked.'))
    expect(svc.refreshProjects.mock.calls.length).toBeGreaterThanOrEqual(2) // the page load + after the restore
  })

  it('a QA lead has no Archived switch', () => {
    svc.perms = { isAdmin: false, isQaLead: true }
    render(<ProjectsPage />)
    expect(screen.queryByRole('button', { name: 'Archived' })).toBeNull()
  })
})
