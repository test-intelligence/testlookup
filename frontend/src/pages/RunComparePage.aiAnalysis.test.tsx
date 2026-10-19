/**
 * W03 AIH-03/04 on `/runs/compare?mode=manual&left=..&right=..`: the "AI analysis" section is mounted right after
 * the AI Comparison Report, asks the TARGET run for a compare against the LEFT run, and is told whether the two runs
 * share a project; the report lists the v2 `ai_changes` under "AI analysis changes" when it has any.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RunComparePage from './RunComparePage'
import type { RunCompareResponse } from '@/services/runCompareService'

const state = vi.hoisted(() => ({ compare: undefined as unknown, sectionProps: [] as unknown[] }))

vi.mock('@/hooks/useRunCompare', () => ({
  useRunCompare: () => ({ compare: state.compare, isLoading: false, isError: false, error: undefined }),
  useLatestSuiteCompare: () => ({ compare: undefined, isLoading: false, isError: false, error: undefined }),
  extractCompareErrorMessage: () => 'Could not load compare',
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: () => ({ data: { items: [] }, isLoading: false }) }))
// FAIL-06: the run pickers' reads (the deep link's two labels, the suite summary).
vi.mock('@/hooks/useRunSearch', () => ({
  RUN_SEARCH_PAGE_SIZE: 20,
  useRunSearch: () => ({ data: undefined, isLoading: false }),
  useRunSummary: () => ({ data: undefined }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ activeProjectId: 'p1', activeProject: { id: 'p1', name: 'Checkout' } }),
}))
vi.mock('@/components/ai/AIAnalysisCompareSection', () => ({
  default: (props: Record<string, unknown>) => {
    state.sectionProps.push(props)
    return <section data-ai-compare-section="" />
  },
}))

function side(id: string, project: string, build: string) {
  return {
    id, project_id: project, build_number: build, branch: 'main', commit_hash: null, status: 'FAILED',
    total_tests: 10, passed_tests: 8, failed_tests: 2, broken_tests: 0, skipped_tests: 0, pass_rate: 80,
    duration_ms: 1000, start_time: null, end_time: null, primary_suite_name: 'Auth', suite_names: ['Auth'],
  }
}

function compare(overrides: Partial<RunCompareResponse> = {}): RunCompareResponse {
  return {
    left: side('left-run', 'p1', '238'),
    right: side('right-run', 'p1', '240'),
    transitions: [],
    scope: 'suite',
    suite_name: 'Auth',
    selection: null,
    ai_report: {
      status: 'ready', executive_summary: 'Two runs compared.', markdown_report: '', risk_level: 'HIGH',
      key_differences: [], new_risks: [], resolved_risks: [], duration_concerns: [], recommended_actions: [],
      confidence: 70, confidence_reason: '', fallback_used: true,
      ai_changes: ['Verdict GO -> NO_GO (risk 35 -> 72)', 'New root cause: Timeout in checkout (4 tests)'],
    },
    delta_total: 0, delta_passed: 0, delta_failed: 0, delta_broken: 0, delta_skipped: 0, delta_pass_rate: 0, delta_duration_ms: 0,
    new_failures: 0, fixed: 0, still_failing: 0, regressed: 0, improved: 0, new_tests: 0, removed_tests: 0, duration_spikes: 0, renamed: 0,
    test_deltas: [], truncated: false,
    ...overrides,
  }
}

beforeEach(() => {
  state.compare = compare()
  state.sectionProps = []
})

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/runs/compare?mode=manual&left=left-run&right=right-run&suite=Auth']}>
      <RunComparePage />
    </MemoryRouter>,
  )

describe('RunComparePage — AI analysis (W03)', () => {
  it('mounts the section after the AI report, the left run as the baseline', () => {
    const { container } = renderPage()
    const section = container.querySelector('[data-ai-compare-section]') as HTMLElement
    const report = screen.getByText('AI Comparison Report').closest('section') as HTMLElement
    expect(Boolean(report.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
    expect(state.sectionProps[state.sectionProps.length - 1]).toMatchObject({
      leftRunId: 'left-run', rightRunId: 'right-run', suiteName: 'Auth', sameProject: true,
      leftLabel: 'Build #238', rightLabel: 'Build #240',
    })
  })

  it('tells the section when the two runs belong to different projects', () => {
    state.compare = compare({ right: side('right-run', 'p2', '240') })
    renderPage()
    expect(state.sectionProps[state.sectionProps.length - 1]).toMatchObject({ sameProject: false })
  })

  it('lists the report\'s AI analysis changes, and none when the report has none (a v1 report)', () => {
    const { container, unmount } = renderPage()
    const changes = container.querySelector('[data-ai-report-changes]') as HTMLElement
    expect(within(changes).getByText('AI analysis changes')).toBeInTheDocument()
    expect(within(changes).getByText('Verdict GO -> NO_GO (risk 35 -> 72)')).toBeInTheDocument()
    expect(within(changes).getByText('New root cause: Timeout in checkout (4 tests)')).toBeInTheDocument()
    unmount()
    const base = compare()
    state.compare = compare({ ai_report: base.ai_report ? { ...base.ai_report, ai_changes: undefined } : null })
    const v1 = renderPage()
    expect(v1.container.querySelector('[data-ai-report-changes]')).toBeNull()
  })
})
