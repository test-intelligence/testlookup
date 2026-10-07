import { cleanup, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'

import CoveragePage, { buildCoverageCsv, CADENCE_MAX_CELLS, CoverageComparisonStrip, coverageCadence } from './CoveragePage'
import { shiftDayIso, utcDayIso } from '@/utils/calendarDay'
import type { TrendPoint } from '@/types/metrics'

// Mock every export from useMetrics — the page (and any child it renders)
// may pull in more hooks than the test exercises, and vitest errors out if
// an imported export isn't defined on the mock module. Defaults to an
// empty SWR shape; individual tests can ``mockReturnValue`` to override.
// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary:  vi.fn(d),
    useTrendData:         vi.fn(d),
    useFlakyTests:        vi.fn(d),
    useFailureCategories: vi.fn(d),
    useTopFailing:        vi.fn(d),
    useCoverage:          vi.fn(d),
    useDefects:           vi.fn(d),
    useSuiteDetail:       vi.fn(d),
    useAiSummary:         vi.fn(d),
  }
})
// Other hooks the page transitively imports — stubbed so SWR doesn't fire.
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] }, isLoading: false }),
}))
// P2: the page no longer reads a widget selection. The hook stays mocked with
// a saved EMPTY selection (a user who once unticked everything): it must not
// hide a section.
const analyticsControls = vi.hoisted(() => ({
  widgetIds: [] as string[],
}))

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: analyticsControls.widgetIds, addInstance: vi.fn(), removeInstance: vi.fn(),
    save: vi.fn(), reset: vi.fn(), isDirty: false, savedViews: [],
    activeViewId: null, setActiveView: vi.fn(), deleteView: vi.fn(),
    updateInstance: vi.fn(), moveInstance: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

// Lazy chunks (the coverage sections) can take longer than waitFor's 1 s default under the full
// suite's load, and more than 5 s with coverage on (seen twice), so 10 s here.
const LAZY_TIMEOUT = 10_000

/** P3: the verdict, its score, its dimensions and the gaps are in a collapsed disclosure below the table. */
function openScore() {
  fireEvent.click(screen.getByRole('button', { name: /How this score is computed/ }))
}

/** P3: every header action beyond Views is in the ⋯ menu; picks one item. */
function pickFromMenu(name: string | RegExp) {
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}

/** The ⋯ menu's items, as their names, with the menu left open. */
function menuItems(): string[] {
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  return screen.getAllByRole('menuitem').map((item) => item.textContent ?? '')
}

describe('CoveragePage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = []
  })

  // P2: the 4-stage "Coverage workflow" ribbon was invented (fixed stages and
  // an evidence count spread across them: "the rest get 1 each so users see the
  // workflow ran end-to-end") and is gone.
  it('renders the verdict and the suite breakdown, and no invented workflow ribbon', async () => {
    const { useCoverage } = await import('@/hooks/useMetrics')

    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: {
          unique_tests: 18,
          suite_count: 4,
          total_executions: 120,
          avg_pass_rate: 92.5,
          days_with_runs: 7,
        },
        suites: [
          { suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 },
          { suite_name: 'Auth', unique_tests: 4, passed: 32, failed: 3, skipped: 0, pass_rate: 91 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes>
          <Route path="/coverage" element={<CoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Test Coverage' })).toBeInTheDocument()
    // P3: the verdict is in the score disclosure, collapsed until asked for.
    expect(screen.queryByRole('region', { name: 'Coverage verdict' })).toBeNull()
    openScore()
    expect(screen.getByRole('region', { name: 'Coverage verdict' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Suite coverage breakdown' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Coverage workflow' })).toBeNull()
    expect(screen.queryByText(/Coverage Workflow/i)).toBeNull()
    expect(screen.queryByText(/Coverage Snapshot|Suite Breadth|Coverage Risk|Coverage Actions/)).toBeNull()
    expect(screen.queryByText(/evidence/i)).toBeNull()
    expect(screen.queryByText(/% coverage score/i)).toBeNull()
    // US-15.1: no invented confidence anywhere.
    expect(screen.queryByText(/% confidence/i)).toBeNull()
  })

  // P2: the widget picker is gone, so a selection saved with it (here: none
  // ticked) must not keep hiding a section forever.
  it('renders every section whatever widget selection was saved', async () => {
    const { useCoverage } = await import('@/hooks/useMetrics')
    analyticsControls.widgetIds = []
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 1, suite_count: 1, total_executions: 5, avg_pass_rate: 80, days_with_runs: 1 },
        suites: [{ suite_name: 'Payments', unique_tests: 1, passed: 4, failed: 1, skipped: 0, pass_rate: 80 }],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Test Coverage' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Coverage metrics' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Suite coverage breakdown' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Customize/ })).toBeNull()
  })

  it('Export button triggers a CSV download with the in-window coverage data', async () => {
    // Regression for the old toast placeholder (it promised the export for
    // a later release). Now wires through to a real CSV with the summary
    // KPIs, per-suite breakdown, and daily cadence trend.
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')

    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: {
          unique_tests: 18, suite_count: 2, total_executions: 120,
          avg_pass_rate: 92.5, days_with_runs: 7,
        },
        suites: [
          { suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 },
          { suite_name: 'Auth',     unique_tests: 4, passed: 32, failed: 3, skipped: 0, pass_rate: 91 },
        ],
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-05-15', passed: 10, failed: 1, skipped: 0, broken: 0, total: 11, pass_rate: 90.9 },
          { date: '2026-05-16', passed: 12, failed: 0, skipped: 0, broken: 0, total: 12, pass_rate: 100 },
        ],
      },
      isLoading: false,
    })

    // Spy on the Blob constructor so we can read the CSV bytes back
    // without depending on jsdom's Blob.text() (which is not implemented).
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
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes>
          <Route path="/coverage" element={<CoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Test Coverage' })
    // P3: Export is in the header's ⋯ menu.
    pickFromMenu('Export CSV')

    expect(clickSpy).toHaveBeenCalledTimes(1)
    const csv = blobInputs.filter((p): p is string => typeof p === 'string').join('')
    expect(csv).toContain('# Summary')
    expect(csv).toContain('# Per-suite breakdown')
    expect(csv).toContain('Payments,6,48,2,1,96')
    expect(csv).toContain('Auth,4,32,3,0,91')
    expect(csv).toContain('# Daily cadence')
    expect(csv).toContain('2026-05-15,10,1,0,0,11,90.9')

    globalThis.Blob = OrigBlob
    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevokeUrl
  })
})

describe('buildCoverageCsv', () => {
  // Pure-function tests pin the CSV shape so a future tweak (extra
  // column, reordered section) doesn't silently break a downstream
  // pipeline consuming the file.
  const _meta = {
    projectName: 'My Project',
    windowLabel: '7d',
    suiteFilter: null,
    generatedAt: '2026-05-16T11:00:00.000Z',
    healthScore: 87,
    verdict: 'HEALTHY',
  }

  it('produces a four-section CSV with header / summary / suites / cadence', () => {
    const csv = buildCoverageCsv({
      summary: {
        unique_tests: 18, suite_count: 2, total_executions: 120,
        avg_pass_rate: 92.5, days_with_runs: 7,
      },
      suites: [
        { suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 },
      ],
      trend: [
        { date: '2026-05-16', passed: 12, failed: 0, skipped: 0, broken: 0, total: 12, pass_rate: 100 },
      ],
      meta: _meta,
    })

    expect(csv).toContain('# TestLookup — Coverage export')
    expect(csv).toContain('# Project,My Project')
    expect(csv).toContain('# Window,7d')
    expect(csv).toContain('# Suite filter,All suites')
    expect(csv).toContain('# Health score,87')
    expect(csv).toContain('# Verdict,HEALTHY')
    expect(csv).toContain('# Summary')
    expect(csv).toContain('unique_tests,suite_count,total_executions,avg_pass_rate,days_with_runs')
    expect(csv).toContain('18,2,120,92.5,7')
    expect(csv).toContain('# Per-suite breakdown')
    expect(csv).toContain('suite_name,unique_tests,passed,failed,skipped,pass_rate')
    expect(csv).toContain('Payments,6,48,2,1,96')
    expect(csv).toContain('# Daily cadence')
    expect(csv).toContain('date,passed,failed,skipped,broken,total,pass_rate')
    expect(csv).toContain('2026-05-16,12,0,0,0,12,100')
    // Four sections → at least three blank-line separators.
    expect(csv.split(/\r\n\r\n/).length).toBeGreaterThanOrEqual(4)
  })

  it('quotes suite names that contain commas, quotes, or newlines', () => {
    const csv = buildCoverageCsv({
      summary: {},
      suites: [
        { suite_name: 'has,comma', unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: 100 },
        { suite_name: 'has "quote"', unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: 100 },
      ],
      trend: [],
      meta: _meta,
    })

    expect(csv).toContain('"has,comma"')
    // Internal double-quote escaped by doubling.
    expect(csv).toContain('"has ""quote"""')
  })

  it('renders the compare-to-previous-window panel when the CTA is clicked', async () => {
    // Regression: the CTA used to raise a "not built yet" toast. Now it
    // toggles an inline strip computed from a double-window trend fetch.
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')

    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: 2, total_executions: 50, avg_pass_rate: 90, days_with_runs: 4 },
        suites: [
          { suite_name: 'Payments', unique_tests: 5, passed: 25, failed: 2, skipped: 0, pass_rate: 92 },
        ],
      },
      isLoading: false,
    })
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    const currentStart = shiftDayIso(utcDayIso(), -29)
    const priorStart = shiftDayIso(currentStart, -30)
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: priorStart, passed: 5,  failed: 5, skipped: 0, broken: 0, total: 10, pass_rate: 50 },
          { date: shiftDayIso(currentStart, -1), passed: 6,  failed: 4, skipped: 0, broken: 0, total: 10, pass_rate: 60 },
          { date: currentStart, passed: 9,  failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
          { date: utcDayIso(), passed: 10, failed: 0, skipped: 0, broken: 0, total: 10, pass_rate: 100 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes>
          <Route path="/coverage" element={<CoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Before click: comparison strip must not render.
    expect(screen.queryByLabelText(/Coverage comparison/i)).toBeNull()

    // P3: the toggle is the header's ⋯ menu item (it was the verdict card's button).
    await screen.findByRole('heading', { name: 'Test Coverage' })
    pickFromMenu('Compare to previous window')

    // Strip renders with the three cells. Pass rate prior = 11/20 = 55%;
    // current = 19/20 = 95%; delta = +40%. ``Total executions`` /
    // ``Pass rate`` labels also appear on the page's KPI tiles, so we
    // anchor the assertions on the strip's aria-labelled section.
    const strip = await screen.findByLabelText(/Coverage comparison/i)
    const stripText = strip.textContent ?? ''
    expect(stripText).toContain('Total executions')
    expect(stripText).toContain('Pass rate')
    expect(stripText).toContain('95.0%')
    expect(stripText).toContain('55.0%')
    expect(stripText).toMatch(/↑\s*40\.0%/)

    // Toggle off — strip disappears, the item's words flip back.
    pickFromMenu('Hide comparison')
    expect(screen.queryByLabelText(/Coverage comparison/i)).toBeNull()
    expect(menuItems()).toContain('Compare to previous window')
  })

  it('does not invent a prior period by splitting sparse current-window rows', async () => {
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 1, suite_count: 1, total_executions: 10, avg_pass_rate: 100, days_with_runs: 2 },
        suites: [{ suite_name: 'Sparse', unique_tests: 1, passed: 10, failed: 0, skipped: 0, pass_rate: 100 }],
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: shiftDayIso(utcDayIso(), -2), passed: 4, failed: 0, skipped: 0, broken: 0, total: 4, pass_rate: 100 },
          { date: utcDayIso(), passed: 6, failed: 0, skipped: 0, broken: 0, total: 6, pass_rate: 100 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Test Coverage' })
    pickFromMenu('Compare to previous window')

    const strip = await screen.findByLabelText(/Coverage comparison/i)
    expect(strip.textContent).toContain('vs prior 0')
    expect(strip.textContent).toContain('10')
  })

  it('falls back to safe defaults for missing summary fields and empty data', () => {
    const csv = buildCoverageCsv({
      summary: {},
      suites: [],
      trend: [],
      meta: { ..._meta, suiteFilter: 'Smoke' },
    })

    // Summary row uses 0 for any missing KPI rather than blanking the cell.
    expect(csv).toContain('0,0,0,0,0')
    // The suite filter is surfaced in the metadata block.
    expect(csv).toContain('# Suite filter,Smoke')
    // Section markers still appear even when the sections are empty —
    // consumers can detect "no data" by header-presence + empty body.
    expect(csv).toContain('# Per-suite breakdown')
    expect(csv).toContain('# Daily cadence')
  })

  // VIZ-606: the page's private csvCell was replaced by the shared one
  // (lib/viz/csv.ts). The output for ordinary data must not move by a byte;
  // only a formula-shaped name changes, and only by becoming inert.
  it('VIZ-606 regression: a normal export is byte-for-byte what the page always wrote', () => {
    const csv = buildCoverageCsv({
      summary: { unique_tests: 18, suite_count: 2, total_executions: 120, avg_pass_rate: 92.5, days_with_runs: 7 },
      suites: [
        { suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 },
        { suite_name: 'Pay, "ments"', unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: 100 },
      ],
      trend: [{ date: '2026-05-16', passed: 12, failed: 0, skipped: 0, broken: 0, total: 12, pass_rate: 100 }],
      meta: _meta,
    })
    expect(csv).toBe(
      [
        '# TestLookup — Coverage export',
        '# Project,My Project',
        '# Window,7d',
        '# Suite filter,All suites',
        '# Generated,2026-05-16T11:00:00.000Z',
        '# Health score,87',
        '# Verdict,HEALTHY',
        '',
        '# Summary',
        'unique_tests,suite_count,total_executions,avg_pass_rate,days_with_runs',
        '18,2,120,92.5,7',
        '',
        '# Per-suite breakdown',
        'suite_name,unique_tests,passed,failed,skipped,pass_rate',
        'Payments,6,48,2,1,96',
        '"Pay, ""ments""",1,1,0,0,100',
        '',
        '# Daily cadence',
        'date,passed,failed,skipped,broken,total,pass_rate',
        '2026-05-16,12,0,0,0,12,100',
        '',
      ].join('\r\n'),
    )
  })

  it('VIZ-606 regression: a formula-shaped suite, project or filter is neutralised; numbers are not', () => {
    const csv = buildCoverageCsv({
      summary: {},
      suites: [{ suite_name: '=HYPERLINK("http://x","y")', unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: -0 }],
      trend: [],
      meta: { ..._meta, projectName: '@evil', suiteFilter: '+1+1' },
    })
    expect(csv).toContain(`\r\n"'=HYPERLINK(""http://x"",""y"")",1,1,0,0,0\r\n`)
    expect(csv).toContain(`# Project,'@evil\r\n`)
    expect(csv).toContain(`# Suite filter,'+1+1\r\n`)
  })

  // Review A6 / F9: in a `;` list-separator locale Excel splits the line on
  // `;` and honours our quotes only at a line start, so a suite named
  // `x;=…` would yield a cell `=…`. Quoted, and neutralised after the `;`.
  it('a suite hiding a formula after a `;` (the list separator of much of Europe) is neutralised there', () => {
    const csv = buildCoverageCsv({
      summary: {},
      suites: [{ suite_name: `x;=1+cmd|' /C calc'!A0`, unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: 100 }],
      trend: [],
      meta: { ..._meta, suiteFilter: 'a\t=1+1' },
    })
    expect(csv).toContain(`\r\n"x;'=1+cmd|' /C calc'!A0",1,1,0,0,100\r\n`)
    expect(csv).toContain(`# Suite filter,"a\t'=1+1"\r\n`)
  })
})

describe('CoverageComparisonStrip', () => {
  // Pure-render tests so we pin the delta math + colour direction
  // without a full page mount.

  const baseStats = (overrides: Partial<{
    passed: number; failed: number; skipped: number; total: number;
    passRate: number; days: number; daysWithRuns: number
  }> = {}) => ({
    passed: 0, failed: 0, skipped: 0, total: 0,
    passRate: 0, days: 7, daysWithRuns: 0,
    ...overrides,
  })

  it('shows an up-arrow with the absolute delta for total executions', () => {
    render(
      <CoverageComparisonStrip
        current={baseStats({ total: 120 })}
        prior={baseStats({ total: 100 })}
        windowDays={7}
      />,
    )
    const strip = screen.getByLabelText(/Coverage comparison/i)
    expect(strip.textContent).toContain('120')
    expect(strip.textContent).toMatch(/↑\s*20\b/)
    expect(strip.textContent).toContain('vs prior 100')
  })

  it('flags a falling pass rate red (down-arrow)', () => {
    render(
      <CoverageComparisonStrip
        current={baseStats({ passRate: 80 })}
        prior={baseStats({ passRate: 95 })}
        windowDays={7}
      />,
    )
    const strip = screen.getByLabelText(/Coverage comparison/i)
    expect(strip.textContent).toMatch(/↓\s*15\.0%/)
  })

  it('flags a falling days-with-runs as bad (cadence regressed)', () => {
    render(
      <CoverageComparisonStrip
        current={baseStats({ daysWithRuns: 3 })}
        prior={baseStats({ daysWithRuns: 6 })}
        windowDays={7}
      />,
    )
    const strip = screen.getByLabelText(/Coverage comparison/i)
    expect(strip.textContent).toMatch(/↓\s*3\b/)
    expect(strip.textContent).toContain('vs prior 6')
  })

  it('renders an em-dash (no arrow) when a metric is flat', () => {
    render(
      <CoverageComparisonStrip
        current={baseStats({ passRate: 90 })}
        prior={baseStats({ passRate: 90 })}
        windowDays={7}
      />,
    )
    const strip = screen.getByLabelText(/Coverage comparison/i)
    // Pass-rate cell shows the em-dash because delta < 0.01.
    expect(strip.textContent).toContain('—')
  })
})

const day = (date: string, executions: number): TrendPoint => ({
  date, passed: executions, failed: 0, skipped: 0, broken: 0, pass_rate: executions ? 100 : 0,
})

describe('coverageCadence (the run cadence strip)', () => {
  const today = '2026-09-28'

  it('has one cell per day, quiet days included, oldest first, today marked', () => {
    const { cells } = coverageCadence([day('2026-09-27', 3)], 5, today)
    expect(cells.map((c) => c.key)).toEqual(['2026-09-24', '2026-09-25', '2026-09-26', '2026-09-27', '2026-09-28'])
    expect(cells.map((c) => c.tone)).toEqual(['none', 'none', 'none', 'pass', 'none'])
    expect(cells.map((c) => c.marker ?? null)).toEqual([null, null, null, null, 'today'])
    expect(cells[3].label).toBe('2026-09-27 · 3 executions')
    expect(cells[0].label).toBe('2026-09-24 · 0 executions')
  })

  it("keeps Coverage's five volume levels (0 / 1-5 / 6-20 / 21-50 / more than 50)", () => {
    const counts = [0, 1, 5, 6, 20, 21, 50, 51]
    const trend = counts.map((n, i) => day(shiftDayIso(today, -(counts.length - 1 - i)), n))
    const { cells } = coverageCadence(trend, counts.length, today)
    expect(cells.map((c) => c.level)).toEqual([0, 1, 1, 2, 2, 3, 3, 4])
  })

  it("counts every status in a day's volume, broken included", () => {
    const { cells } = coverageCadence(
      [{ date: `${today}T00:00:00Z`, passed: 2, failed: 2, skipped: 1, broken: 1, pass_rate: 33 }],
      1,
      today,
    )
    expect(cells[0].level).toBe(2) // 6 executions
  })

  it('is volume, not outcome: no cell is marked mixed or failed, so no failure cue', () => {
    const trend = [{ date: today, passed: 0, failed: 9, skipped: 0, broken: 0, pass_rate: 0 }]
    const { cells } = coverageCadence(trend, 3, today)
    expect(cells.map((c) => c.tone)).toEqual(['none', 'none', 'pass'])
  })

  // The bug B3 found: the label counted the whole window but said "last 30".
  it('labels only the cells it draws when the window is longer than 30 days', () => {
    // 90 days: runs on 20 days that are NOT drawn (61-80 days ago) and on 4 that are.
    const trend = [
      ...Array.from({ length: 20 }, (_, i) => day(shiftDayIso(today, -(61 + i)), 2)),
      ...[0, 3, 7, 12].map((ago) => day(shiftDayIso(today, -ago), 2)),
    ]
    const { cells, label } = coverageCadence(trend, 90, today)
    expect(cells).toHaveLength(CADENCE_MAX_CELLS)
    expect(cells[0].key).toBe(shiftDayIso(today, -29))
    expect(label).toBe('Run cadence over the last 30 days. 4 active days, 26 empty days.')
  })
})

describe('CoveragePage — verdict meter and cadence strip', () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )

  it('draws the health score as a named meter and the cadence as a labelled strip', async () => {
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 18, suite_count: 4, total_executions: 120, avg_pass_rate: 92.5, days_with_runs: 7 },
        suites: [{ suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 }],
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [day(utcDayIso(), 30)], period_days: 30 },
      isLoading: false,
    })
    renderPage()

    await screen.findByRole('heading', { name: 'Test Coverage' })
    openScore()
    const meter = await screen.findByRole('meter', { name: 'Coverage health score' })
    const score = Number(meter.getAttribute('aria-valuenow'))
    expect(score).toBeGreaterThan(0)
    expect(meter.getAttribute('aria-valuetext')).toMatch(new RegExp(`^${score} of 100, (Healthy|At risk|Blocked)$`))
    expect(meter).toHaveAttribute('data-gauge-bar', 'fill')
    for (const tick of ['Block · 0', 'At risk · 33', 'Healthy · 66', '100']) expect(within(meter).getByText(tick)).toBeInTheDocument()

    const strip = screen.getByRole('img', { name: /^Run cadence over the last \d+ days\. 1 active days, \d+ empty days\.$/ })
    expect(strip.querySelectorAll('[data-day-cell]').length).toBeGreaterThan(1)
    expect(strip.querySelector('[data-day-cue]')).toBeNull()
    expect(strip.closest('[data-day-strip]')).toHaveAttribute('data-day-strip', 'intensity')
  })

  it('a PENDING verdict (composite 0) draws no reading: the bar is not measured', async () => {
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 0, suite_count: 0, total_executions: 0, avg_pass_rate: 0, days_with_runs: 0 },
        suites: [{ suite_name: 'Payments', unique_tests: 0, passed: 0, failed: 0, skipped: 0, pass_rate: 0 }],
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
    renderPage()
    await screen.findByRole('heading', { name: 'Test Coverage' })
    // The disclosure's header says so too: no score.
    expect(screen.getByRole('button', { name: /How this score is computed/ })).toHaveTextContent('Pending · no score yet')
    openScore()
    expect(await screen.findByRole('img', { name: 'Coverage health score: not measured' })).toBeInTheDocument()
    expect(screen.queryByRole('meter', { name: 'Coverage health score' })).toBeNull()
  })
})


/** `matchMedia` answering `(min-width: 768px)` with `wide` (jsdom has none, which reads as wide). */
function stubViewportWide(wide: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(min-width: 768px)' ? wide : true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
}

describe('CoveragePage — Wave 3 sections (VIZ-502 / 501)', () => {
  const coverage = {
    summary: { unique_tests: 18, suite_count: 2, total_executions: 120, avg_pass_rate: 92.5, days_with_runs: 7 },
    suites: [{ suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 }],
  }
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    const { useCoverage } = await import('@/hooks/useMetrics')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: coverage, isLoading: false })
  })
  const renderCoverage = () =>
    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )

  // X2-3: below 768 px the breakdown and the gaps get the whole width; from 768 px the desktop's 1.65 : 1.
  it.each([
    [false, 'minmax(0, 1fr)'],
    [true, 'minmax(0, 1.65fr) minmax(0, 1fr)'],
  ])('body grid at min-width 768 = %s: %s', async (wide, columns) => {
    stubViewportWide(wide)
    onTestFinished(() => {
      vi.unstubAllGlobals()
    })
    const { container } = renderCoverage()
    await screen.findByRole('heading', { name: 'Test Coverage' })
    expect((container.querySelector('.body-grid') as HTMLElement).style.gridTemplateColumns).toBe(columns)
  })

  it('the coverage map and the heatmaps are mounted lazily, below the body grid (no flag asked since Phase D, S4)', async () => {
    // An observer that never reports the sections near: they stay placeholders.
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    onTestFinished(() => {
      vi.unstubAllGlobals()
    })
    const { container } = renderCoverage()
    expect(await screen.findByRole('heading', { name: 'Test Coverage' })).toBeInTheDocument()
    // The sections arrive as ONE lazy module (the page chunk holds only the small composite).
    const advanced = await waitFor(() => {
      const found = container.querySelector('[data-coverage-advanced]') as HTMLElement | null
      expect(found).not.toBeNull()
      return found as HTMLElement
    }, { timeout: LAZY_TIMEOUT })
    // Not near the reader yet: the map's placeholder, no section, no request.
    await waitFor(() =>
      expect([...advanced.querySelectorAll('[data-lazy-section]')].map((el) => el.getAttribute('data-lazy-section'))).toEqual([
        'coverage-map',
      ]),
    { timeout: LAZY_TIMEOUT })
    expect(container.querySelector('[data-catalogue-section]')).toBeNull()
    // After the body grid (the provenance footer that followed it is gone, P2).
    expect((container.querySelector('.body-grid') as HTMLElement).compareDocumentPosition(advanced)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    // P3: in the Coverage map tab (the default), which renders the map alone
    // (`sections`): the heatmap is not rendered at all, not merely hidden.
    const scope = advanced.closest('[data-coverage-scope]') as HTMLElement
    expect(scope).toHaveAttribute('data-coverage-scope', 'map')
    expect(scope.className).not.toMatch(/:hidden/)
    expect(container.querySelector('[data-lazy-section^="heatmap-"], [data-catalogue-section^="heatmap-"]')).toBeNull()
  })
})

describe('CoveragePage — suite breakdown fit (W3 C0 BEFORE notes 1 and 2)', () => {
  const realRect = HTMLElement.prototype.getBoundingClientRect
  let barWidth = 0
  // The rows' grid is measured too (F-18): a desktop card unless a test narrows it.
  let gridWidth = 800
  beforeEach(() => {
    analyticsControls.widgetIds = []
    gridWidth = 800
    // The rows' grid reports `gridWidth`; every other measured element (the bars) reports `barWidth`.
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      const width = this.hasAttribute('data-suite-rows') ? gridWidth : barWidth
      return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: 18, width, height: 18, toJSON: () => ({}) } as DOMRect
    }
  })
  afterEach(() => {
    HTMLElement.prototype.getBoundingClientRect = realRect
  })

  async function renderSuites(suites: { suite_name: string; passed: number; failed: number; skipped: number }[]) {
    const { useCoverage } = await import('@/hooks/useMetrics')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 18, suite_count: suites.length, total_executions: 1000, avg_pass_rate: 92.5, days_with_runs: 7 },
        suites: suites.map((s) => ({ ...s, unique_tests: 12, pass_rate: 90 })),
      },
      isLoading: false,
    })
    const view = render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )
    await within(view.container).findByText('Suite coverage breakdown')
    return view
  }
  const segments = (container: HTMLElement, suite: string) =>
    [...(container.querySelector(`[role="row"][aria-label^="${suite}:"]`) as HTMLElement).querySelectorAll('[data-suite-segment]')].map(
      (el) => [el.getAttribute('data-suite-segment'), el.textContent, el.getAttribute('title')],
    )

  it('a narrow segment hides its count instead of running it into the next one ("38" + "12" read "3812")', async () => {
    barWidth = 300
    const { container } = await renderSuites([{ suite_name: 'Auth', passed: 900, failed: 38, skipped: 12 }])
    // 300 px over 950: failed 12 px, skipped 3.8 px: neither count fits; every count stays in the title.
    expect(segments(container, 'Auth')).toEqual([
      ['passed', '900', '900 passed'],
      ['failed', '', '38 failed'],
      ['skipped', '', '12 skipped'],
    ])
    expect(container.textContent).not.toContain('3812')
  })

  it('a segment wide enough for its digits shows them, the same rule for every count', async () => {
    barWidth = 3000
    const { container } = await renderSuites([{ suite_name: 'Auth', passed: 900, failed: 38, skipped: 12 }])
    // failed 120 px, skipped 37.9 px: both fit two bold digits and their padding (21.1 px).
    expect(segments(container, 'Auth').map(([, text]) => text)).toEqual(['900', '38', '12'])
  })

  it('a count is shown only where it fits with its padding: one digit needs 13.6 px, two need 21.1 px', async () => {
    // 100 px over 100 executions: a segment is exactly its count in px.
    barWidth = 100
    const { container } = await renderSuites([
      { suite_name: 'One', passed: 72, failed: 14, skipped: 14 },
      { suite_name: 'Two', passed: 66, failed: 13, skipped: 21 },
    ])
    // 14 px: under the 21.1 px two digits need; 21 px: just under too.
    expect(segments(container, 'One').map(([, text]) => text)).toEqual(['72', '', ''])
    expect(segments(container, 'Two').map(([, text]) => text)).toEqual(['66', '', ''])
    cleanup()
    barWidth = 200
    const wider = await renderSuites([{ suite_name: 'Three', passed: 92, failed: 7, skipped: 1 }])
    // failed 14 px: "7" fits (13.6 px); skipped 2 px: "1" does not.
    expect(segments(wider.container, 'Three').map(([, text]) => text)).toEqual(['92', '7', ''])
  })

  it('draws no count before the bar is measured', async () => {
    barWidth = 0
    const { container } = await renderSuites([{ suite_name: 'Auth', passed: 900, failed: 38, skipped: 12 }])
    expect(segments(container, 'Auth').map(([, text]) => text)).toEqual(['', '', ''])
  })

  it('sizes the name column from the widest name (160 to 240 px) on one grid, so a long name is not cut by a fixed width', async () => {
    barWidth = 300
    const { container } = await renderSuites([
      { suite_name: 'Notifications', passed: 10, failed: 0, skipped: 2 },
      { suite_name: 'Auth', passed: 5, failed: 1, skipped: 0 },
    ])
    const rows = [...container.querySelectorAll('[role="row"]')] as HTMLElement[]
    const grid = rows[0].parentElement as HTMLElement
    expect(grid.style.gridTemplateColumns).toBe('fit-content(240px) minmax(0, 1fr) 64px 76px')
    for (const row of rows) {
      expect(row.parentElement).toBe(grid)
      expect(row.style.gridTemplateColumns).toBe('subgrid')
      expect(row.style.gridColumn).toBe('1 / -1')
      expect((row.firstElementChild as HTMLElement).style.minWidth).toBe('160px')
    }
    expect(within(container).getByTitle('Notifications')).toHaveTextContent('Notifications')
  })

  const SUITES_TWO = [
    { suite_name: 'Auth', passed: 610, failed: 38, skipped: 12 },
    { suite_name: 'Notifications', passed: 10, failed: 0, skipped: 2 },
  ]
  const nameCell = (row: HTMLElement) => within(row).getAllByRole('cell')[0]

  it('rows 320-399 px wide drop the runs column and hold the name at 160 px, so the bar keeps its width (F-18)', async () => {
    barWidth = 60
    gridWidth = 360
    const { container } = await renderSuites(SUITES_TWO)
    const card = container.querySelector('section[aria-label="Suite coverage breakdown"]') as HTMLElement
    const grid = card.querySelector('[data-suite-rows]') as HTMLElement
    // 160 + 16 + bar + 16 + 76: at 360 px the bar is 92 px (it was 0 px in a 322 px card, and a row 348 px wide).
    expect(grid.style.gridTemplateColumns).toBe('fit-content(160px) minmax(0, 1fr) 76px')
    const rows = within(card).getAllByRole('row')
    for (const row of rows) {
      expect(within(row).getAllByRole('cell')).toHaveLength(3)
      expect(nameCell(row).style.minWidth).toBe('160px')
      expect(nameCell(row).style.gridColumn).toBe('')
    }
    expect(card.textContent).not.toMatch(/\d runs?\b/)
    // The pass rate and every count stay: the cell, and the row's name.
    expect(within(rows[0]).getByText('92%')).toBeInTheDocument()
    expect(within(card).getByRole('row', { name: 'Auth: 610 passed, 38 failed, 12 skipped' })).toBeInTheDocument()
    const results = await axe.run(card, { rules: { 'color-contrast': { enabled: false } } })
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([])
  })

  it('rows under 320 px (the card at a 375 px viewport is ~170 px) put the name on its own line over the bar (F-18)', async () => {
    barWidth = 60
    for (const width of [319, 167]) {
      gridWidth = width
      const { container } = await renderSuites(SUITES_TWO)
      const card = container.querySelector('section[aria-label="Suite coverage breakdown"]') as HTMLElement
      const grid = card.querySelector('[data-suite-rows]') as HTMLElement
      // The bar takes everything the pass rate leaves: no fixed 160 px name column beside it.
      expect(grid.style.gridTemplateColumns).toBe('minmax(0, 1fr) 76px')
      for (const row of within(card).getAllByRole('row')) {
        expect(within(row).getAllByRole('cell')).toHaveLength(3)
        expect(nameCell(row).style.gridColumn).toBe('1 / -1')
        expect(nameCell(row).style.minWidth).toBe('0px')
      }
      expect(card.textContent).not.toMatch(/\d runs?\b/)
      expect(within(card).getByTitle('Notifications')).toHaveTextContent('Notifications')
      const results = await axe.run(card, { rules: { 'color-contrast': { enabled: false } } })
      expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([])
      cleanup()
    }
  })

  it('keeps the runs column from 400 px up, and before the card is measured (the committed desktop baselines)', async () => {
    for (const width of [400, 0]) {
      gridWidth = width
      barWidth = 200
      const { container } = await renderSuites([{ suite_name: 'Auth', passed: 610, failed: 38, skipped: 12 }])
      const grid = container.querySelector('[data-suite-rows]') as HTMLElement
      expect(grid.style.gridTemplateColumns).toBe('fit-content(240px) minmax(0, 1fr) 64px 76px')
      expect(within(grid).getAllByRole('cell')).toHaveLength(4)
      expect(within(grid).getByText('660 runs')).toBeInTheDocument()
      expect(nameCell(within(grid).getByRole('row')).style.minWidth).toBe('160px')
      cleanup()
    }
    // The compact layout starts exactly at 320 px.
    gridWidth = 320
    const { container } = await renderSuites(SUITES_TWO)
    expect((container.querySelector('[data-suite-rows]') as HTMLElement).style.gridTemplateColumns).toBe(
      'fit-content(160px) minmax(0, 1fr) 76px',
    )
  })

  it('the rows are a real table (rows of cells, a table parent): axe finds nothing on the card (FK2-4)', async () => {
    barWidth = 300
    const { container } = await renderSuites([
      { suite_name: 'Auth', passed: 610, failed: 38, skipped: 12 },
      { suite_name: 'Notifications', passed: 10, failed: 0, skipped: 2 },
    ])
    const card = container.querySelector('section[aria-label="Suite coverage breakdown"]') as HTMLElement
    const table = within(card).getByRole('table', { name: 'Suites' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getAllByRole('cell')).toHaveLength(4)
    expect(within(card).getByRole('row', { name: 'Auth: 610 passed, 38 failed, 12 skipped' })).toBeInTheDocument()
    const results = await axe.run(card, { rules: { 'color-contrast': { enabled: false } } })
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([])
  })
})

// P2 "remove the noise" (UX redesign): anything not built is not rendered.
// Every control below only raised a not-built toast; every value below was
// invented. Each test fails if its item comes back.
describe('CoveragePage P2: no stubs, no invented values', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 30 })
  })

  type Suite = { suite_name: string; unique_tests: number; passed: number; failed: number; skipped: number; pass_rate: number }

  async function renderCoverage(suites: Suite[], trend: TrendPoint[] = [], daysWithRuns = 1) {
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')
    const total = suites.reduce((n, x) => n + x.passed + x.failed + x.skipped, 0)
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 18, suite_count: suites.length, total_executions: total, avg_pass_rate: 80, days_with_runs: daysWithRuns },
        suites,
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    const view = render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes><Route path="/coverage" element={<CoveragePage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Test Coverage' })
    return view
  }

  // A failing suite, an untagged group and a 1-of-30-day cadence: every issue
  // row (and the untagged callout) that once carried a stub renders.
  const NOISY: Suite[] = [
    { suite_name: 'Payments', unique_tests: 6, passed: 40, failed: 10, skipped: 0, pass_rate: 80 },
    { suite_name: 'Unknown Suite', unique_tests: 3, passed: 5, failed: 0, skipped: 0, pass_rate: 100 },
  ]

  it('has none of the not-built controls: issue rows, verdict, untagged callout, header', async () => {
    await renderCoverage(NOISY)
    // P3: the verdict's issue rows are gone with the card; the facts they
    // stated stay on the page: the untagged executions (the callout and the
    // suites tile) and the 1-of-30-day cadence (the Run cadence tile).
    expect(screen.queryByText(/executions un-tagged/)).toBeNull()
    expect(screen.getByRole('region', { name: 'Untagged executions' })).toHaveTextContent("5 of 55 executions (9%) didn't carry a suite label")
    const kpis = within(screen.getByRole('region', { name: 'Coverage metrics' }))
    expect(kpis.getByText('Test suites').closest('[data-metric-card]')).toHaveTextContent('+ 1 untagged')
    expect(kpis.getByText('Run cadence').closest('[data-metric-card]')).toHaveTextContent('1 / 30 days')
    expect(kpis.getByText('Run cadence').closest('[data-metric-card]')).toHaveTextContent('Schedule may be paused')
    for (const name of [
      /Fix tagging/, /^Schedule/, /Configure schedule/, /Apply suite labels/, /Open runner config/,
      /Decision trail/, /Customize/, /^Open$/,
    ]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    // The real actions stay, in the header's ⋯ menu (P3).
    expect(menuItems()).toEqual(['Compare to previous window', 'Export CSV', 'Open triage queue', 'View all suites'])
    expect(screen.getByRole('menuitem', { name: 'Open triage queue' })).toHaveAttribute('href', '/failures?days=30')
  })

  it('renders no recommended-actions card and no provenance footer', async () => {
    await renderCoverage(NOISY)
    expect(screen.queryByRole('region', { name: 'Recommended actions' })).toBeNull()
    expect(screen.queryByText(/routed by role/)).toBeNull()
    expect(screen.queryByText('Provenance')).toBeNull()
    expect(screen.queryByText(/coverage analyzer v1/)).toBeNull()
    expect(screen.queryByText(/evidence item/)).toBeNull()
  })

  it('shows the real Unique tests number with no invented delta', async () => {
    await renderCoverage(NOISY)
    const kpis = screen.getByRole('region', { name: 'Coverage metrics' })
    const cell = within(kpis).getByText('Unique tests').closest('[data-metric-card]') as HTMLElement
    expect(cell).toHaveTextContent(/^Unique tests18$/)
    expect(within(kpis).queryByText(/no change/)).toBeNull()
    expect(within(kpis).queryByText(/vs prev/)).toBeNull()
  })

  it('draws the Total executions delta only when it is measured, and says what it compares', async () => {
    // One trend day: no halves to compare, so no delta at all (it read "no change").
    await renderCoverage(NOISY, [{ date: utcDayIso(), passed: 10, failed: 0, skipped: 0, broken: 0, pass_rate: 100 }])
    let cell = within(screen.getByRole('region', { name: 'Coverage metrics' })).getByText('Total executions').closest('[data-metric-card]') as HTMLElement
    expect(cell).toHaveTextContent(/^Total executions55$/)
    cleanup()
    // Earlier half 10, later half 15: +50%, the later half against the earlier one.
    await renderCoverage(NOISY, [
      { date: shiftDayIso(utcDayIso(), -3), passed: 10, failed: 0, skipped: 0, broken: 0, pass_rate: 100 },
      { date: utcDayIso(), passed: 15, failed: 0, skipped: 0, broken: 0, pass_rate: 100 },
    ])
    cell = within(screen.getByRole('region', { name: 'Coverage metrics' })).getByText('Total executions').closest('[data-metric-card]') as HTMLElement
    // The compact tile words the direction ("Up") and its judgement ("(better)", more executions).
    expect(cell).toHaveTextContent('Up 50% · later vs earlier half of window')
    expect(cell.querySelector('[data-trend-judgement]')).toHaveTextContent('(better)')
  })

  it('links "View all suites" and the breakdown footer to the suites list, not a nameless suite page', async () => {
    await renderCoverage(NOISY)
    expect(screen.getByRole('link', { name: /Browse suites/ })).toHaveAttribute('href', '/suites')
    // P3: "View all suites" is a ⋯ menu item.
    menuItems()
    expect(screen.getByRole('menuitem', { name: 'View all suites' })).toHaveAttribute('href', '/suites')
    for (const link of screen.getAllByRole('link')) {
      expect(link.getAttribute('href')).not.toBe('/coverage/suite')
    }
  })

  it('counts every untagged group the breakdown draws', async () => {
    await renderCoverage([
      ...NOISY,
      { suite_name: '', unique_tests: 1, passed: 2, failed: 0, skipped: 0, pass_rate: 100 },
    ])
    const card = screen.getByRole('region', { name: 'Suite coverage breakdown' })
    const untaggedRows = within(card).getAllByRole('row', { name: /^Untagged group:/ })
    expect(untaggedRows).toHaveLength(2)
    expect(card).toHaveTextContent(`Showing 1 suite + ${untaggedRows.length} untagged groups`)
  })
})

// P3 (UX redesign, the page template): header (filters, Views, ⋯) · KPI strip
// · the suite table (`data-primary`) beside Run cadence · the score
// disclosure · the page's tabs (Coverage map · Env × release heatmap, in
// `?tab=`). Every section was moved, none rebuilt.
describe('CoveragePage P3: the page template', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 30 })
    // An observer that never reports a section near: the Wave 3 sections stay placeholders (no chunk, no request).
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  type Suite = { suite_name: string; unique_tests: number; passed: number; failed: number; skipped: number; pass_rate: number }
  const SUITES: Suite[] = [
    { suite_name: 'Payments', unique_tests: 6, passed: 40, failed: 10, skipped: 0, pass_rate: 80 },
    { suite_name: 'Auth', unique_tests: 4, passed: 32, failed: 3, skipped: 0, pass_rate: 91 },
  ]

  /** The router's location, rendered so a test can read the URL the page writes. */
  function Location() {
    const location = useLocation()
    return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
  }

  async function renderAt(path: string, suites: Suite[] = SUITES) {
    const { useCoverage, useTrendData } = await import('@/hooks/useMetrics')
    const total = suites.reduce((n, x) => n + x.passed + x.failed + x.skipped, 0)
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: suites.length, total_executions: total, avg_pass_rate: 85, days_with_runs: 20 },
        suites,
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    const view = render(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/coverage" element={<><CoveragePage /><Location /></>} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Test Coverage' })
    return view
  }

  const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
  const tab = (name: string) => within(screen.getByRole('tablist', { name: 'Coverage views' })).getByRole('tab', { name })

  it('puts the one primary content (the suite table) before the page tab bar and the disclosure', async () => {
    await renderAt('/coverage')
    const primary = document.querySelectorAll('[data-primary]')
    expect(primary).toHaveLength(1)
    expect(primary[0]).toHaveAttribute('aria-label', 'Suite coverage breakdown')
    expect(within(primary[0] as HTMLElement).getByRole('table', { name: 'Suites' })).toBeInTheDocument()
    expect(follows(screen.getByRole('region', { name: 'Coverage metrics' }), primary[0])).toBe(true)
    const tablists = screen.getAllByRole('tablist')
    expect(tablists.map((t) => t.getAttribute('aria-label'))).toEqual(['Coverage views'])
    const disclosures = document.querySelectorAll('[data-disclosure]')
    expect(disclosures).toHaveLength(1)
    for (const later of [...tablists, ...disclosures]) expect(follows(primary[0], later)).toBe(true)
    expect(document.querySelectorAll('[data-kpi-strip] [data-metric-card="compact"]')).toHaveLength(5)
    // No verdict card above the table (its score is the disclosure's).
    expect(screen.queryByRole('region', { name: 'Coverage verdict' })).toBeNull()
  })

  it('has the suite filter, the window picker and Views in the header, every other action in ⋯, and the help ?', async () => {
    await renderAt('/coverage')
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const toolbar = header.querySelector('[data-page-toolbar]') as HTMLElement
    const suite = within(toolbar).getByDisplayValue('All suites')
    const windows = within(toolbar).getByRole('radiogroup', { name: 'Time window' })
    const views = toolbar.querySelector('[data-saved-views-trigger]') as HTMLElement
    expect(views).toHaveTextContent('Views')
    expect(follows(suite, windows) && follows(windows, views)).toBe(true)
    expect(within(windows).getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    expect(within(header).getByRole('button', { name: 'Help: Test Coverage' })).toHaveAttribute('data-help-topic', 'dashboards')
    // No page-level button outside the header's ⋯ (the verdict card's two and the header's two moved in).
    for (const name of [/^Export/, /Compare to previous window/, /Open triage queue/, /View all suites/]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
      expect(screen.queryByRole('link', { name })).toBeNull()
    }
    expect(menuItems()).toEqual(['Compare to previous window', 'Export CSV', 'Open triage queue', 'View all suites'])
  })

  it('keeps the score collapsed: its header states the verdict and the score; it opens to the meter, the dimensions and the gaps', async () => {
    await renderAt('/coverage')
    const toggle = screen.getByRole('button', { name: /How this score is computed/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle.textContent).toMatch(/(Healthy|At risk|Blocked) · health \d+ \/ 100$/)
    expect(screen.queryByRole('region', { name: 'Coverage gaps' })).toBeNull()
    openScore()
    const verdict = screen.getByRole('region', { name: 'Coverage verdict' })
    expect(within(verdict).getByRole('meter', { name: 'Coverage health score' })).toBeInTheDocument()
    for (const dim of ['Pass rate', 'Tag quality', 'Run cadence', 'Suite breadth']) expect(within(verdict).getByText(dim)).toBeInTheDocument()
    expect(within(verdict).getByRole('region', { name: 'Coverage gaps' })).toBeInTheDocument()
    expect(verdict).toHaveClass('grid-cols-1', 'lg:grid-cols-[1.45fr_1fr]')
  })

  it.each([
    ['/coverage', 'map', 'Coverage map'],
    ['/coverage?tab=map', 'map', 'Coverage map'],
    ['/coverage?tab=heatmap', 'heatmap', 'Env × release heatmap'],
    ['/coverage?tab=nope', 'map', 'Coverage map'],
  ] as const)('%s selects the %s tab and renders its section alone', async (path, id, label) => {
    // An observer that never reports the section near: it stays its placeholder (no request).
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    onTestFinished(() => {
      vi.unstubAllGlobals()
    })
    const { container } = await renderAt(path)
    expect(tab(label)).toHaveAttribute('aria-selected', 'true')
    const panel = container.querySelector('[data-tab-panel]') as HTMLElement
    expect(panel).toHaveAttribute('data-tab-panel', id)
    expect(panel).toHaveAttribute('data-coverage-scope', id)
    // Composition, not concealment (`sections`): no CSS hides the other tab's section; it is not rendered.
    expect(panel.className).not.toMatch(/:hidden/)
    await waitFor(() => expect(panel.querySelector('[data-coverage-advanced]')).not.toBeNull(), { timeout: LAZY_TIMEOUT })
    const own = id === 'map' ? 'coverage-map' : 'heatmap-suite_environment'
    await waitFor(
      () => expect([...panel.querySelectorAll('[data-lazy-section]')].map((el) => el.getAttribute('data-lazy-section'))).toEqual([own]),
      { timeout: LAZY_TIMEOUT },
    )
    expect(container.querySelectorAll('[data-lazy-section]')).toHaveLength(1)
  })

  it('a tab click writes ?tab=, the default clears it, and the panel remounts', async () => {
    const { container } = await renderAt('/coverage')
    const before = container.querySelector('[data-tab-panel]')
    fireEvent.click(tab('Env × release heatmap'))
    expect(screen.getByTestId('location')).toHaveTextContent('/coverage?tab=heatmap')
    const after = container.querySelector('[data-tab-panel]')
    expect(after).not.toBe(before)
    fireEvent.click(tab('Coverage map'))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/coverage$/)
  })

  it('flags each suite\'s own gap in the table: no passing run, too few runs; a suite with neither, and the untagged group, carry none', async () => {
    const { container } = await renderAt('/coverage', [
      { suite_name: 'Payments', unique_tests: 6, passed: 40, failed: 10, skipped: 0, pass_rate: 80 },
      { suite_name: 'Broken', unique_tests: 2, passed: 0, failed: 6, skipped: 0, pass_rate: 0 },
      { suite_name: 'Tiny', unique_tests: 1, passed: 2, failed: 0, skipped: 0, pass_rate: 100 },
      { suite_name: 'Unknown Suite', unique_tests: 1, passed: 1, failed: 0, skipped: 0, pass_rate: 100 },
    ])
    const table = within(container.querySelector('[data-primary]') as HTMLElement).getByRole('table', { name: 'Suites' })
    // A fifth column, only because a suite has a gap.
    expect((table as HTMLElement).style.gridTemplateColumns).toBe('fit-content(240px) minmax(0, 1fr) 64px 76px auto')
    const flagOf = (name: RegExp) => {
      const row = within(table).getByRole('row', { name })
      expect(within(row).getAllByRole('cell')).toHaveLength(5)
      return row.querySelector('[data-suite-gap]')
    }
    expect(flagOf(/^Payments:/)).toBeNull()
    expect(flagOf(/^Broken:/)).toHaveTextContent('No passing run')
    expect(flagOf(/^Broken:/)).toHaveAttribute('title', 'No passing run in 30d — blocks regression baseline')
    expect(flagOf(/^Broken:/)).toHaveAttribute('data-suite-gap', 'bad')
    expect(flagOf(/^Tiny:/)).toHaveTextContent('Too few runs')
    expect(flagOf(/^Tiny:/)).toHaveAttribute('data-suite-gap', 'warn')
    expect(flagOf(/^Untagged group:/)).toBeNull()
  })

  it('draws no gap column when no suite has a gap', async () => {
    const { container } = await renderAt('/coverage')
    const table = within(container.querySelector('[data-primary]') as HTMLElement).getByRole('table', { name: 'Suites' }) as HTMLElement
    expect(table.style.gridTemplateColumns).toBe('fit-content(240px) minmax(0, 1fr) 64px 76px')
    expect(table.querySelector('[data-suite-gap]')).toBeNull()
  })

  it('states the cadence strip\'s reading as its title\'s tooltip, not a paragraph', async () => {
    await renderAt('/coverage')
    const card = screen.getByRole('region', { name: 'Run cadence' })
    expect(within(card).getByRole('heading', { name: /^Run cadence/ })).toHaveAttribute('title', expect.stringMatching(/^Each cell is a day/))
    expect(card).not.toHaveTextContent('Empty cells are missed windows')
  })
})
