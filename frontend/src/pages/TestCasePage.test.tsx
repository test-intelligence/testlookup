import { render, screen, within } from '@testing-library/react'
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
  useTestStepFlips: vi.fn(() => ({ data: undefined, isLoading: false })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('@/components/ai/AIAnalysisPanel', () => ({
  default: () => <div data-testid="ai-panel" />,
}))

const TEST_CASE = {
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
}

async function mockCase(data: unknown) {
  const { useTestCase } = await import('@/hooks/useRuns')
  ;(useTestCase as ReturnType<typeof vi.fn>).mockReturnValue({ data, isLoading: false })
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/runs/run-12345678/tests/test-1']}>
      <Routes>
        <Route path="/runs" element={<div>Runs List</div>} />
        <Route path="/runs/:runId/tests/:testId" element={<TestCasePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TestCasePage — the page template (P4)', () => {
  it('trail, one header, then the body: the answer (data-primary) before any tab bar', async () => {
    await mockCase(TEST_CASE)
    renderPage()
    const trail = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(trail).getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '/runs')
    expect(within(trail).getByRole('link', { name: '#run-1234' })).toHaveAttribute('href', '/runs/run-12345678')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Login should work' })).toBeInTheDocument()
    expect(screen.getByText('com.example.LoginTest.loginShouldWork')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Help: Login should work' })).toHaveAttribute('data-help-topic', 'ingestion')
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(header).toHaveAttribute('data-compact', 'true')
    const primary = document.querySelector('[data-primary]') as HTMLElement
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    expect(header.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    for (const tablist of document.querySelectorAll('[role="tablist"]')) {
      expect(primary.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(within(primary).getByTestId('ai-panel')).toBeInTheDocument()
    // Not the hard-coded `<Class>.java` trace title (§5 "Delete").
    expect(screen.queryByText('LoginTest.java')).toBeNull()
  })

  it('a missing test case says so', async () => {
    await mockCase(undefined)
    renderPage()
    expect(screen.getByText('Test case not found')).toBeInTheDocument()
  })

  it('returns to the runs list when the project changes', async () => {
    await mockCase(TEST_CASE)
    const { rerender } = renderPage()

    expect(await screen.findByRole('heading', { name: /Login should work/i })).toBeInTheDocument()

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/runs/run-12345678/tests/test-1']}>
        <Routes>
          <Route path="/runs" element={<div>Runs List</div>} />
          <Route path="/runs/:runId/tests/:testId" element={<TestCasePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Runs List/i)).toBeInTheDocument()
  })
})
