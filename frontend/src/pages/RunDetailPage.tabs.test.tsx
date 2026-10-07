/**
 * UX redesign P4: the Run page (`/runs/:runId`) is one page with four tabs —
 * Tests (default) · Analysis · Changes · Evidence — and one PageHeader whose
 * ⋯ menu carries every action. These tests hold:
 *
 *  - the layout: the page's tab bar, then the primary content (the test
 *    table) with nothing between but the status chips, and no Disclosure
 *    above it;
 *  - `?tab=` selects each tab and renders its section — and a tab that is
 *    not open is not rendered and asks for nothing (its hooks are not called
 *    with the run);
 *  - the Tests tab: the counts as chips that filter, failures first;
 *  - the header: the overflow's actions, and no link to an old URL
 *    (`/runs/:id/intelligence`, `/deep-investigate/:id`, `/agents/run/:id`)
 *    anywhere on the page.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RunDetailPage from './RunDetailPage'

const { mockProjectState, hooks } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
  hooks: {
    useRun: vi.fn(),
    useRuns: vi.fn(),
    useTestCases: vi.fn(),
    useRunAttribution: vi.fn(),
    useRunIntelligence: vi.fn(),
    useDecisionReportVersions: vi.fn(),
    useFailureClusters: vi.fn(),
    useDeepFindings: vi.fn(),
    usePipelineStatus: vi.fn(),
    usePipelines: vi.fn(),
    usePipelineStages: vi.fn(),
    usePipelineTimeline: vi.fn(),
    useRunSummary: vi.fn(),
    useSWR: vi.fn(),
  },
}))

vi.mock('@/hooks/useRuns', () => ({
  useRun: hooks.useRun,
  useRuns: hooks.useRuns,
  useTestCases: hooks.useTestCases,
  useRunAttribution: hooks.useRunAttribution,
}))
vi.mock('@/hooks/useRunIntelligence', () => ({
  useRunIntelligence: hooks.useRunIntelligence,
  useDecisionReportVersions: hooks.useDecisionReportVersions,
}))
vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: hooks.useFailureClusters,
  useDeepFindings: hooks.useDeepFindings,
  usePipelineStatus: hooks.usePipelineStatus,
}))
vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: hooks.usePipelines,
  usePipelineStages: hooks.usePipelineStages,
  usePipelineTimeline: hooks.usePipelineTimeline,
  useRunSummary: hooks.useRunSummary,
  useActiveLiveRuns: vi.fn(() => ({ data: [] })),
}))
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'rules' } })),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: true })),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) => selector(mockProjectState)),
}))
vi.mock('@/services/runsService', () => ({
  runsService: { setRelease: vi.fn(), get: vi.fn(), list: vi.fn(), listTests: vi.fn(), getTest: vi.fn() },
}))
vi.mock('@/services/onboardingService', () => ({
  onboardingService: { completeStep: vi.fn(() => Promise.resolve()) },
}))
vi.mock('swr', () => ({ default: hooks.useSWR, mutate: vi.fn() }))

const RUN = {
  id: 'run-1',
  build_number: '42',
  jenkins_job: 'checkout-ci',
  branch: 'main',
  created_at: '2026-03-31T10:00:00Z',
  release_name: null,
  passed_tests: 3,
  failed_tests: 2,
  broken_tests: 1,
  skipped_tests: 1,
  unknown_tests: 0,
  total_tests: 7,
  pass_rate: 50,
  status: 'FAILED',
  primary_suite_name: 'Checkout',
  suite_names: ['Checkout'],
}

const TESTS = [
  // Deliberately NOT failures-first: the page must put them first itself.
  { id: 'tc-a', test_name: 'test_a_pass', suite_name: 'Checkout', status: 'PASSED', duration_ms: 10 },
  { id: 'tc-b', test_name: 'test_b_fail', suite_name: 'Checkout', status: 'FAILED', duration_ms: 10 },
  { id: 'tc-c', test_name: 'test_c_skip', suite_name: 'Checkout', status: 'SKIPPED', duration_ms: 10 },
  { id: 'tc-d', test_name: 'test_d_broken', suite_name: 'Checkout', status: 'BROKEN', duration_ms: 10 },
  { id: 'tc-e', test_name: 'test_e_fail', suite_name: 'Checkout', status: 'FAILED', duration_ms: 10 },
]

const INTELLIGENCE = {
  intelligence_available: true,
  run: { id: 'run-1', build_number: '42', status: 'FAILED', total_tests: 7, passed_tests: 3, failed_tests: 2, broken_tests: 1, skipped_tests: 1, pass_rate: 50 },
  structured_summary: null,
  failure_clusters: [
    { id: 'row-1', cluster_id: 'cl-1', label: 'Gateway timeouts', size: 2, representative_error: 'TimeoutError', member_test_ids: ['tc-b', 'tc-e'], cohesion_score: 0.9, criticality_level: 'HIGH', dimension_scores: [] },
  ],
  category_breakdown: { PRODUCT_BUG: 2 },
  affected_suites: [{ suite: 'Checkout', failed_count: 3 }],
  top_analyses: [],
  avg_confidence: 0.8,
  release_decision: { recommendation: 'NO_GO', risk_score: 70, composite_risk: 70, reasoning: 'Checkout is broken.', blocking_issues: [], conditions_for_go: [] },
  role_actions: {},
  pipeline_stages: [],
  all_green: false,
  dimension_scores: [],
  what_changed_since_last_good_run: null,
  defect_candidates: [],
  summary_modes: null,
  provenance: null,
  partial_errors: null,
}

const DIFF = {
  baseline_available: true,
  baseline_run_id: 'run-0',
  baseline_build_number: '40',
  pass_rate_delta: -12.5,
  new_failing_tests: [{ test_name: 'test_b_fail', suite_name: 'Checkout' }],
  new_failing_count: 1,
  resolved_count: 0,
  commit_range: [{ sha: 'abc1234', message: 'Change the gateway timeout', author: 'dev', timestamp: '2026-03-31T09:00:00Z' }],
}

const PIPELINE = {
  id: 'pipe-1', test_run_id: 'run-1', workflow_type: 'offline', status: 'completed',
  started_at: '2026-03-31T10:00:00Z', completed_at: '2026-03-31T10:01:00Z', error: null,
  created_at: '2026-03-31T10:00:00Z', execution_metadata: null, provenance_metadata: null,
}

beforeEach(() => {
  for (const fn of Object.values(hooks)) fn.mockReset()
  hooks.useRun.mockReturnValue({ data: RUN })
  hooks.useRuns.mockReturnValue({ data: { items: [] }, isLoading: false })
  hooks.useTestCases.mockReturnValue({ data: { items: TESTS, pages: 1, total: TESTS.length }, isLoading: false, error: undefined })
  hooks.useRunAttribution.mockReturnValue({ data: { items: [], total: 0 } })
  hooks.useRunIntelligence.mockReturnValue({ intelligence: INTELLIGENCE, isLoading: false, isError: false, refresh: vi.fn() })
  hooks.useDecisionReportVersions.mockReturnValue({ versions: [], isLoading: false, isError: false })
  hooks.useFailureClusters.mockReturnValue({ data: [] })
  hooks.useDeepFindings.mockReturnValue({ data: [] })
  hooks.usePipelineStatus.mockReturnValue({ data: null, mutate: vi.fn() })
  hooks.usePipelines.mockReturnValue({ data: [PIPELINE], isLoading: false })
  hooks.usePipelineStages.mockReturnValue({ data: [], isLoading: false })
  hooks.usePipelineTimeline.mockReturnValue({ data: undefined })
  hooks.useRunSummary.mockReturnValue({
    data: { test_run_id: 'run-1', executive_summary: 'Two checkout tests fail after the timeout change.', markdown_report: '', executive_panel: null },
    isLoading: false,
    error: undefined,
  })
  hooks.useSWR.mockImplementation((key: unknown) => ({
    data: key === 'regression-diff-run-1' ? DIFF : undefined,
    isLoading: false,
    error: undefined,
  }))
})

let location = ''
function LocationProbe() {
  const { pathname, search } = useLocation()
  useEffect(() => {
    location = `${pathname}${search}`
  })
  return null
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/runs/:runId" element={<><RunDetailPage /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Was the hook ever called for this run (i.e. would it have fetched)? */
const askedFor = (fn: ReturnType<typeof vi.fn>, arg: unknown) => fn.mock.calls.some((call) => call[0] === arg)
const pageTabs = () => screen.getByRole('tablist', { name: 'Run sections' })
const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('Run page — tabs', () => {
  it('opens on Tests: the tab bar, then the test table as the one primary content, no Disclosure above it', () => {
    const { container } = renderAt('/runs/run-1')
    expect(within(pageTabs()).getByRole('tab', { name: /Tests/ })).toHaveAttribute('aria-selected', 'true')
    expect(within(pageTabs()).getAllByRole('tab').map((t) => t.getAttribute('data-tab'))).toEqual(['tests', 'analysis', 'changes', 'evidence'])
    // The Tests tab counts the run's tests.
    expect(within(within(pageTabs()).getByRole('tab', { name: /Tests/ })).getByText('7')).toBeInTheDocument()
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(primary).toHaveAttribute('aria-label', 'Tests')
    expect(within(primary).getByRole('table')).toBeInTheDocument()
    expect(follows(pageTabs(), primary)).toBe(true)
    // Only the page's own tab bar: nothing tabbed sits between it and the table.
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(1)
    for (const d of Array.from(container.querySelectorAll('[data-disclosure]'))) expect(follows(primary, d)).toBe(true)
    // A closed tab asks for nothing.
    expect(askedFor(hooks.useRunIntelligence, 'run-1')).toBe(false)
    expect(askedFor(hooks.useFailureClusters, 'run-1')).toBe(false)
    expect(askedFor(hooks.usePipelines, 'run-1')).toBe(false)
    expect(hooks.useSWR.mock.calls.some((c) => c[0] === 'regression-diff-run-1')).toBe(false)
  })

  it('lists failed and broken tests first, keeping the order within each group', () => {
    renderAt('/runs/run-1')
    const names = within(screen.getByRole('region', { name: 'Tests' }))
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.querySelector('td p')?.textContent)
    expect(names).toEqual(['test_b_fail', 'test_d_broken', 'test_e_fail', 'test_a_pass', 'test_c_skip'])
  })

  it('shows the counts as status chips that filter the table, failures first', () => {
    renderAt('/runs/run-1')
    const chips = within(screen.getByRole('group', { name: 'Filter by status' })).getAllByRole('button')
    expect(chips.map((c) => c.textContent)).toEqual(['All 7', '2 failed', '1 broken', '3 passed', '1 skipped'])
    fireEvent.click(chips[1])
    expect(chips[1]).toHaveAttribute('aria-pressed', 'true')
    const lastCall = hooks.useTestCases.mock.calls[hooks.useTestCases.mock.calls.length - 1]
    expect(lastCall[1]).toMatchObject({ status: 'FAILED', page: 1 })
    expect(new URLSearchParams(location.split('?')[1]).get('status')).toBe('FAILED')
    // Clicking the active chip again clears the filter.
    fireEvent.click(chips[1])
    expect(new URLSearchParams(location.split('?')[1] ?? '').get('status')).toBeNull()
  })

  it('?tab=analysis renders the verdict, What failed and Deep Investigation\'s clusters with "Analyze failures" — and no test table', () => {
    const { container } = renderAt('/runs/run-1?tab=analysis')
    expect(within(pageTabs()).getByRole('tab', { name: 'Analysis' })).toHaveAttribute('aria-selected', 'true')
    expect(container.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'no_go')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(primary).toHaveAttribute('aria-label', 'What failed')
    expect(within(primary).getByText('Gateway timeouts')).toBeInTheDocument()
    const deep = screen.getByRole('region', { name: 'Deep investigation' })
    expect(within(deep).getByRole('heading', { name: /Proposed clusters/ })).toBeInTheDocument()
    expect(within(deep).getByRole('button', { name: /Analyze failures/ })).toBeEnabled()
    expect(follows(primary, deep)).toBe(true)
    expect(screen.queryByRole('region', { name: 'Tests' })).toBeNull()
    expect(askedFor(hooks.useTestCases, 'run-1')).toBe(false)
    expect(askedFor(hooks.useRunIntelligence, 'run-1')).toBe(true)
    expect(askedFor(hooks.useFailureClusters, 'run-1')).toBe(true)
  })

  it('a broken hosted section fails alone: the verdict, What failed and the tabs stay', () => {
    // Found by the journey spine: a clusters answer that is not a list threw
    // inside Deep Investigation's section and took the whole page down.
    hooks.useFailureClusters.mockReturnValue({ data: {} })
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderAt('/runs/run-1?tab=analysis')
    expect(screen.getByText('The deep investigation failed to load')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'What failed' })).getByText('Gateway timeouts')).toBeInTheDocument()
    expect(pageTabs()).toBeInTheDocument()
    quiet.mockRestore()
  })

  it('?tab=changes renders the regression diff and the way into Compare', () => {
    const { container } = renderAt('/runs/run-1?tab=changes')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(within(primary).getByRole('heading', { name: 'What changed since the last good run' })).toBeInTheDocument()
    expect(within(primary).getByText('-12.5%')).toBeInTheDocument()
    expect(within(primary).getByText('Change the gateway timeout')).toBeInTheDocument()
    const compare = screen.getByRole('region', { name: 'Compare' })
    expect(within(compare).getByRole('link', { name: /Compare with build #40/ }).getAttribute('href'))
      .toBe('/runs/compare?mode=manual&left=run-0&right=run-1&suite=Checkout')
    expect(within(compare).getByRole('link', { name: 'Open Compare' }).getAttribute('href')).toBe('/runs/compare?left=run-1')
    expect(askedFor(hooks.useTestCases, 'run-1')).toBe(false)
  })

  it('?tab=evidence renders the AI report of the run\'s newest pipeline (what /agents/run/:id showed), stages collapsed', () => {
    renderAt('/runs/run-1?tab=evidence')
    const report = screen.getByRole('region', { name: 'AI report' })
    expect(within(report).getByText('Two checkout tests fail after the timeout change.')).toBeInTheDocument()
    expect(within(report).getByRole('button', { name: 'Show stages' })).toHaveAttribute('aria-expanded', 'false')
    expect(askedFor(hooks.usePipelines, 'run-1')).toBe(true)
    expect(askedFor(hooks.usePipelineStages, 'pipe-1')).toBe(true)
    expect(askedFor(hooks.useRunSummary, 'run-1')).toBe(true)
    expect(askedFor(hooks.useTestCases, 'run-1')).toBe(false)
  })

  it('says so when no agent pipeline has analysed the run', () => {
    hooks.usePipelines.mockReturnValue({ data: [], isLoading: false })
    renderAt('/runs/run-1?tab=evidence')
    expect(screen.getByText('No agent pipeline has analysed this run yet.')).toBeInTheDocument()
  })

  it('switching tabs writes ?tab= and keeps the Tests filters; Tests is the clean URL', () => {
    renderAt('/runs/run-1?status=FAILED')
    fireEvent.click(within(pageTabs()).getByRole('tab', { name: 'Analysis' }))
    let params = new URLSearchParams(location.split('?')[1])
    expect(params.get('tab')).toBe('analysis')
    expect(params.get('status')).toBe('FAILED')
    fireEvent.click(within(pageTabs()).getByRole('tab', { name: /Tests/ }))
    params = new URLSearchParams(location.split('?')[1])
    expect(params.get('tab')).toBeNull()
    expect(params.get('status')).toBe('FAILED')
  })

  it('arriving on ?tab= keeps it (the filter sync no longer rewrites the whole query)', () => {
    renderAt('/runs/run-1?tab=evidence&report_version=2')
    const params = new URLSearchParams(location.split('?')[1])
    expect(params.get('tab')).toBe('evidence')
    expect(params.get('report_version')).toBe('2')
  })

  it('an unknown ?tab= falls back to Tests', () => {
    renderAt('/runs/run-1?tab=intelligence')
    expect(within(pageTabs()).getByRole('tab', { name: /Tests/ })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('Run page — one header', () => {
  it('has one PageHeader whose ⋯ menu carries the run\'s actions', () => {
    const { container } = renderAt('/runs/run-1')
    expect(container.querySelectorAll('[data-page-header]')).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Run #42' })).toBeInTheDocument()
    // Job · branch · date, one line.
    expect(screen.getByText(/^checkout-ci · main · \S/)).toBeInTheDocument()
    // The status pill, suite and release chips sit in the header; the counts are the Tests tab's chips.
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).getByText('Set release')).toBeInTheDocument()
    expect(within(header).queryByText(/^\d+ failed$/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const items = screen.getAllByRole('menuitem').map((item) => item.textContent)
    expect(items).toEqual([
      'Trigger pipeline',
      'Deep investigation',
      'Refresh AI analysis',
      'Export PDF',
      'Download evidence bundle',
      'Compare to previous run',
      'Release gate',
    ])
    expect(screen.getByRole('menuitem', { name: 'Release gate' })).toHaveAttribute('href', '/release-gate/run-1')
    // The old "Run Intelligence" button is the Analysis tab now.
    expect(screen.queryByRole('link', { name: /Run Intelligence/ })).toBeNull()
  })

  it('"Deep investigation" queues the investigation and opens the Analysis tab (not /deep-investigate/:id)', async () => {
    const agentService = (await import('@/services/agentService')).default
    const trigger = vi.spyOn(agentService, 'triggerDeepPipeline').mockResolvedValue({ task_id: 't', pipeline_run_id: 'p', message: 'ok', run_id: 'run-1' })
    renderAt('/runs/run-1')
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Deep investigation' }))
    await screen.findByRole('region', { name: 'Deep investigation' })
    expect(trigger).toHaveBeenCalledWith('run-1')
    expect(location).toBe('/runs/run-1?tab=analysis')
    trigger.mockRestore()
  })

  it.each(['tests', 'analysis', 'changes', 'evidence'])('links nowhere old on the %s tab', (tab) => {
    const { container } = renderAt(`/runs/run-1?tab=${tab}`)
    for (const link of Array.from(container.querySelectorAll('a[href]'))) {
      expect(link.getAttribute('href')).not.toMatch(/^\/runs\/[^/?]+\/intelligence|^\/deep-investigate\/|^\/agents\/run\//)
    }
  })
})
