import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OnboardingPage from './OnboardingPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' } as { id: string; name: string } | null,
  },
}))

vi.mock('@/services/onboardingService', () => ({
  onboardingService: {
    detectProgress: vi.fn(),
    skipStep: vi.fn(),
    restoreStep: vi.fn(),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('OnboardingPage', () => {
  it('renders the activation workflow and onboarding steps', async () => {
    const { onboardingService } = await import('@/services/onboardingService')

    ;(onboardingService.detectProgress as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_id: 'proj-1',
      steps: [
        {
          key: 'create_project',
          label: 'Create Project',
          description: 'Add a project',
          status: 'completed',
          completed_at: '2026-04-03T15:00:00Z',
        },
        {
          key: 'upload_run',
          label: 'Upload Run',
          description: 'Load a run',
          status: 'pending',
          completed_at: null,
        },
      ],
      completed_count: 1,
      total_count: 2,
      progress_pct: 50,
      is_complete: false,
    })

    render(
      <MemoryRouter initialEntries={['/getting-started']}>
        <Routes>
          <Route path="/getting-started" element={<OnboardingPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Activation workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Create Project/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Upload Run/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Setup Progress/i)).toBeInTheDocument()
  })

  it('offers a Restore control for a skipped step and calls restoreStep', async () => {
    // A skip used to be a dead end — the card dimmed to opacity-50 with no
    // control. A self-hoster who skipped by accident must be able to reopen it.
    // A distinct project id keeps this case out of SWR's per-project cache,
    // so the skipped-step status below is what the page actually renders.
    mockProjectState.activeProjectId = 'proj-restore'
    mockProjectState.activeProject = { id: 'proj-restore', name: 'Restore Project' }

    const { onboardingService } = await import('@/services/onboardingService')

    ;(onboardingService.detectProgress as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_id: 'proj-restore',
      steps: [
        {
          key: 'connect_jira',
          label: 'Connect Jira',
          description: 'Link Jira',
          status: 'skipped',
          completed_at: null,
        },
      ],
      completed_count: 1,
      total_count: 1,
      progress_pct: 100,
      is_complete: true,
    })
    const restoreStep = onboardingService.restoreStep as ReturnType<typeof vi.fn>
    restoreStep.mockClear()
    restoreStep.mockResolvedValue({
      project_id: 'proj-restore',
      steps: [
        {
          key: 'connect_jira',
          label: 'Connect Jira',
          description: 'Link Jira',
          status: 'pending',
          completed_at: null,
        },
      ],
      completed_count: 0,
      total_count: 1,
      progress_pct: 0,
      is_complete: false,
    })

    render(
      <MemoryRouter initialEntries={['/getting-started']}>
        <Routes>
          <Route path="/getting-started" element={<OnboardingPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const restore = await screen.findByRole('button', { name: /restore/i })
    fireEvent.click(restore)
    await waitFor(() =>
      expect(restoreStep).toHaveBeenCalledWith('proj-restore', 'connect_jira'),
    )
  })

  it('shows workspace onboarding when All Projects is selected', async () => {
    const { onboardingService } = await import('@/services/onboardingService')

    mockProjectState.activeProjectId = '__ALL__'
    mockProjectState.activeProject = null

    const detectProgress = onboardingService.detectProgress as ReturnType<typeof vi.fn>
    detectProgress.mockClear()

    render(
      <MemoryRouter initialEntries={['/getting-started']}>
        <Routes>
          <Route path="/getting-started" element={<OnboardingPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getAllByText(/Workspace onboarding/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Create Project/i).length).toBeGreaterThan(0)
    expect(detectProgress).not.toHaveBeenCalled()
  })
})
