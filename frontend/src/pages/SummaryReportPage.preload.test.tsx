/**
 * Summary report: the catalogue chunk (the page's first chart code) is asked
 * for on mount, while the report is still loading (R1 (a)). Before this,
 * `lazy()` first asked for it when the page rendered the report, and the
 * charts started their download only then.
 *
 * The section module's factory counts its imports (only the FIRST import in a
 * file is reliable to count this way).
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.fn()
// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: { get: (...args: unknown[]) => mockGet(...args), downloadPdf: vi.fn() },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ activeProjectId: 'p1', activeProject: { id: 'p1', name: 'GoogleProject' } }),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const chunk = vi.hoisted(() => ({ asked: 0 }))
vi.mock('@/components/reports/catalogue/SummaryCatalogue', () => {
  chunk.asked += 1
  return { default: () => null }
})

import SummaryReportPage from './SummaryReportPage'

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/reports/summary']}>
        <SummaryReportPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

afterEach(() => {
  mockGet.mockReset()
})

describe('SummaryReportPage — the catalogue chunk is preloaded on mount', () => {
  it('asked while the report is still loading', async () => {
    mockGet.mockReturnValue(new Promise(() => {}))
    renderPage()
    await waitFor(() => expect(chunk.asked).toBe(1))
    // Still the page's loading state: nothing has rendered the section.
    expect(screen.queryByText('Total tests')).toBeNull()
  })
})
