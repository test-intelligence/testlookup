import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FailureAnalysisPage, { buildFailuresCsv } from './FailureAnalysisPage'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'

vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: vi.fn(),
  useFailureCategories: vi.fn(),
  useTopFailing: vi.fn(),
  useTrendData: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    widgetIds: [],
    setWidgets: vi.fn(),
    save: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('FailureAnalysisPage', () => {
  beforeEach(() => {
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
