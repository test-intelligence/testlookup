import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'

vi.mock('@/services/searchService', () => ({
  searchService: {
    search: vi.fn(),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string }) => unknown) =>
    selector({ activeProjectId: 'proj-1' })),
}))

describe('SearchPage', () => {
  it('renders the search workflow strip and search controls', async () => {
    render(
      <MemoryRouter initialEntries={['/search']}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Search workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/Global/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Search test names/i)).toBeInTheDocument()
  })
})
