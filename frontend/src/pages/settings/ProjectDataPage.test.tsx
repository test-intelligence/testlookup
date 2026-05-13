/**
 * Tests for ProjectDataPage — the ADMIN-only reset page.
 *
 * Focus areas (the safety invariants the user asked for):
 *   1. Non-ADMIN users see no controls.
 *   2. The danger-zone buttons are disabled in All-Projects mode.
 *   3. The confirm modal's Delete button stays disabled until the user
 *      types the project name verbatim.
 *   4. A confirmed reset POSTs to projectsService.reset with the right
 *      payload.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import ProjectDataPage from './ProjectDataPage'

// Mock the service so no real network call happens; per-test handles
// let us inject success / error / inspect the call.
const mockReset = vi.fn()
vi.mock('@/services/projectsService', () => ({
  projectsService: {
    reset: (...args: unknown[]) => mockReset(...args),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const mockActiveProject = {
  id: 'proj-uuid-1',
  name: 'GoogleSearch',
}

const mockPermissions = { isAdmin: true }
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => mockPermissions,
}))

const mockProjectStore = {
  activeProject: mockActiveProject as { id: string; name: string } | null,
  activeProjectId: 'proj-uuid-1' as string,
}
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (
    selector: (s: { activeProject: unknown; activeProjectId: string }) => unknown,
  ) => selector(mockProjectStore),
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectDataPage />
    </MemoryRouter>,
  )
}

describe('ProjectDataPage', () => {
  beforeEach(() => {
    mockReset.mockReset()
    mockPermissions.isAdmin = true
    mockProjectStore.activeProject = { ...mockActiveProject }
    mockProjectStore.activeProjectId = 'proj-uuid-1'
  })

  it('renders an access-denied empty state when the user is not ADMIN', () => {
    mockPermissions.isAdmin = false
    renderPage()
    expect(screen.getByText(/admin access required/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete test runs/i })).toBeNull()
  })

  it('disables both danger buttons when in All-Projects mode', () => {
    mockProjectStore.activeProject = null
    mockProjectStore.activeProjectId = 'all'
    renderPage()

    const runsBtn = screen.getByRole('button', { name: /delete test runs/i })
    const fullBtn = screen.getByRole('button', { name: /wipe project data/i })
    expect(runsBtn).toBeDisabled()
    expect(fullBtn).toBeDisabled()
    expect(
      screen.getByText(/select a specific project from the picker/i),
    ).toBeInTheDocument()
  })

  it('keeps the confirm button disabled until the project name is typed exactly', () => {
    renderPage()

    // Open the modal via the runs button.
    fireEvent.click(screen.getByRole('button', { name: /delete test runs/i }))

    // The modal's Delete button is rendered with the same accessible label
    // as the row button — disambiguate by finding the one with aria-disabled.
    const input = screen.getByLabelText(/type .* to confirm/i)
    const buttons = screen
      .getAllByRole('button', { name: /delete test runs/i })
      .filter((b) => b.getAttribute('aria-disabled') !== null)
    expect(buttons.length).toBeGreaterThan(0)
    const confirmBtn = buttons[0]

    expect(confirmBtn).toBeDisabled()

    // Wrong case — must reject.
    fireEvent.change(input, { target: { value: 'googlesearch' } })
    expect(confirmBtn).toBeDisabled()

    // Exact match enables the button.
    fireEvent.change(input, { target: { value: 'GoogleSearch' } })
    expect(confirmBtn).not.toBeDisabled()
  })

  it('calls projectsService.reset with the right payload on confirm', async () => {
    mockReset.mockResolvedValue({
      mode: 'runs',
      deleted: { test_runs: 5 },
    })
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /delete test runs/i }))
    fireEvent.change(screen.getByLabelText(/type .* to confirm/i), {
      target: { value: 'GoogleSearch' },
    })

    const confirmBtn = screen
      .getAllByRole('button', { name: /delete test runs/i })
      .filter((b) => b.getAttribute('aria-disabled') !== null)[0]
    fireEvent.click(confirmBtn)

    await waitFor(() => expect(mockReset).toHaveBeenCalled())
    expect(mockReset).toHaveBeenCalledWith('proj-uuid-1', {
      mode: 'runs',
      confirmation_name: 'GoogleSearch',
    })

    // After success the page shows the deleted counts.
    expect(await screen.findByText(/reset complete \(runs\)/i)).toBeInTheDocument()
    expect(screen.getByText(/test_runs/)).toBeInTheDocument()
  })
})
