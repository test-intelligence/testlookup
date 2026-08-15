import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import IntelligenceHubPage from './IntelligenceHubPage'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/store/projectStore', () => {
  const ALL_PROJECTS_ID = 'all'
  return {
    ALL_PROJECTS_ID,
    useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) =>
      selector({ activeProjectId: ALL_PROJECTS_ID }),
  }
})

describe('IntelligenceHubPage', () => {
  it('renders the verdict cockpit and runs table with the new design', async () => {
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { id: 'run-1', build_number: '42', status: 'PASSED', failed_tests: 0,  passed_tests: 120, broken_tests: 0, skipped_tests: 0, total_tests: 120, pass_rate: 100, branch: 'main', project_name: 'Project One', created_at: '2026-04-03T15:00:00Z', duration_ms: 492000 },
          { id: 'run-2', build_number: '41', status: 'PASSED', failed_tests: 0,  passed_tests: 119, broken_tests: 1, skipped_tests: 0, total_tests: 120, pass_rate: 99.2,  branch: 'main', project_name: 'Project One', created_at: '2026-04-02T15:00:00Z', duration_ms: 664000 },
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

    // Header
    expect(await screen.findByText(/Run Intelligence/i)).toBeInTheDocument()
    // Verdict eyebrow
    expect(screen.getByText(/Pipeline health/i)).toBeInTheDocument()
    // Composite health number is rendered
    expect(screen.getByText('/100')).toBeInTheDocument()
    // Runs table is present with the redesigned title
    expect(screen.getByText(/Recent runs analyzed/i)).toBeInTheDocument()
    // Specific run rows show up — they appear both in the table cell and in
    // the activity feed, so getAllByText is the right query.
    expect(screen.getAllByText('#42').length).toBeGreaterThan(0)
    expect(screen.getAllByText('#41').length).toBeGreaterThan(0)
    // Spend panel is per-project (real /llm-usage meter) — in All-Projects
    // mode it asks the user to narrow scope instead of faking numbers.
    expect(screen.getByText(/Select a project to see its LLM spend/i)).toBeInTheDocument()
  })

  // ── US-15.1 honesty fix ────────────────────────────────────────────────
  it('no longer labels the pass-rate column an "AI confidence"', async () => {
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { id: 'run-1', build_number: '42', status: 'PASSED', failed_tests: 0, passed_tests: 120, broken_tests: 0, skipped_tests: 0, total_tests: 120, pass_rate: 100, branch: 'main', project_name: 'Project One', created_at: '2026-04-03T15:00:00Z', duration_ms: 492000 },
          // No pass_rate at all: the old code invented 55 for a failed run.
          { id: 'run-2', build_number: '41', status: 'FAILED', failed_tests: 3, passed_tests: 117, broken_tests: 0, skipped_tests: 0, total_tests: 120, pass_rate: null, branch: 'main', project_name: 'Project One', created_at: '2026-04-02T15:00:00Z', duration_ms: 664000 },
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

    await screen.findByText(/Recent runs analyzed/i)
    expect(screen.queryByText(/AI confidence/i)).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Pass rate/i })).toBeInTheDocument()
    // The run with no pass_rate shows an em dash, not a fabricated figure.
    expect(screen.queryByText('55%')).not.toBeInTheDocument()
  })

  // ── An empty window must never read as a verdict ──────────────────────────
  //
  // Reported as "the entire AI intelligence page is broken". The runs were 8-10
  // days old and the default window is 7 days, so the window was empty — but
  // the page did not say that. `failed` was 0, so the verdict fell through to
  // "All clear - nothing needs investigation right now" for a project whose
  // most recent run had three failing tests, and composite health rendered
  // 0/100 as though it had been measured.
  //
  // An empty window is a statement about the WINDOW, never about the code.

  async function renderEmptyWindow(mostRecent?: unknown) {
    const { useRuns, useMostRecentRun } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [] },
      isLoading: false,
    })
    ;(useMostRecentRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mostRecent ? { items: [mostRecent] } : undefined,
    })
    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes>
          <Route path="/intelligence" element={<IntelligenceHubPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('never claims "All clear" when nothing was analysed', async () => {
    await renderEmptyWindow()
    expect(screen.queryByText(/all clear/i)).not.toBeInTheDocument()
    expect(await screen.findByText(/nothing analysed in this window/i)).toBeInTheDocument()
  })

  it('says the emptiness is about the window, not the code', async () => {
    await renderEmptyWindow()
    expect(
      await screen.findByText(/statement about the time window, not about the code/i),
    ).toBeInTheDocument()
  })

  it('does not render a composite health score it never measured', async () => {
    // 0/100 from an empty window is a fabricated measurement, and it reads as
    // "measured, and terrible" rather than "not measured".
    await renderEmptyWindow()
    expect(await screen.findByText('—')).toBeInTheDocument()
    expect(screen.queryByText('/100')).not.toBeInTheDocument()
  })

  it('tells the reader how far back the data actually is', async () => {
    await renderEmptyWindow({
      id: 'old-1',
      build_number: '9',
      status: 'FAILED',
      created_at: '2026-08-05T10:00:00Z',
      failed_tests: 3,
      passed_tests: 12,
      broken_tests: 0,
      skipped_tests: 0,
      total_tests: 15,
      pass_rate: 80,
      branch: 'main',
      project_name: 'Auth Service',
      duration_ms: 1000,
    })
    expect(await screen.findByText(/the most recent one is/i)).toBeInTheDocument()
    expect(screen.getByText(/just outside the selected range/i)).toBeInTheDocument()
  })

  it('falls back to the generic message when there is no history at all', async () => {
    // A brand-new project genuinely has nothing — do not imply otherwise.
    await renderEmptyWindow()
    expect(await screen.findByText('No runs in this window')).toBeInTheDocument()
  })
})
