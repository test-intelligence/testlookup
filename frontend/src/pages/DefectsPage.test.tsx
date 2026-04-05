import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import DefectsPage from './DefectsPage'

vi.mock('@/hooks/useMetrics', () => ({
  useDefects: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('DefectsPage', () => {
  it('renders the defect workflow strip above the defect table', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')

    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            id: 'def-1',
            test_name: 'payments should process a charge',
            suite_name: 'Payments',
            resolution_status: 'OPEN',
            ai_confidence_score: 84,
            created_at: '2026-04-01T10:00:00Z',
          },
        ],
        total: 1,
        pages: 1,
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Defect Workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/Defect Intake/i)).toBeInTheDocument()
    expect(screen.getByText(/Defects/i)).toBeInTheDocument()
  })
})
