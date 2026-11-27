/**
 * D-125: a new project becomes the active one, even when the list read it joined was already in flight.
 *
 * The store shares one in-flight list request between callers. One that started before the create
 * committed answers the OLD list: the new project was then missing from the picker, and a later refresh
 * dropped the selection. The page now reads the list once more when the new project is not in it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

// D-81: the New Project form reads offline mode; online here.
vi.mock('@/hooks/useAIConfig', () => ({ useAIConfig: () => ({ data: { ai_offline_mode: false } }) }))

import ProjectsPage from './ProjectsPage'
import { projectsService } from '@/services/projectsService'

const OLD = { id: 'p-pay', name: 'Payments', slug: 'payments', description: '', is_active: true, created_at: '2026-01-07T09:00:00Z', tags: [] }
const CREATED = { id: 'p-new', name: 'Checkout', slug: 'checkout', description: '', is_active: true, created_at: '2026-11-26T09:00:00Z', tags: [] }

async function createCheckout() {
  render(<ProjectsPage />)
  await screen.findByText(OLD.name)
  fireEvent.click(screen.getByRole('button', { name: /New Project/ }))
  fireEvent.change(screen.getByLabelText(/project name/i), { target: { value: 'Checkout' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Project' }))
}

describe('D-125: the new project is selected', () => {
  afterEach(() => {
    storeState.refreshProjects.mockReset()
    storeState.setActiveProject.mockReset()
  })

  it('a stale list (a read already in flight) is read again, then the new project is active and listed', async () => {
    vi.mocked(projectsService.create).mockResolvedValue(CREATED as never)
    storeState.refreshProjects
      .mockResolvedValueOnce([OLD])           // the page's first load
      .mockResolvedValueOnce([OLD])           // joined an in-flight read: the old list
      .mockResolvedValueOnce([OLD, CREATED])  // read again: fresh
    await createCheckout()
    await waitFor(() => expect(storeState.setActiveProject).toHaveBeenCalledWith(CREATED))
    expect(storeState.refreshProjects).toHaveBeenCalledTimes(3)
    expect(await screen.findByText(CREATED.name)).toBeInTheDocument()
  })

  it('a fresh list is not read twice', async () => {
    vi.mocked(projectsService.create).mockResolvedValue(CREATED as never)
    storeState.refreshProjects.mockResolvedValueOnce([OLD]).mockResolvedValueOnce([OLD, CREATED])
    await createCheckout()
    await waitFor(() => expect(storeState.setActiveProject).toHaveBeenCalledWith(CREATED))
    expect(storeState.refreshProjects).toHaveBeenCalledTimes(2)
  })
})
