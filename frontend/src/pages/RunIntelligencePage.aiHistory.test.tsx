/**
 * W03 AIH-04 on the Analysis tab body and the Evidence tab's decision report:
 *
 *  - the AI history (previous-run card + version picker) is the FIRST thing in `[data-run-analysis]`;
 *  - `?ai_version=` naming a stored version that is not the latest shows that snapshot INSTEAD of the live
 *    sections (no verdict banner, no What failed); the latest version is the live view;
 *  - an old `?report_version=` link maps to the version that published that report;
 *  - "one picker drives both": the Evidence tab's report select moves `?ai_version=` too.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RunDecisionReport, RunIntelligenceBody } from './RunIntelligencePage'
import { digest, historyList, storedVersion, versionSummary } from '@/components/ai/aiHistory.fixtures'

const { mockProjectState, ai, intel } = vi.hoisted(() => ({
  mockProjectState: { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } },
  ai: {
    list: undefined as unknown,
    byId: {} as Record<string, unknown>,
  },
  intel: { value: undefined as unknown, reportVersions: [] as unknown[], calls: [] as unknown[][] },
}))

vi.mock('@/hooks/useRunIntelligence', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useRunIntelligence')>()),
  useRunIntelligence: (runId: string, reportVersion: number | null) => {
    intel.calls.push([runId, reportVersion])
    return { intelligence: intel.value, isLoading: false, isError: false, refresh: vi.fn() }
  },
  useDecisionReportVersions: () => ({ versions: intel.reportVersions, isLoading: false, isError: false }),
}))
vi.mock('@/hooks/useAIHistory', () => {
  const idle = { data: undefined, error: undefined, isLoading: true, mutate: () => Promise.resolve(undefined) }
  return {
    useAIHistory: (runId: string | null, kind = 'analysis_digest') => ({
      data: runId && kind === 'analysis_digest' ? ai.list : undefined,
      error: undefined,
      isLoading: false,
      mutate: () => Promise.resolve(undefined),
    }),
    useAIVersion: (runId: string | null, id: string | null) => ({ ...idle, data: runId && id ? ai.byId[id] : undefined, isLoading: false }),
    useAIVersionDiff: () => idle,
    useAICompare: () => idle,
    usePreviousRun: () => idle,
  }
})
vi.mock('@/hooks/useReleaseCouncil', () => ({
  useReleaseCouncil: () => ({ council: undefined, isLoading: false, isError: false, refresh: vi.fn() }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) => selector(mockProjectState)),
}))
vi.mock('@/hooks/useRuns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useRuns')>()),
  useRunStepFlips: () => ({ data: undefined, isLoading: false }),
}))
vi.mock('@/hooks/useDecisionTrail', () => ({
  useDecisionTrail: () => ({ data: undefined, error: undefined, isLoading: false }),
}))
vi.mock('@/services/onboardingService', () => ({
  onboardingService: { completeStep: vi.fn(() => Promise.resolve()) },
}))

const INTELLIGENCE = {
  intelligence_available: true,
  run: { id: 'run-1', build_number: '42', status: 'FAILED', total_tests: 7, passed_tests: 3, failed_tests: 2, broken_tests: 1, skipped_tests: 1, pass_rate: 50 },
  structured_summary: null,
  failure_clusters: [
    { id: 'row-1', cluster_id: 'cl-1', label: 'Gateway timeouts', size: 2, representative_error: 'TimeoutError', member_test_ids: ['tc-b'], cohesion_score: 0.9, criticality_level: 'HIGH', dimension_scores: [] },
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

const V1 = versionSummary(1)
const V2 = versionSummary(2)

beforeEach(() => {
  ai.list = historyList([V1, V2])
  ai.byId = { [V1.version_id]: storedVersion(1, digest()) }
  intel.value = INTELLIGENCE
  intel.reportVersions = []
  intel.calls = []
})

let search = ''
function Probe() {
  const current = useLocation().search
  useEffect(() => {
    search = current
  })
  return null
}

function renderBody(query: string) {
  return render(
    <MemoryRouter initialEntries={[`/runs/run-1${query}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<><RunIntelligenceBody runId="run-1" /><Probe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RunIntelligenceBody — the AI history (W03 AIH-04)', () => {
  it('puts the previous-run card and the version picker first, above the live verdict', () => {
    const { container } = renderBody('?tab=analysis')
    const analysis = container.querySelector('[data-run-analysis]') as HTMLElement
    const first = analysis.firstElementChild as HTMLElement
    expect(first.querySelector('[data-previous-run-card]')).not.toBeNull()
    expect(first.querySelector('[data-ai-version-picker]')).not.toBeNull()
    expect(analysis.querySelector('[data-status-banner]')).not.toBeNull()
    expect(screen.getByRole('region', { name: 'What failed' })).toBeInTheDocument()
    expect(analysis.querySelector('[data-ai-snapshot]')).toBeNull()
  })

  it('a stored version that is not the latest replaces the live sections with its snapshot', () => {
    const { container } = renderBody('?tab=analysis&ai_version=1&report_version=1')
    const analysis = container.querySelector('[data-run-analysis]') as HTMLElement
    expect(analysis.querySelector('[data-ai-snapshot]')).toHaveAttribute('data-ai-snapshot-version', '1')
    expect(analysis.querySelector('[data-status-banner]')).toBeNull()
    expect(screen.queryByRole('region', { name: 'What failed' })).toBeNull()
    expect(analysis.querySelector('[data-previous-run-card]')).toBeNull()
    expect(screen.getByRole('combobox', { name: 'AI analysis version' })).toHaveValue('1')
  })

  it('the latest version is the live view', () => {
    const { container } = renderBody('?tab=analysis&ai_version=2')
    expect(container.querySelector('[data-ai-snapshot]')).toBeNull()
    expect(screen.getByRole('region', { name: 'What failed' })).toBeInTheDocument()
  })

  it('an old ?report_version= link opens the version that published that report', () => {
    const { container } = renderBody('?tab=analysis&report_version=1')
    expect(container.querySelector('[data-ai-snapshot]')).toHaveAttribute('data-ai-snapshot-version', '1')
  })

  it('with no stored version yet, the live view and "First analysis of this run"', () => {
    ai.list = historyList([])
    const { container } = renderBody('?tab=analysis&report_version=1')
    expect(container.querySelector('[data-ai-snapshot]')).toBeNull()
    expect(container.querySelector('[data-ai-version-empty]')).toHaveTextContent(/^First analysis of this run/)
    expect(screen.getByRole('region', { name: 'What failed' })).toBeInTheDocument()
  })

  it('a link to a version waits for the list instead of flashing the live view', () => {
    ai.list = undefined
    const { container } = renderBody('?tab=analysis&ai_version=1')
    expect(screen.queryByRole('region', { name: 'What failed' })).toBeNull()
    expect(container.querySelector('[data-run-analysis]')).not.toBeNull()
  })
})

describe('RunDecisionReport — one picker drives both', () => {
  it('choosing a report version on the Evidence tab moves ?ai_version= to the version that published it', () => {
    intel.value = {
      ...INTELLIGENCE,
      structured_summary: {
        decision_intelligence: {
          schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
          metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
          release_decision: { recommendation: 'NO_GO', risk_score: 80, reasoning: 'Verified.' },
          quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
          source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
          verification: { status: 'passed', checks: [], repairs: [] },
        },
        decision_report_verification: { status: 'passed', checks: [] },
        decision_report: { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' },
      },
    }
    intel.reportVersions = [
      { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' },
      { report_id: 'report-1', report_version: 1, status: 'published', generated_at: '2026-08-11T19:00:00Z' },
    ]
    render(
      <MemoryRouter initialEntries={['/runs/run-1?tab=evidence']}>
        <Routes>
          <Route path="/runs/:runId" element={<><RunDecisionReport runId="run-1" /><Probe /></>} />
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.change(screen.getByLabelText('Decision report version'), { target: { value: '1' } })
    let params = new URLSearchParams(search)
    expect(params.get('report_version')).toBe('1')
    expect(params.get('ai_version')).toBe('1')
    expect(params.get('tab')).toBe('evidence')
    fireEvent.change(screen.getByLabelText('Decision report version'), { target: { value: '2' } })
    params = new URLSearchParams(search)
    expect(params.get('report_version')).toBe('2')
    expect(params.get('ai_version')).toBe('2')
  })
})
