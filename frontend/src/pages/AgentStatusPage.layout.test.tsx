/**
 * `/agents` — "Pipeline runs" under Admin › AI (UX redesign P5, owner
 * decision D3): the page template, the AI report first and full width, the
 * agent stages collapsed.
 *
 * BUG-008 / TL-2026-09-18-01-010 (user request, 2026-09-18). The report is the
 * pipeline's headline output — which is why `showSummary` defaults to true —
 * but it rendered *below* the stage detail, so a reader scrolled past the
 * mechanism to reach the conclusion. P5 goes the rest of the way: the stages
 * are a collapsed Disclosure, and the report is no longer a column beside the
 * pipeline list but the page's full-width primary content.
 *
 * Order is asserted with `compareDocumentPosition`, not by "both are present":
 * the defect was purely about which came first, so a presence check would have
 * passed against it.
 */
import { useEffect } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(),
  usePipelineTimeline: vi.fn(),
  useRunSummary: vi.fn(),
  useActiveLiveRuns: vi.fn(),
}))

vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({
    data: { items: [{ id: 'run-recent', build_number: '9', run_seq: 9, primary_suite_name: 'Recent' }] },
    isLoading: false,
  })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: false })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const PIPELINE = {
  id: 'pipe-1',
  test_run_id: 'run-1',
  workflow_type: 'offline',
  status: 'completed',
  started_at: '2026-03-31T10:00:00Z',
  completed_at: '2026-03-31T10:01:00Z',
  error: null,
  created_at: '2026-03-31T10:00:00Z',
  run_seq: 41,
  build_number: '41',
  suite_name: 'AuthSuite',
}

const LIVE_RUN = {
  run_id: 'live-1', build_number: '77', run_seq: 77, suite_name: 'AuthSuite',
  started_at: '2026-03-31T10:00:00Z', current_test: 'test_login',
  total: 10, passed: 4, failed: 1, broken: 0, skipped: 0, pass_rate: 80,
}

let location = ''
function LocationProbe() {
  const { pathname, search } = useLocation()
  useEffect(() => {
    location = `${pathname}${search}`
  }, [pathname, search])
  return null
}

async function mockHooks({ liveRuns = [] as unknown[] } = {}) {
  const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
    await import('@/hooks/useAgentRuns')

  ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: liveRuns })
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({ data: [PIPELINE], isLoading: false })
  ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({
    data: [
      {
        stage_name: 'summary',
        status: 'completed',
        started_at: '2026-03-31T10:00:10Z',
        completed_at: '2026-03-31T10:00:20Z',
        result_data: { summary_length: 120 },
        error: null,
      },
    ],
    isLoading: false,
  })
  ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
  ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      test_run_id: 'run-1',
      project_id: 'proj-1',
      build_number: '42',
      executive_summary: 'The AI pipeline found a DB timeout regression.',
      markdown_report: '## Executive Summary\nThe AI pipeline found a DB timeout regression.',
      anomaly_count: 1,
      is_regression: true,
      analysis_count: 3,
      generated_at: '2026-03-31T10:01:00Z',
    },
    isLoading: false,
    error: undefined,
  })
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/agents" element={<><AgentStatusPage /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

/** A run on screen opens its newest pipeline: the report is there without a click. */
async function renderWithPipeline() {
  await mockHooks()
  const view = renderAt('/agents?run=run-1')
  await screen.findByRole('heading', { name: 'AI Report' })
  return view
}

/** True when `a` comes before `b` in document order. */
function precedes(a: Element, b: Element): boolean {
  // Node.DOCUMENT_POSITION_FOLLOWING === 4
  return (a.compareDocumentPosition(b) & 4) !== 0
}

const stagesToggle = () => screen.getByRole('button', { name: 'Agent stages' })

describe('Pipeline runs — the page template (P5, D3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    location = ''
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  it('is titled "Pipeline runs" in a compact header with its help topic', async () => {
    const { container } = await renderWithPipeline()
    expect(screen.getByRole('heading', { level: 1, name: 'Pipeline runs' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Agent Pipeline' })).toBeNull()
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Pipeline runs' })).toHaveAttribute('data-help-topic', 'ai-agents')
    // One secondary action: the sibling Workflow editor.
    expect(within(header).getByRole('link', { name: /Workflow editor/ })).toHaveAttribute('href', '/agents/workflows')
    expect(container.querySelectorAll('[data-page-header]')).toHaveLength(1)
  })

  it('has one run picker, and it shows the run on screen', async () => {
    await renderWithPipeline()
    const pickers = screen.getAllByRole('combobox')
    expect(pickers).toHaveLength(1)
    expect((pickers[0] as HTMLSelectElement).value).toBe('run-1')
  })

  it('names a ?run= that is older than the recent runs instead of reading "All recent pipelines"', async () => {
    // run-1 is not among the picker's recent runs (only run-recent is).
    await renderWithPipeline()
    const picker = screen.getByLabelText(/test suite & build/i) as HTMLSelectElement
    expect(picker.selectedOptions[0].textContent).toBe('AuthSuite · Run #41')
    expect(within(picker).getByRole('option', { name: 'Recent · Run #9' })).toBeInTheDocument()
  })

  it('puts the AI report, full width, as the primary content — not a column beside the list', async () => {
    const { container } = await renderWithPipeline()
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(primary).toHaveAttribute('aria-label', 'AI report')
    expect(within(primary).getByRole('heading', { name: 'AI Report' })).toBeInTheDocument()
    // A block of the page itself (a sibling of the header), not a grid column.
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(primary.parentElement).toBe(header.parentElement)
    // The old 1/3 + 2/3 split is gone.
    expect(container.querySelector('[class*="lg:grid-cols-3"]')).toBeNull()
    expect(container.querySelector('[class*="lg:col-span-2"]')).toBeNull()
    // The pipelines list sits above it, then the report, then the disclosures.
    const list = screen.getByRole('region', { name: 'Pipelines' })
    expect(precedes(list, primary)).toBe(true)
    for (const disclosure of container.querySelectorAll('[data-disclosure]')) {
      expect(precedes(primary, disclosure)).toBe(true)
    }
  })

  it('puts the AI report above Agent stages', async () => {
    await renderWithPipeline()
    expect(
      precedes(screen.getByRole('heading', { name: 'AI Report' }), stagesToggle()),
      'the AI report is the headline output and must come before the stage detail',
    ).toBe(true)
  })

  it('renders the report content itself above the stage detail, not just the heading', async () => {
    await renderWithPipeline()
    const summaryText = screen.getAllByText(/DB timeout regression/i)[0]
    expect(precedes(summaryText, stagesToggle()), 'the heading moved but the report body did not').toBe(true)
  })

  it('keeps the agent stages in a collapsed "Agent stages" disclosure that opens without hiding the report', async () => {
    await renderWithPipeline()
    // Collapsed on arrival: nothing of the mechanism is drawn.
    expect(stagesToggle()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Raw stage cards')).toBeNull()
    // The old hand-rolled toggle is gone.
    expect(screen.queryByRole('button', { name: /hide stages|show stages/i })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Agent Stages' })).toBeNull()

    fireEvent.click(stagesToggle())
    expect(stagesToggle()).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Raw stage cards')).toBeInTheDocument()
    // Opening the mechanism must not take the conclusion with it.
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'AI Report' })).toBeInTheDocument()

    fireEvent.click(stagesToggle())
    await waitFor(() => {
      expect(screen.queryByText('Raw stage cards')).toBeNull()
    })
  })

  // P2: the Live · Debug · Audit · Compare bar is gone (Audit and Compare were
  // "not built yet" placeholder text; Live pointed at the strip above).
  it('renders no Audit / Compare (or any) workflow-mode tabs', async () => {
    await renderWithPipeline()
    fireEvent.click(stagesToggle())
    expect(screen.queryByRole('tab', { name: /Audit/i })).toBeNull()
    expect(screen.queryByRole('tab', { name: /Compare/i })).toBeNull()
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByText(/coming in the next\s+iteration/i)).toBeNull()
  })

  // P2 item 3: the real-data timeline sits in a collapsed "Pipeline"
  // disclosure at the bottom of the report.
  it('puts the workflow timeline in a collapsed "Pipeline" disclosure below the stage detail', async () => {
    await renderWithPipeline()
    fireEvent.click(stagesToggle())
    const toggle = screen.getByRole('button', { name: 'Pipeline' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Workflow Progress')).toBeNull()
    expect(precedes(screen.getByText('Raw stage cards'), toggle)).toBe(true)

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Workflow Progress')).toBeInTheDocument()
  })

  it('still shows the report expanded by default', async () => {
    // Pinned by AgentStatusPage.test.tsx too; repeated here because the reorder
    // is exactly the kind of change that quietly flips a default.
    await renderWithPipeline()
    expect(screen.getByRole('button', { name: /hide report/i })).toBeInTheDocument()
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
  })

  it('lists the pipelines as one-line rows, the selected one pressed', async () => {
    await renderWithPipeline()
    const list = screen.getByRole('region', { name: 'Pipelines' })
    expect(within(list).getByRole('heading', { name: "This run's pipelines" })).toBeInTheDocument()
    const row = within(list).getByRole('button', { name: /offline pipeline/i })
    expect(row).toHaveAttribute('aria-pressed', 'true')
    expect(row).toHaveTextContent('Run #41')
    expect(row).toHaveTextContent('Duration: 60s')
  })

  it('opens a pipeline picked from the recent list on ITS run, so the picker and URL follow', async () => {
    await mockHooks()
    renderAt('/agents')
    const list = screen.getByRole('region', { name: 'Pipelines' })
    expect(within(list).getByRole('heading', { name: 'Recent pipelines' })).toBeInTheDocument()
    expect(screen.getByText(/Select a pipeline run to see agent stages/i)).toBeInTheDocument()

    fireEvent.click(within(list).getByRole('button', { name: /offline pipeline/i }))

    await waitFor(() => expect(location).toBe('/agents?run=run-1'))
    expect((screen.getByLabelText(/test suite & build/i) as HTMLSelectElement).value).toBe('run-1')
    expect(screen.getByRole('heading', { name: 'AI Report' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Pipelines' })).getByRole('button', { name: /offline pipeline/i }))
      .toHaveAttribute('aria-pressed', 'true')
  })

  it('collapses live executions into a disclosure after the report', async () => {
    await mockHooks({ liveRuns: [LIVE_RUN] })
    const { container } = renderAt('/agents?run=run-1')
    await screen.findByRole('heading', { name: 'AI Report' })
    const toggle = screen.getByRole('button', { name: /^Live executions/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('1 running')
    expect(screen.queryByText('LIVE')).toBeNull()
    expect(precedes(container.querySelector('[data-primary]') as HTMLElement, toggle)).toBe(true)

    fireEvent.click(toggle)
    expect(screen.getByText('LIVE')).toBeInTheDocument()
    expect(screen.getByText('Run #77')).toBeInTheDocument()
  })

  it('shows no live-executions section when nothing is streaming', async () => {
    await renderWithPipeline()
    expect(screen.queryByRole('button', { name: /^Live executions/ })).toBeNull()
  })
})
