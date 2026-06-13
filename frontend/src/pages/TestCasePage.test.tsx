import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import TestCasePage from './TestCasePage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useRuns', () => ({
  useTestCase: vi.fn(),
  useTestSteps: vi.fn(() => ({ data: undefined, isLoading: false })),
  useTestCaseHistory: vi.fn(() => ({ data: undefined, isLoading: false })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

describe('TestCasePage', () => {
  it('returns to the runs list when the project changes', async () => {
    const { useTestCase } = await import('@/hooks/useRuns')

    ;(useTestCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'test-1',
        test_name: 'Login should work',
        full_name: 'com.example.LoginTest.loginShouldWork',
        class_name: 'LoginTest',
        status: 'FAILED',
        suite_name: 'AuthSuite',
        duration_ms: 1200,
        severity: 'HIGH',
        feature: 'Auth',
        owner: 'QA Team',
        created_at: '2026-03-31T10:00:00Z',
        error_message: 'AssertionError: expected 200',
        tags: [],
        has_attachments: false,
      },
      isLoading: false,
    })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/runs/run-1/tests/test-1']}>
        <Routes>
          <Route path="/runs" element={<div>Runs List</div>} />
          <Route path="/runs/:runId/tests/:testId" element={<TestCasePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: /Login should work/i })).toBeInTheDocument()

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/runs/run-1/tests/test-1']}>
        <Routes>
          <Route path="/runs" element={<div>Runs List</div>} />
          <Route path="/runs/:runId/tests/:testId" element={<TestCasePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Runs List/i)).toBeInTheDocument()
  })
})
