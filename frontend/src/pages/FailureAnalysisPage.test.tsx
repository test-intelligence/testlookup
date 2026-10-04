import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import FailureAnalysisPage, {
  buildFailuresCsv,
  failureTimelineCells,
  failureTimelineLabel,
  RUN_STRIP_DAYS,
  runStripCells,
  runStripLabel,
  TIMELINE_MAX_CELLS,
} from './FailureAnalysisPage'
import { shiftDayIso, utcDayIso } from '@/utils/calendarDay'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'

// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: vi.fn(),
  useFailureCategories: vi.fn(),
  useTopFailing: vi.fn(),
  useTrendData: vi.fn(),
  // AI-4: consumed by the kind badge's evidence popover (lazy — the page
  // itself never triggers a fetch until a badge is clicked).
  useKindEvidence: vi.fn(() => ({ data: undefined, isLoading: false })),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

const analyticsControls = vi.hoisted(() => ({
  widgetIds: ['failures_kpis', 'failure_category_pie', 'top_failing_bar', 'flaky_leaderboard_table'],
}))

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    widgetIds: analyticsControls.widgetIds,
    setWidgets: vi.fn(),
    save: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { id: string; name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } })),
}))

// US-2.4 wiring — the page posts quarantine proposals + classification
// corrections through these services and resolves analysis ids via the
// lookup hook. Mocked so the tests exercise the page's behavior only.
vi.mock('@/services/flakyQuarantineService', () => ({
  flakyQuarantineService: {
    propose: vi.fn(),
  },
}))

vi.mock('@/services/aiFeedbackService', () => ({
  aiFeedbackService: {
    submitFeedback: vi.fn(),
  },
}))

vi.mock('@/hooks/useAnalysisLookup', () => ({
  useAnalysisLookup: vi.fn(() => ({ lookup: undefined, isLoading: true, isError: false })),
}))

// Wave 3 (VIZ-504 and friends): the page's one mount. The composite's own
// flag gates are `FailuresAdvanced.test.tsx`'s; here, only that the page hands
// it its window and suite scope, once.
const advanced = vi.hoisted(() => ({ props: [] as unknown[] }))
vi.mock('@/components/reports/catalogue/FailuresAdvanced', () => ({
  default: (props: unknown) => {
    advanced.props.push(props)
    return <div data-testid="failures-advanced" />
  },
}))

describe('FailureAnalysisPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = [
      'failures_kpis', 'failure_category_pie', 'top_failing_bar', 'flaky_leaderboard_table',
    ]
    // The page reads its window from the shared ``useTimeWindowStore``
    // (Zustand). Reset to the documented default so each test starts
    // from a known state — picking 30d in one test would otherwise
    // leak into the next via Zustand-persist's localStorage hydration.
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('renders the failure analysis workflow strip above the charts', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'test A', failure_rate_pct: 42 }] },
      isLoading: false,
    })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'INFRA_FAILURE', count: 4 }] },
      isLoading: false,
    })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'test A', fail_count: 4 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-01', passed: 10, failed: 2, skipped: 0, broken: 0, pass_rate: 83 }] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Failure analysis workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Flaky Detection/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Failure Analysis/i).length).toBeGreaterThan(0)
  })

  // X3: at 375 px the 205 + 124 px body columns pushed a card past the screen; below 768 px it is one column.
  it.each([
    [false, 'minmax(0, 1fr)'],
    [true, 'minmax(0, 1.65fr) minmax(0, 1fr)'],
  ])('body grid at min-width 768 = %s: %s', async (wide, columns) => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(min-width: 768px)' ? wide : true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    try {
      const { useRuns } = await import('@/hooks/useRuns')
      ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
      const { container } = render(
        <MemoryRouter initialEntries={['/failure-analysis']}>
          <Routes>
            <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
          </Routes>
        </MemoryRouter>,
      )
      await screen.findByText(/Failure analysis workflow/i)
      expect((container.querySelector('.body-grid') as HTMLElement).style.gridTemplateColumns).toBe(columns)
      // The workflow ribbon: four stages across from 768 px, two by two below (a quarter of 375 px is
      // narrower than "Categorization").
      const ribbon = container.querySelector('[data-ribbon-columns]') as HTMLElement
      expect(ribbon.style.gridTemplateColumns).toBe(`repeat(${wide ? 4 : 2}, minmax(0, 1fr))`)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('removes deselected failure panels from the rendered layout', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    analyticsControls.widgetIds = ['failures_kpis']
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes><Route path="/failure-analysis" element={<FailureAnalysisPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Failure Analysis' })).toBeInTheDocument()
    expect(screen.queryByText('Failure category distribution')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Flakiness analysis')).not.toBeInTheDocument()
  })

  it('treats a manually-triaged flake as a flake (not a "hard regression") and labels it "Flagged"', async () => {
    // Regression for the /failures vs /flaky-coach disagreement: when the only
    // flaky signal is a human-triaged FLAKY_TEST (source==='manual', the 100
    // marker), the page must (a) NOT render "flake detector found zero
    // intermittents / treat as a hard regression", and (b) render the entry as
    // "Flagged" rather than a misleading "100% flake".
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'login_flow', test_fingerprint: 'mfp-1', failure_rate_pct: 100, source: 'manual' }] },
      isLoading: false,
    })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    // A repeat failure exists — without the manual flake this would be a
    // REPEAT_FAILURE verdict and show the "hard regression" copy.
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'login_flow', fail_count: 4 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-01', passed: 6, failed: 4, skipped: 0, broken: 0, pass_rate: 60 }] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Verdict flipped to FLAKY → the contradictory copy is gone.
    expect(screen.queryByText(/zero intermittents/i)).not.toBeInTheDocument()
    // Manual entry rendered distinctly, not as "100% flake".
    expect(await screen.findByText(/^Flagged$/)).toBeInTheDocument()
    expect(screen.queryByText(/100% flake/i)).not.toBeInTheDocument()
    // Lede explains it was manually flagged.
    expect(screen.getByText(/manually flagged as flaky/i)).toBeInTheDocument()
  })

  it('shows a per-test-data-pending warning instead of "no failures" when trend reports failures but top-failing is empty', async () => {
    // Regression test for the "pass rate < 100% but What's failing says
    // 'No failures'" bug — caused by per-test rows not landing for
    // live-stream runs while ``test_runs.failed_tests`` aggregates are
    // already populated. The card MUST cross-check the trend prop and
    // render the warning empty state with a link to the failed runs,
    // not the green "every recent run passed" all-clear.
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    // The per-test endpoint returns NO failing tests (live-stream gap).
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    // But trend data (read from test_runs.failed_tests) reports failures.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-05-15', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis?days=7']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The green "no failures" message must NOT render.
    expect(
      screen.queryByText(/No failing tests in this window/i),
    ).not.toBeInTheDocument()

    // Warning empty state appears with the failing-execution count.
    expect(
      await screen.findByText(/failing execution.*detected in this window/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/Per-test data pending/i)).toBeInTheDocument()
  })

  it('keeps the green "no failures" empty state when trend has zero failures', async () => {
    // Counterpart to the bug-fix test: when there are TRULY no failures
    // anywhere, the original green all-clear empty state is unchanged.
    // This pins that the new branch is gated on trend-level failures, not
    // applied unconditionally.
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-05-15', passed: 10, failed: 0, skipped: 0, broken: 0, total: 10, pass_rate: 100 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis?days=7']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(
      await screen.findByText(/No failing tests in this window/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Per-test data pending/i)).not.toBeInTheDocument()
  })

  it('renders an inline current-vs-prior comparison when "Compare to previous window" is clicked', async () => {
    // Pin the user-visible bug fix: the CTA used to navigate to /trends,
    // which didn't show any comparison. Now the CTA toggles an inline
    // strip computed from a double-window trend fetch.

    // The page reads window from useTimeWindowStore (shared across
    // /summary, /live, /coverage, /trends, /runs, /overview, /my-failures).
    // Seed 30 so the strip's right-slot label is deterministic.
    useTimeWindowStore.setState({ days: 30 })

    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    // 4 trend points — 2 prior (low failures) + 2 current (high failures).
    // The strip should compute a +3 failure delta and render it.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-04-01', passed: 10, failed: 1, skipped: 0, broken: 0, total: 11, pass_rate: 90 },
          { date: '2026-04-02', passed: 10, failed: 1, skipped: 0, broken: 0, total: 11, pass_rate: 90 },
          { date: '2026-04-03', passed: 8,  failed: 3, skipped: 0, broken: 0, total: 11, pass_rate: 72 },
          { date: '2026-04-04', passed: 7,  failed: 2, skipped: 0, broken: 0, total: 9,  pass_rate: 78 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis?days=30']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Before the click, no comparison strip should be rendered.
    expect(screen.queryByText(/last \d+d vs prior \d+d/i)).toBeNull()

    // The "Compare to previous window" action lives in the verdict CTAs.
    // Click it — page state flips and the inline panel appears.
    fireEvent.click(await screen.findByText(/Compare to previous window/i))

    // The strip's right-slot label is unique on the page — use it as
    // the "strip is rendered" signal. With only 4 trend points (2 per
    // half) inside a 30d window, the label now shows the ACTUAL data
    // span — "last 2d (of 30d) vs prior 2d" — rather than the old
    // unconditional "last 30d vs prior 30d" (which misled users into
    // reading single-day totals as 30-day totals; see the comment on
    // ``actualLabel`` in FailureAnalysisPage.tsx).
    expect(
      await screen.findByText(/last 2d \(of 30d\) vs prior 2d/i),
    ).toBeInTheDocument()
    // Prior failures = 1+1 = 2; current failures = 3+2 = 5; delta = +3.
    // The failures cell renders the up-arrow with "3" — pin that one
    // delta value as the proof the math ran. Use a word-boundary so it
    // doesn't also match "↑ 33".
    expect(screen.getByText(/↑\s*3\b/)).toBeInTheDocument()

    // Re-clicking hides the panel.
    fireEvent.click(screen.getByText(/Hide comparison/i))
    expect(screen.queryByText(/last \d+d vs prior \d+d/i)).toBeNull()
  })

  it('does not show "0% failure rate" when a top failing test has small fail_count vs total executions', async () => {
    // Regression for the user-reported "0% failure rate on
    // api_key_scope_enforced — failed 8 of 2773 executions" bug.
    // The old code divided one test's fail_count by the suite's total
    // executions, then rounded to whole percent — so 8/2773 = 0.29%
    // rounded to "0%". The new code drops that misleading denominator
    // and instead shows count + share of failures, or a per-test rate
    // when the flaky list has matching total_runs.
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'api_key_scope_enforced', fail_count: 8 }] },
      isLoading: false,
    })
    // Trend has 2773 total executions, 8 failures (all from the one
    // top-failing test). Old math: 8/2773 = 0%.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-05-16', passed: 2765, failed: 8, skipped: 0, broken: 0, total: 2773, pass_rate: 99.7 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The issue-row body is split across <code>, <strong>, and plain
    // text spans, so we match by joined textContent on the issue-body
    // container rather than ``findByText`` (which only walks a single
    // text node).
    await screen.findAllByText('api_key_scope_enforced')
    const bodies = document.querySelectorAll('.issue-body')
    const joined = Array.from(bodies).map(b => b.textContent || '').join(' | ')
    expect(joined).toMatch(/api_key_scope_enforced/)
    expect(joined).toMatch(/failed/)
    expect(joined).toMatch(/8 times/)
    expect(joined).toMatch(/100%/)
    expect(joined).toMatch(/of failures here/)
    // The misleading "0% failure rate" headline must NOT appear.
    expect(joined).not.toMatch(/0% failure rate/)
    // The wrong denominator "of 2773 executions" must NOT appear.
    expect(joined).not.toMatch(/of 2773 executions/)
  })

  it('shows the per-test failure rate when the flaky list has matching total_runs', async () => {
    // When ``flaky[]`` carries the test's own ``total_runs``, we can
    // produce an honest failure rate (this_test.fail_count / this_test.total_runs).
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'flaky_pay', fail_count: 4 }] },
      isLoading: false,
    })
    // Same test in flaky → has total_runs=20 (so 4/20 = 20%).
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_fingerprint: 'fp', test_name: 'flaky_pay', suite_name: 'S', total_runs: 20, fail_count: 4, failure_rate_pct: 20 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-05-16', passed: 16, failed: 4, skipped: 0, broken: 0, total: 20, pass_rate: 80 }] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // 4 / 20 = 20%, using the test's own runs as denominator.
    expect(await screen.findByText(/20% failure rate/i)).toBeInTheDocument()
    // The truthful "failed 4 of 20 executions" denominator IS shown
    // because we have the right number.
    expect(screen.getByText(/failed 4 of 20 executions/i)).toBeInTheDocument()
  })

  it('shows a sub-1% rate as "0.3%" instead of collapsing to "0%"', async () => {
    // Direct user-scenario pin: 8 failures out of 2773 of the SAME
    // test (flaky list matches) should render "0.3%", not "0%".
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'api_key_scope_enforced', fail_count: 8 }] },
      isLoading: false,
    })
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_fingerprint: 'fp', test_name: 'api_key_scope_enforced', suite_name: 'S', total_runs: 2773, fail_count: 8, failure_rate_pct: 0.3 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-05-16', passed: 2765, failed: 8, skipped: 0, broken: 0, total: 2773, pass_rate: 99.7 }] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/0\.3% failure rate/i)).toBeInTheDocument()
    expect(screen.queryByText(/^0% failure rate$/i)).toBeNull()
  })

  it('renders the softer "uncategorised" copy instead of the misleading "clustering ran" claim', async () => {
    // Regression for user-reported "Category unknown — clustering ran
    // but 100% of failures couldn't be matched to a known pattern. No
    // owner auto-routed; no playbook attached." The new copy drops the
    // misleading "clustering ran" reference and the absolute "no owner
    // auto-routed" claim, replacing both with a shorter actionable
    // sentence.
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'UNKNOWN', count: 10 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-05-16', passed: 5, failed: 10, skipped: 0, broken: 0, total: 15, pass_rate: 33 }] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Old (incorrect) copy must NOT appear.
    expect(screen.queryByText(/clustering ran/i)).toBeNull()
    expect(screen.queryByText(/No owner auto-routed/i)).toBeNull()
    expect(screen.queryByText(/no playbook attached/i)).toBeNull()
    // New copy IS rendered.
    expect(
      await screen.findByText(/100% of failures aren't categorised yet/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Classify them so owners can be auto-routed/i),
    ).toBeInTheDocument()
  })

  it('Export button triggers a CSV download with the in-window failure data', async () => {
    // Pin the user-visible feature: the Export button used to toast
    // "coming in Phase 2". Now it downloads a CSV with the page's data.
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [
        { test_fingerprint: 'fp-1', test_name: 'flaky_a', suite_name: 'A', total_runs: 12, fail_count: 3, failure_rate_pct: 25 },
      ] },
      isLoading: false,
    })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'INFRA_FAILURE', count: 4 }] }, isLoading: false,
    })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [
        { test_name: 'test_pay', suite_name: 'PaymentSuite', class_name: 'CheckoutTests', failure_category: 'PRODUCT_BUG', fail_count: 5, last_failed: '2026-05-16T10:00:00Z' },
      ] }, isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-05-15', passed: 10, failed: 5, skipped: 0, broken: 0, total: 15, pass_rate: 67 }] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    // Spy on the Blob constructor so we can read what the page tried
    // to download without depending on jsdom Blob.text() (which is not
    // implemented). Then stub URL.createObjectURL + the anchor click
    // so nothing tries to actually navigate.
    const blobInputs: BlobPart[] = []
    const OrigBlob = globalThis.Blob
    globalThis.Blob = class extends OrigBlob {
      constructor(parts: BlobPart[] = [], options?: BlobPropertyBag) {
        super(parts, options)
        blobInputs.push(...parts)
      }
    }
    const origCreateUrl = URL.createObjectURL
    const origRevokeUrl = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()
    const clickSpy = vi.fn()
    const origCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = origCreateElement(tag) as HTMLElement
      if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy
      return el
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('button', { name: /Export/i }))

    expect(clickSpy).toHaveBeenCalledTimes(1)
    const csv = blobInputs.filter((p): p is string => typeof p === 'string').join('')
    expect(csv).toContain('# Top failing tests')
    expect(csv).toContain('test_pay')
    expect(csv).toContain('PaymentSuite')
    expect(csv).toContain('# Failure categories')
    expect(csv).toContain('INFRA_FAILURE')
    expect(csv).toContain('# Flaky tests')
    expect(csv).toContain('flaky_a')

    globalThis.Blob = OrigBlob
    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevokeUrl
  })
})

// VIZ-606: the page's private csvCell was replaced by the shared one
// (lib/viz/csv.ts). Ordinary data must come out byte-for-byte as before; a
// formula-shaped name — test names come from ingested CI reports — is made inert.
describe('buildFailuresCsv — shared csvCell (VIZ-606 regression)', () => {
  const meta = { projectName: 'Shop', windowLabel: '7d', suiteName: null, generatedAt: '2026-05-16T11:00:00.000Z' }

  it('a normal export is byte-for-byte what the page always wrote', () => {
    const csv = buildFailuresCsv({
      topFailing: [
        {
          test_name: 'test_pay',
          suite_name: 'PaymentSuite',
          class_name: 'Checkout, "v2"',
          failure_category: 'PRODUCT_BUG',
          fail_count: 5,
          last_failed: '2026-05-16T10:00:00Z',
        } as never,
      ],
      categories: [{ category: 'INFRA_FAILURE', count: 4 } as never],
      flaky: [{ test_name: 'flaky_a', suite_name: null, total_runs: 12, fail_count: 3, failure_rate_pct: 25 } as never],
      meta,
    })
    expect(csv).toBe(
      [
        '# TestLookup — Failure analysis export',
        '# Project,Shop',
        '# Window,7d',
        '# Suite filter,All suites',
        '# Generated,2026-05-16T11:00:00.000Z',
        '',
        '# Top failing tests',
        'test_name,suite_name,class_name,failure_category,fail_count,last_failed',
        'test_pay,PaymentSuite,"Checkout, ""v2""",PRODUCT_BUG,5,2026-05-16T10:00:00Z',
        '',
        '# Failure categories',
        'category,count',
        'INFRA_FAILURE,4',
        '',
        '# Flaky tests',
        'test_name,suite_name,total_runs,fail_count,failure_rate_pct',
        'flaky_a,,12,3,25',
        '',
      ].join('\r\n'),
    )
  })

  it('a formula-shaped test, suite or project is neutralised; a negative number is not', () => {
    const csv = buildFailuresCsv({
      topFailing: [
        {
          test_name: '=HYPERLINK("http://x","y")',
          suite_name: '-2+3',
          class_name: '@SUM(A1)',
          failure_category: null,
          fail_count: -1,
          last_failed: null,
        } as never,
      ],
      categories: [],
      flaky: [],
      meta: { ...meta, projectName: '+evil' },
    })
    expect(csv).toContain(`\r\n"'=HYPERLINK(""http://x"",""y"")",'-2+3,'@SUM(A1),,-1,\r\n`)
    expect(csv).toContain(`# Project,'+evil\r\n`)
  })

  // Review A6 / F9: Excel in a `;` list-separator locale splits on `;` and
  // honours our quotes only at a line start; a test named `x;=…` must not
  // leave a cell `=…` behind. Full-width and space-led forms (A11) too.
  it('a formula hidden after a `;`, behind a space or in full width is neutralised', () => {
    const csv = buildFailuresCsv({
      topFailing: [
        {
          test_name: 'x;=1+1;',
          suite_name: ' =1',
          class_name: '＝SUM(A1)',
          failure_category: null,
          fail_count: 3,
          last_failed: null,
        } as never,
      ],
      categories: [],
      flaky: [],
      meta,
    })
    expect(csv).toContain(`\r\n"x;'=1+1;",' =1,'＝SUM(A1),,3,\r\n`)
  })
})

describe('FailureAnalysisPage — US-2.4 wired actions', () => {
  beforeEach(() => {
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
    vi.clearAllMocks()
  })

  /** Seed the metrics hooks with a repeat-failing test scenario. */
  async function seedFailingScenario({
    topFailingItem = { test_name: 'checkout_flow', fail_count: 4, test_fingerprint: 'fp-top', suite_name: 'Checkout' },
    categories = [] as { category: string; count: number }[],
    flakyItems = [] as unknown[],
  } = {}) {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: flakyItems }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: categories }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [topFailingItem] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-07-01', passed: 6, failed: 4, skipped: 0, broken: 0, total: 10, pass_rate: 60 }] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
  }

  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('re-adds the "Start bisect" CTA (Epic 8 US-8.2 — wired to the Suspects surface)', async () => {
    await seedFailingScenario()
    renderPage()

    // The failing-test card is present…
    expect(await screen.findByRole('button', { name: /Mute test/i })).toBeInTheDocument()
    // …and the bisect CTA is back — it reveals the Suspects panel.
    expect(screen.getByRole('button', { name: /Start bisect/i })).toBeInTheDocument()
  })

  it('opens the mute modal and posts a quarantine proposal with the typed reason', async () => {
    const { flakyQuarantineService } = await import('@/services/flakyQuarantineService')
    ;(flakyQuarantineService.propose as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'PROPOSED' })

    await seedFailingScenario()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /Mute test/i }))
    const dialog = await screen.findByRole('dialog', { name: /Mute test \(propose quarantine\)/i })

    // Submit is disabled until a reason is provided (required field).
    const submit = within(dialog).getByRole('button', { name: /Propose quarantine/i })
    expect(submit).toBeDisabled()
    fireEvent.click(submit)
    expect(flakyQuarantineService.propose).not.toHaveBeenCalled()

    fireEvent.change(within(dialog).getByPlaceholderText(/Why should this test stop gating runs/i), {
      target: { value: 'Known infra flake, tracked in JIRA-123' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: /Propose quarantine/i }))

    await waitFor(() => expect(flakyQuarantineService.propose).toHaveBeenCalledTimes(1))
    expect(flakyQuarantineService.propose).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 'proj-1',
      test_fingerprint: 'fp-top',
      test_name: 'checkout_flow',
      suite_name: 'Checkout',
      detection_method: 'manual',
      rationale: expect.objectContaining({ reason: 'Known infra flake, tracked in JIRA-123' }),
    }))
    // Modal closes after a successful proposal.
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Mute test/i })).toBeNull())
  })

  it('disables "Mute test" with a tooltip when the top failing test has no fingerprint', async () => {
    await seedFailingScenario({
      topFailingItem: { test_name: 'no_fp_test', fail_count: 3 } as never,
    })
    renderPage()

    const muteBtn = await screen.findByRole('button', { name: /Mute test/i })
    expect(muteBtn).toBeDisabled()
    expect(muteBtn).toHaveAttribute('title', expect.stringMatching(/fingerprint/i))
    fireEvent.click(muteBtn)
    expect(screen.queryByRole('dialog', { name: /Mute test/i })).toBeNull()
  })

  it('opens the correct-classification dialog and submits rating=incorrect feedback', async () => {
    const { useAnalysisLookup } = await import('@/hooks/useAnalysisLookup')
    ;(useAnalysisLookup as ReturnType<typeof vi.fn>).mockReturnValue({
      lookup: { analysis_id: 'an-1', failure_category: 'UNKNOWN', analyzed_at: '2026-07-01T00:00:00Z' },
      isLoading: false,
      isError: false,
    })
    const { aiFeedbackService } = await import('@/services/aiFeedbackService')
    ;(aiFeedbackService.submitFeedback as ReturnType<typeof vi.fn>).mockResolvedValue({ feedback_id: 'fb-1', message: 'ok' })

    // ≥50% UNKNOWN so the category card renders its correction CTA.
    await seedFailingScenario({ categories: [{ category: 'UNKNOWN', count: 4 }] })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /Correct the classification/i }))
    const dialog = await screen.findByRole('dialog', { name: /Correct classification/i })

    // Shows the current category from the lookup.
    expect(within(dialog).getByText('UNKNOWN')).toBeInTheDocument()

    // Submit needs a selected category first.
    const submit = within(dialog).getByRole('button', { name: /Record correction/i })
    expect(submit).toBeDisabled()

    fireEvent.click(within(dialog).getByRole('radio', { name: /Infrastructure/i }))
    fireEvent.change(within(dialog).getByPlaceholderText(/What gave the misclassification away/i), {
      target: { value: 'Stack trace is a DNS timeout' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: /Record correction/i }))

    await waitFor(() => expect(aiFeedbackService.submitFeedback).toHaveBeenCalledTimes(1))
    expect(aiFeedbackService.submitFeedback).toHaveBeenCalledWith('an-1', expect.objectContaining({
      rating: 'incorrect',
      corrected_category: 'INFRASTRUCTURE',
      comment: 'Stack trace is a DNS timeout',
    }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Correct classification/i })).toBeNull())
  })

  it('shows the "no AI analysis recorded" empty state instead of a form when the lookup finds nothing', async () => {
    const { useAnalysisLookup } = await import('@/hooks/useAnalysisLookup')
    ;(useAnalysisLookup as ReturnType<typeof vi.fn>).mockReturnValue({
      lookup: { analysis_id: null, failure_category: null, analyzed_at: null },
      isLoading: false,
      isError: false,
    })

    await seedFailingScenario({ categories: [{ category: 'UNKNOWN', count: 4 }] })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /Correct the classification/i }))
    const dialog = await screen.findByRole('dialog', { name: /Correct classification/i })

    expect(within(dialog).getByText(/No AI analysis recorded for this test yet/i)).toBeInTheDocument()
    // No submit button in the empty state — nothing to correct.
    expect(within(dialog).queryByRole('button', { name: /Record correction/i })).toBeNull()
  })

  it('routes the QA "Recommended actions" entry to the correction dialog (no more placeholder toast)', async () => {
    const { useAnalysisLookup } = await import('@/hooks/useAnalysisLookup')
    ;(useAnalysisLookup as ReturnType<typeof vi.fn>).mockReturnValue({
      lookup: { analysis_id: 'an-1', failure_category: 'UNKNOWN', analyzed_at: '2026-07-01T00:00:00Z' },
      isLoading: false,
      isError: false,
    })

    await seedFailingScenario({ categories: [{ category: 'UNKNOWN', count: 4 }] })
    renderPage()

    // The QA clustering rec renders with an "Open" CTA — clicking it opens
    // the same correction dialog the category card uses.
    const qaRec = (await screen.findByText(/QA · clustering/i)).closest('div[class*="grid"]') as HTMLElement
    fireEvent.click(within(qaRec).getByRole('button', { name: /^Open$/i }))
    expect(await screen.findByRole('dialog', { name: /Correct classification/i })).toBeInTheDocument()
  })
})

describe('FailureAnalysisPage — failure-kind triad (US-9.2)', () => {
  beforeEach(() => {
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
    vi.clearAllMocks()
  })

  /** Seed a window with product + infrastructure failures and a by-kind
   *  aggregation (the backend ships it on the failure-categories payload). */
  async function seedKindScenario() {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { category: 'PRODUCT_BUG', count: 5, kind: 'product' },
          { category: 'INFRASTRUCTURE', count: 2, kind: 'infrastructure' },
        ],
        by_kind: [
          { kind: 'product', count: 5 },
          { kind: 'test_code', count: 0 },
          { kind: 'infrastructure', count: 3 },  // includes 1 BROKEN-nudged row
          { kind: 'unknown', count: 0 },
        ],
      },
      isLoading: false,
    })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'checkout_flow', fail_count: 5, failure_category: 'PRODUCT_BUG', failure_kind: 'product' }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-07-01', passed: 12, failed: 8, skipped: 0, broken: 0, total: 20, pass_rate: 60 }] },
      isLoading: false,
    })
  }

  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('renders the kind filter chips with by-kind counts and the AI-classified provenance copy', async () => {
    await seedKindScenario()
    renderPage()

    const group = await screen.findByRole('group', { name: /Filter by failure kind \(AI-classified\)/i })
    // All four kinds stay visible (zero counts included) + the All chip
    // (5 product + 3 infrastructure = 8 analyzed failures).
    const allChip = within(group).getByRole('button', { name: /All\s*8/i })
    expect(allChip).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: /Product\s*5/i })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: /Test code\s*0/i })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: /Infrastructure\s*3/i })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: /Unknown\s*0/i })).toBeInTheDocument()
    // Provenance copy — kinds are AI-classified, never ground truth.
    expect(within(group).getByText('AI-classified')).toBeInTheDocument()
    // "All" starts active.
    expect(allChip).toHaveAttribute('aria-pressed', 'true')
  })

  it('filters the category distribution card when a kind chip is selected', async () => {
    await seedKindScenario()
    renderPage()

    const group = await screen.findByRole('group', { name: /Filter by failure kind/i })
    fireEvent.click(within(group).getByRole('button', { name: /Infrastructure\s*3/i }))

    // Card copy flips to the filtered wording…
    expect(
      await screen.findByText(/filtered to AI-classified/i),
    ).toBeInTheDocument()
    // …and the card total now reflects only the infrastructure category rows
    // (2 categorised INFRASTRUCTURE failures; the BROKEN-nudged row has no
    // category item, which is exactly the category-only fidelity limit).
    expect(screen.getByText(/^2 failures$/)).toBeInTheDocument()

    // Switching back to All restores the full distribution total (7).
    fireEvent.click(within(group).getByRole('button', { name: /All\s*8/i }))
    expect(screen.getByText(/^7 failures$/)).toBeInTheDocument()
  })

  it("shows a color-coded kind badge on the What's-failing card for the headline test", async () => {
    await seedKindScenario()
    renderPage()

    await screen.findAllByText('checkout_flow')
    // The badge carries the AI-classified provenance in its tooltip.
    const badges = screen.getAllByTitle(/AI-classified failure kind: Product/i)
    expect(badges.length).toBeGreaterThan(0)
  })

  it('derives chip counts client-side when an older payload has no by_kind block', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    // No ``by_kind`` and no per-item ``kind`` — the page falls back to the
    // category → kind mirror (AUTOMATION_DEFECT → test_code).
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'AUTOMATION_DEFECT', count: 4 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-07-01', passed: 6, failed: 4, skipped: 0, broken: 0, total: 10, pass_rate: 60 }] },
      isLoading: false,
    })

    renderPage()

    const group = await screen.findByRole('group', { name: /Filter by failure kind/i })
    expect(within(group).getByRole('button', { name: /Test code\s*4/i })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: /Product\s*0/i })).toBeInTheDocument()
  })
})

describe('buildFailuresCsv', () => {
  // Pure-function tests — no DOM, no render. Pin the CSV shape so a
  // future tweak (extra column, reordered section) doesn't silently
  // break a customer's downstream pipeline that consumes the file.
  it('produces a multi-section CSV with header / top-failing / categories / flaky blocks', () => {
    const csv = buildFailuresCsv({
      topFailing: [
        { test_name: 'test_a', suite_name: 'S1', class_name: 'C', failure_category: 'PRODUCT_BUG', fail_count: 3, last_failed: '2026-05-16T10:00:00Z' },
      ],
      flaky: [
        { test_fingerprint: 'fp', test_name: 'flaky_b', suite_name: 'S2', total_runs: 10, fail_count: 2, failure_rate_pct: 20 },
      ],
      categories: [{ category: 'INFRA_FAILURE', count: 5 }],
      meta: {
        projectName: 'My Project',
        windowLabel: '7d',
        suiteName: null,
        generatedAt: '2026-05-16T11:00:00.000Z',
      },
    })

    expect(csv).toContain('# TestLookup — Failure analysis export')
    expect(csv).toContain('# Project,My Project')
    expect(csv).toContain('# Window,7d')
    expect(csv).toContain('# Suite filter,All suites')
    expect(csv).toContain('# Top failing tests')
    expect(csv).toContain('test_name,suite_name,class_name,failure_category,fail_count,last_failed')
    expect(csv).toContain('test_a,S1,C,PRODUCT_BUG,3,2026-05-16T10:00:00Z')
    expect(csv).toContain('# Failure categories')
    expect(csv).toContain('category,count')
    expect(csv).toContain('INFRA_FAILURE,5')
    expect(csv).toContain('# Flaky tests')
    expect(csv).toContain('flaky_b,S2,10,2,20')
    // Section separators (blank lines) keep Excel users sane.
    expect(csv.split(/\r\n\r\n/).length).toBeGreaterThanOrEqual(4)
  })

  it('quotes cells that contain commas, quotes, or newlines', () => {
    const csv = buildFailuresCsv({
      topFailing: [
        { test_name: 'has,comma', suite_name: 'has "quote"', class_name: null, failure_category: null, fail_count: 1, last_failed: null },
      ],
      flaky: [],
      categories: [],
      meta: { projectName: 'P', windowLabel: '1d', suiteName: null, generatedAt: 'now' },
    })

    // ``has,comma`` must be wrapped in quotes; the inner quote in
    // ``has "quote"`` must be doubled.
    expect(csv).toContain('"has,comma"')
    expect(csv).toContain('"has ""quote"""')
  })

  it('falls back to safe defaults when the project name is missing or empty', () => {
    const csv = buildFailuresCsv({
      topFailing: [],
      flaky: [],
      categories: [],
      meta: { projectName: 'All projects', windowLabel: '24h', suiteName: 'Smoke', generatedAt: 'now' },
    })

    expect(csv).toContain('# Project,All projects')
    expect(csv).toContain('# Suite filter,Smoke')
  })
})

const point = (date: string, passed: number, failed: number, skipped = 0, broken = 0) => ({
  date, passed, failed, skipped, broken, pass_rate: passed + failed ? (passed / (passed + failed)) * 100 : 0,
})

describe("runStripCells (What's failing: the 14-day run strip)", () => {
  const today = '2026-09-28'

  it('is always the last 14 days, oldest first, whatever the trend holds', () => {
    const cells = runStripCells([point('2026-01-01', 5, 0)], today)
    expect(cells).toHaveLength(RUN_STRIP_DAYS)
    expect(cells[0].key).toBe(shiftDayIso(today, -13))
    expect(cells[13].key).toBe(today)
    expect(cells.every((c) => c.tone === 'none')).toBe(true)
  })

  it('a failure makes the day fail; a pass with no failure passes; nothing (or skips only) did not run', () => {
    const cells = runStripCells(
      [
        point(shiftDayIso(today, -4), 9, 1),
        point(shiftDayIso(today, -3), 4, 0),
        point(shiftDayIso(today, -2), 0, 0, 3, 0), // skipped only: nothing passed or failed
      ],
      today,
    )
    expect(cells.slice(-5).map((c) => c.tone)).toEqual(['fail', 'pass', 'none', 'none', 'none'])
    expect(cells[9].label).toBe(`${shiftDayIso(today, -4)} · failed`)
    expect(cells[13].label).toBe(`${today} · not run`)
    // Only `fail` cells carry the failure cue: no mixed tone on this strip.
    expect(cells.some((c) => c.tone === 'mixed')).toBe(false)
  })

  // The owner's rule (OD-7 / OD-16): a chart does not silently drop a status.
  // A broken-only day used to be drawn as "not run" — a quiet day.
  it('broken is failure-like: a broken-only day is a failing day, not "not run"', () => {
    const cells = runStripCells(
      [
        point(shiftDayIso(today, -1), 0, 0, 2, 1), // skipped + broken only
        point(today, 5, 0, 0, 2), // passes and breakages, no failure
      ],
      today,
    )
    expect(cells.slice(-2).map((c) => c.tone)).toEqual(['fail', 'fail'])
    expect(cells[12].label).toBe(`${shiftDayIso(today, -1)} · broken`)
    expect(runStripLabel(cells)).toBe('Run strip: 2 failed, 0 passed, 12 not run.')
    expect(runStripCells([point(today, 0, 1, 0, 1)], today)[13].label).toBe(`${today} · failed and broken`)
  })

  it('keeps the "Run strip:" name the card and its visual spec find it by, with real counts', () => {
    const cells = runStripCells([point(shiftDayIso(today, -3), 9, 1), point(shiftDayIso(today, -2), 4, 0)], today)
    expect(runStripLabel(cells)).toBe('Run strip: 1 failed, 1 passed, 12 not run.')
  })
})

describe('failureTimelineCells (the failure timeline)', () => {
  const today = '2026-09-28'

  it('draws at most 30 days and names the drawn window', () => {
    const cells = failureTimelineCells([], 90, today)
    expect(cells).toHaveLength(TIMELINE_MAX_CELLS)
    expect(failureTimelineLabel(cells)).toBe('Failure timeline: 0 days with failures over the last 30 days.')
    expect(failureTimelineCells([], 7, today)).toHaveLength(7)
  })

  it("deepens a failed day by its failed share (the severity), and counts only drawn days", () => {
    const cells = failureTimelineCells(
      [
        point(shiftDayIso(today, -40), 0, 5), // outside the drawn 30: not counted
        point(shiftDayIso(today, -2), 3, 1),
        point(shiftDayIso(today, -1), 0, 4),
        point(today, 6, 0),
      ],
      90,
      today,
    )
    const [d2, d1, d0] = cells.slice(-3)
    expect([d2.tone, d1.tone, d0.tone]).toEqual(['fail', 'fail', 'pass'])
    expect(d2.severity).toBe(0.25)
    expect(d1.severity).toBe(1)
    expect(d0.severity).toBeUndefined()
    expect(d2.label).toBe(`${shiftDayIso(today, -2)} · 1 of 4 failed`)
    expect(failureTimelineLabel(cells)).toBe('Failure timeline: 2 days with failures over the last 30 days.')
  })

  it('a day with results that are all zero is an empty day, not a pass', () => {
    const cells = failureTimelineCells([point(today, 0, 0)], 3, today)
    // The run strip's word for the same day, on the same page.
    expect(cells[2]).toMatchObject({ tone: 'none', label: `${today} · not run` })
    expect(runStripCells([point(today, 0, 0)], today)[13]).toMatchObject({ tone: 'none', label: `${today} · not run` })
  })

  // R1 F6: `total` counts skipped, so 5 passed + 3 skipped read "8 passing",
  // and a skips-only day read "4 passing" and was drawn green, while the run
  // strip on the same card called that day "not run".
  it('names passed and skipped truthfully, and a skips-only day is "not run", as on the run strip', () => {
    const cells = failureTimelineCells(
      [point(shiftDayIso(today, -1), 5, 0, 3), point(today, 0, 0, 4)],
      2,
      today,
    )
    expect(cells[0]).toMatchObject({ tone: 'pass', label: `${shiftDayIso(today, -1)} · 5 passed, 3 skipped` })
    expect(cells[1]).toMatchObject({ tone: 'none', label: `${today} · not run (4 skipped)` })
    expect(runStripCells([point(today, 0, 0, 4)], today)[13].tone).toBe(cells[1].tone)
  })

  // OD-7 / OD-16: broken counts, in the total and as failure-like.
  it('counts broken alongside failed: a broken-only day fails, and broken deepens the severity', () => {
    const cells = failureTimelineCells(
      [
        point(shiftDayIso(today, -2), 0, 0, 0, 3), // broken only: was "0 runs"
        point(shiftDayIso(today, -1), 6, 1, 0, 1), // 1 failed + 1 broken of 8
        point(today, 3, 0, 1, 0), // passes and a skip: a pass day
      ],
      3,
      today,
    )
    expect(cells.map((c) => c.tone)).toEqual(['fail', 'fail', 'pass'])
    expect(cells[0]).toMatchObject({ severity: 1, label: `${shiftDayIso(today, -2)} · 3 of 3 broken` })
    expect(cells[1]).toMatchObject({
      severity: 0.25,
      label: `${shiftDayIso(today, -1)} · 2 of 8 failed or broken (1 broken)`,
    })
    expect(cells[2].label).toBe(`${today} · 3 passed, 1 skipped`)
    expect(failureTimelineLabel(cells)).toBe('Failure timeline: 2 days with failures over the last 3 days.')
  })
})

describe('FailureAnalysisPage — verdict meter and strips', () => {
  it('draws the stability score as a meter, and both strips as named day strips', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'test A', fail_count: 4 }] },
      isLoading: false,
    })
    const today = utcDayIso()
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [point(shiftDayIso(today, -1), 8, 2), point(today, 10, 0)] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes><Route path="/failure-analysis" element={<FailureAnalysisPage />} /></Routes>
      </MemoryRouter>,
    )

    const meter = await screen.findByRole('meter', { name: 'Stability score' })
    expect(meter).toHaveAttribute('data-gauge-bar', 'fill')
    expect(meter.getAttribute('aria-valuetext')).toMatch(/^\d+ of 100, /)
    for (const tick of ['Block · 0', 'At risk · 33', 'Stable · 66', '100']) expect(within(meter).getByText(tick)).toBeInTheDocument()

    const strip = screen.getByRole('img', { name: 'Run strip: 1 failed, 1 passed, 12 not run.' })
    expect(strip.querySelectorAll('[data-day-cell]')).toHaveLength(14)
    // The failed day carries the failure cue, not colour alone.
    expect(strip.querySelectorAll('[data-day-cue="fail"]')).toHaveLength(1)
    expect(screen.getByText('1 fail · 12 idle · 1 pass')).toBeInTheDocument()

    const timeline = screen.getByRole('img', { name: /^Failure timeline: 1 day with failures over the last \d+ days\.$/ })
    expect(timeline.closest('[data-day-strip]')).toHaveAttribute('data-day-strip', 'status')
    // One word for an empty day on this page: both strips state it as "Not run"
    // (the run strip has no legend; its table says it), never "No runs".
    for (const img of [strip, timeline]) {
      const table = img.closest('[data-day-strip]')?.querySelector('[data-day-strip-table]') as HTMLElement
      expect(table).toHaveTextContent('Not run')
      expect(table).not.toHaveTextContent('No runs')
    }
    const legend = timeline.closest('[data-day-strip]')?.querySelector('[data-day-strip-legend]') as HTMLElement
    expect(legend).toHaveTextContent('Not run')
    expect(legend).not.toHaveTextContent('No runs')
  })

  it('with no runs the verdict is PENDING and the bar is not measured, never a 0 reading', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes><Route path="/failure-analysis" element={<FailureAnalysisPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('img', { name: 'Stability score: not measured' })).toBeInTheDocument()
    expect(screen.queryByRole('meter', { name: 'Stability score' })).toBeNull()
  })
})

describe('FailureAnalysisPage — Wave 3', () => {
  async function renderWithCategories() {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'PRODUCT_BUG', count: 21 }, { category: 'UNKNOWN', count: 6 }] },
      isLoading: false,
    })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    analyticsControls.widgetIds = ['failures_kpis', 'failure_category_pie']
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: 30 })
    advanced.props = []
    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes><Route path="/failure-analysis" element={<FailureAnalysisPage />} /></Routes>
      </MemoryRouter>,
    )
    return screen.findByText('Failure category distribution')
  }

  it('mounts the advanced sections once, with the page window and suite scope', async () => {
    await renderWithCategories()
    expect(screen.getAllByTestId('failures-advanced')).toHaveLength(1)
    expect(advanced.props[advanced.props.length - 1]).toEqual({ days: 30, suiteFilter: null })
  })

  it('category rows share ONE grid whose label column is as wide as the widest label (no fixed 220 px)', async () => {
    const heading = await renderWithCategories()
    const rows = within(heading.closest('.rounded-xl') as HTMLElement).getAllByRole('row')
    expect(rows).toHaveLength(6)
    const grid = rows[0].parentElement as HTMLElement
    // The label column's FLOOR is its min-content: the widest whole name (or badge), never 0 (R2-B F-17).
    expect(grid.style.gridTemplateColumns).toBe('minmax(min-content, max-content) minmax(48px, 1fr) 56px 56px')
    expect(grid).toHaveAttribute('data-category-layout', 'columns')
    for (const row of rows) {
      expect(row.parentElement).toBe(grid)
      expect(row.style.gridTemplateColumns).toBe('subgrid')
      expect(row.style.gridColumn).toBe('1 / -1')
    }
    expect(screen.getByRole('row', { name: 'Assertion / product: 78% (21 of 27)' })).toBeInTheDocument()
  })

  // R2-B F-17: at 640 the label column collapsed to the badge ("Un…", three INFRASTRUCTURE rows with no name), at 375
  // name and badge were drawn over the bars, and on Linux at 768 the names were cut again.
  describe('category names are never cut, at any card width', () => {
    const widths = (grid: number) => {
      vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
        const width = this.hasAttribute('data-category-grid')
          ? grid
          : this.hasAttribute('data-category-name')
            ? 140
            : this.hasAttribute('data-category-badge')
              ? 105
              : 0
        return { width, height: 0, top: 0, left: 0, right: width, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
      })
    }
    const grid = (heading: HTMLElement) =>
      (heading.closest('.rounded-xl') as HTMLElement).querySelector('[data-category-grid]') as HTMLElement

    afterEach(() => vi.restoreAllMocks())

    it('a name is whole text that never truncates; the badge may wrap under it', async () => {
      const heading = await renderWithCategories()
      for (const name of grid(heading).querySelectorAll<HTMLElement>('[data-category-name]')) {
        expect(name).toHaveClass('whitespace-nowrap')
        expect(name.querySelector('.truncate')).toBeNull()
        expect(name).not.toHaveClass('truncate')
        expect((name.parentElement as HTMLElement).style.flexWrap).toBe('wrap')
      }
      expect(grid(heading).querySelectorAll('[data-category-name]')).toHaveLength(6)
      expect(grid(heading).querySelector('[data-category-name]')).toHaveTextContent('Unknown')
    })

    it('the four columns while the widest name + bar + counts fit (140 + 196 = 336 px)', async () => {
      widths(336)
      const heading = await renderWithCategories()
      expect(grid(heading)).toHaveAttribute('data-category-layout', 'columns')
    })

    it('narrower: each row stacks, its name (and badge) on a line above its bar and counts', async () => {
      widths(335)
      const heading = await renderWithCategories()
      const card = grid(heading)
      expect(card).toHaveAttribute('data-category-layout', 'stacked')
      expect(card.style.gridTemplateColumns).toBe('minmax(0, 1fr)')
      const rows = within(card).getAllByRole('row')
      expect(rows).toHaveLength(6)
      for (const row of rows) {
        expect(row.style.gridTemplateColumns).toBe('minmax(0, 1fr) 56px 56px')
        expect((row.firstElementChild as HTMLElement).style.gridColumn).toBe('1 / -1')
      }
      expect(screen.getByRole('row', { name: 'Assertion / product: 78% (21 of 27)' })).toHaveTextContent('Assertion / product')
    })
  })
})
