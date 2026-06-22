import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage from './DeepInvestigationPage'

vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: vi.fn(),
  useDeepFindings: vi.fn(),
  // Pipeline status moved from a load-on-mount effect to this SWR hook; the
  // mock must export it or the page throws on render.
  usePipelineStatus: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  // The page added a ``useRun`` fallback fetch (run-detail KPIs populate
  // even when the run isn't in the recent list). Mock must export it.
  useRun: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  // ``ALL_PROJECTS_ID`` is read at module-import time by several pages —
  // the mock must export it even when the test doesn't exercise All-Projects
  // mode, or vitest raises "No ALL_PROJECTS_ID export is defined".
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('DeepInvestigationPage', () => {
  it('renders the investigation workflow strip above the cluster view', async () => {
    const { useFailureClusters, useDeepFindings, usePipelineStatus } = await import('@/hooks/useDeepInvestigation')
    const { useRuns, useRun } = await import('@/hooks/useRuns')

    ;(usePipelineStatus as ReturnType<typeof vi.fn>).mockReturnValue({ data: null })

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ id: 'run-1' }] },
    })
    ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
    })
    ;(useFailureClusters as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          cluster_id: 'cl-1',
          label: 'DB Timeouts',
          representative_error: 'TimeoutError',
          member_test_ids: ['t1', 't2'],
          size: 2,
          cohesion_score: 0.88,
        },
      ],
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useDeepFindings as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          cluster_id: 'cl-1',
          root_cause: 'Connection pool exhaustion',
          failure_category: 'INFRASTRUCTURE',
          confidence_score: 91,
          causal_chain: [
            { step: 1, service: 'payments-api', finding: 'Pool exhausted under load' },
          ],
          evidence: [
            { source: 'log', excerpt: 'Pool exhausted' },
          ],
          affected_services: ['payments-api'],
          contract_violations: [],
          recommended_actions: ['Increase pool size'],
        },
      ],
      isLoading: false,
      mutate: vi.fn(),
    })

    render(
      <MemoryRouter initialEntries={['/deep-investigate/run-1']}>
        <Routes>
          <Route path="/deep-investigate/:runId" element={<DeepInvestigationPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Investigation Workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Failure Clustering/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Deep Investigation/i).length).toBeGreaterThan(0)
  })
})
