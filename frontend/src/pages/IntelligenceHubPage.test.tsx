import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import IntelligenceHubPage from './IntelligenceHubPage'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

describe('IntelligenceHubPage', () => {
  it('renders the intelligence selection workflow strip', async () => {
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { id: 'run-1', build_number: '42', status: 'FAILED', failed_tests: 12, total_tests: 120, branch: 'main', project_name: 'Project One', created_at: '2026-04-03T15:00:00Z' },
          { id: 'run-2', build_number: '41', status: 'PASSED', failed_tests: 0, total_tests: 120, branch: 'main', project_name: 'Project One', created_at: '2026-04-02T15:00:00Z' },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes>
          <Route path="/intelligence" element={<IntelligenceHubPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Intelligence selection flow/i)).toBeInTheDocument()
    expect(screen.getByText(/Failed Runs/i)).toBeInTheDocument()
    expect(screen.getByText(/Recent Passing Runs/i)).toBeInTheDocument()
  })
})
