import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'

vi.mock('@/services/searchService', () => ({
  searchService: {
    search:         vi.fn().mockResolvedValue({ items: [], total: 0 }),
    globalSearch:   vi.fn().mockResolvedValue({ groups: {}, total: 0 }),
    // SearchPage calls ``getIndexStatus`` on mount to render the index health
    // pill; the mock has to expose it or the page throws "is not a function".
    getIndexStatus: vi.fn().mockResolvedValue({ status: 'ok', documents: 0 }),
    reindex:        vi.fn().mockResolvedValue({ task_id: 't', status: 'queued' }),
    similar:        vi.fn().mockResolvedValue({ items: [], total: 0, query: '' }),
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

    // The workflow ribbon is identified by aria-label ("Search workflow"),
    // but its visible heading reads "Retrieval workflow" — match either.
    expect(
      (await screen.findAllByText(/(Search|Retrieval) workflow/i)).length,
    ).toBeGreaterThan(0)
    // Hero search input is the stable signal that controls rendered.
    // (The "Global" scope label was removed in the hero-first redesign.)
    // Hero placeholder text was rewritten in the redesign; match its leading
    // substring rather than the prior phrasing.
    expect(screen.getByPlaceholderText(/Search tests, runs, suites/i)).toBeInTheDocument()
  })
})
