import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ValueMetricsPage from './ValueMetricsPage'

vi.mock('@/services/valueMetricsService', () => ({
  valueMetricsService: {
    get: vi.fn(),
    exportUrl: vi.fn(() => '/api/v1/value-metrics/export?days=30'),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('ValueMetricsPage', () => {
  it('renders the value realization workflow strip once metrics load', async () => {
    const { valueMetricsService } = await import('@/services/valueMetricsService')

    ;(valueMetricsService.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      period_days: 30,
      project_id: 'proj-1',
      triage_time_saved_minutes: 360,
      triage_time_saved_hours: 6,
      defects_auto_grouped: 12,
      tests_grouped: 44,
      duplicate_tickets_avoided: 5,
      defects_promoted: 9,
      flaky_tests_identified: 3,
      quarantine_recommended: 2,
      risky_releases_blocked: 4,
      releases_conditional: 1,
      release_overrides: 0,
      intelligence_reports_generated: 7,
    })

    render(<ValueMetricsPage />)

    await waitFor(() => {
      expect(screen.getByText(/Value Realization Workflow/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/Value Capture/i)).toBeInTheDocument()
    expect(screen.getByText(/Value Metrics/i)).toBeInTheDocument()
  })
})
