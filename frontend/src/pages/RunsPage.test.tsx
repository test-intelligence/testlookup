/**
 * Tests for ``buildBisectHref`` — the pure helper that powers every
 * "Bisect from last green" / "Start bisect" CTA on the /runs page.
 *
 * The CTAs used to toast a "bisect modal later" placeholder; they
 * now navigate to /runs/compare with pre-filled left / right / suite
 * params. Pinning the URL construction here is a cheap regression
 * guard against:
 *
 *   - Stripping the ``suite`` filter when one side has it and the
 *     other doesn't (would silently widen the compare beyond the
 *     suite the user is investigating).
 *   - Reordering left/right (the compare page treats ``left`` as the
 *     baseline; swapping flips every delta's classification).
 *   - Silently returning a partial URL when one side is missing
 *     (would land the user on a half-populated compare page instead
 *     of toasting a clear "no green run found" reason).
 *
 * Below them: the page's parts (the per-build pass rate, the signature
 * split, the build strip, the health meter) and the page itself, laid out on
 * the UX redesign P3 template.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { useRuns } from '@/hooks/useRuns'
import agentService from '@/services/agentService'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import RunsPage, {
  buildBisectHref, buildPipelineModel, BuildPassRate, BuildVelocityCard, HealthMeter, SignatureClusters,
} from './RunsPage'

// Only the full-page describes at the bottom use these; the pure helpers
// and components above never touch them.
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
  }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

// The upload gate (the `manual_upload` flag) and the role, per test.
const flags = vi.hoisted(() => ({ manual_upload: false } as Record<string, boolean>))
const perms = vi.hoisted(() => ({ isQaEngineer: false }))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useFeatureFlags')>()),
  useFeatureEnabled: (key: string) => flags[key] ?? false,
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => perms }))
vi.mock('@/components/runs/UploadReportModal', () => ({
  default: () => <div data-testid="upload-report-modal" />,
}))
vi.mock('@/services/agentService', () => ({
  default: {
    triggerPipeline: vi.fn(async () => ({})),
    triggerDeepPipeline: vi.fn(async () => ({})),
    bulkTriggerPipelines: vi.fn(async (ids: string[]) => ({ queued: ids.length })),
  },
}))

// Build the href and assert it is non-null, returning the narrowed
// string so the URL-introspection cases can read it without a `!`
// non-null assertion (which the lint ratchet forbids). The explicit
// throw narrows the union for TypeScript; the `expect` keeps the
// failure message readable when the helper unexpectedly returns null.
function expectHref(model: Parameters<typeof buildBisectHref>[0]): string {
  const href = buildBisectHref(model)
  expect(href).not.toBeNull()
  if (href === null) throw new Error('buildBisectHref returned null')
  return href
}

function _testRun(overrides: Partial<{
  id: string
  status: string
  primary_suite_name: string | null
  build_number: string
  created_at: string
}> = {}) {
  // Cast to any so the helper sees only the fields it actually reads —
  // mirrors how it's invoked from the page (where the full TestRun has
  // ~20 fields most of which the helper ignores).
  return {
    id: '00000000-0000-0000-0000-000000000001',
    status: 'PASSED',
    primary_suite_name: null,
    build_number: '1',
    created_at: '2026-05-16T00:00:00Z',
    ...overrides,
  } as never
}

describe('buildBisectHref', () => {
  it('returns null when there is no green run to bisect against', () => {
    const href = buildBisectHref({
      lastGreen: null,
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED' }),
    })
    expect(href).toBeNull()
  })

  it('returns null when there is no failing run to bisect', () => {
    const href = buildBisectHref({
      lastGreen: _testRun({ id: 'green-1', status: 'PASSED' }),
      latestFailedRun: null,
    })
    expect(href).toBeNull()
  })

  it('builds a /runs/compare URL with left=lastGreen and right=latestFailed', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'payments-e2e' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: 'payments-e2e' }),
    })
    expect(href).toContain('/runs/compare?')
    expect(href).toContain('mode=manual')
    expect(href).toContain('left=green-1')
    expect(href).toContain('right=failed-1')
    expect(href).toContain('suite=payments-e2e')
  })

  it('prefers the failing run\'s suite when both sides have a suite name', () => {
    // The user is investigating the failing run, so the compare view
    // should default to the failing run's suite filter — not the
    // green run's, even if they differ.
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'old-suite' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: 'new-suite' }),
    })
    expect(href).toContain('suite=new-suite')
    expect(href).not.toContain('suite=old-suite')
  })

  it('falls back to the green run\'s suite when the failing run has none', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: 'payments-e2e' }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: null }),
    })
    expect(href).toContain('suite=payments-e2e')
  })

  it('omits the suite param entirely when neither side has a suite name', () => {
    // Adding ``&suite=`` (empty) would land on the compare page with
    // a stale empty filter. Cleaner to leave it off so the compare
    // page falls back to its default behaviour.
    const href = expectHref({
      lastGreen: _testRun({ id: 'green-1', primary_suite_name: null }),
      latestFailedRun: _testRun({ id: 'failed-1', status: 'FAILED', primary_suite_name: null }),
    })
    expect(href).not.toContain('suite=')
  })

  it('URL-encodes special characters in the suite name', () => {
    const href = expectHref({
      lastGreen: _testRun({ id: 'g', primary_suite_name: 'Realistic TestNG client examples' }),
      latestFailedRun: _testRun({ id: 'f', status: 'FAILED', primary_suite_name: 'Realistic TestNG client examples' }),
    })
    // Either ``+`` (form-style) or ``%20`` (path-style) are both valid
    // URL-encodings of a space; URLSearchParams uses ``+``.
    expect(href).toMatch(/suite=Realistic[+%20]TestNG[+%20]client[+%20]examples/)
  })
})

// ── Wave 2.5 (VIZ-104, OD-1): the glyphs, the health meter and the build
// strip draw only what the runs say. UX redesign P3 moved the KPI strip's two
// real glyphs: the per-build pass rate into "Build history", the signature
// split into the failure-signature side panel. ───────────────────────────────

/** A run as the API lists it; `created_at` orders it. */
function run(i: number, over: Partial<TestRun> = {}): TestRun {
  return {
    id: `run-${i}`,
    build_number: 100 + i,
    status: 'PASSED',
    passed_tests: 9,
    failed_tests: 1,
    skipped_tests: 0,
    total_tests: 10,
    pass_rate: 90,
    created_at: `2026-09-${String(10 + i).padStart(2, '0')}T12:00:00Z`,
    ...over,
  }
}

/** The API's order: NEWEST first. */
const newestFirst = (runs: TestRun[]) => [...runs].reverse()

/** Any drawing in the pass-rate cell. */
const GLYPH_SVG = 'svg:not(.lucide)'

const passRateCell = () => screen.getByRole('region', { name: 'Average pass rate' })

describe('BuildPassRate — the per-build pass rate, a line only where the runs have one (OD-1)', () => {
  const runs = newestFirst([
    run(1, { pass_rate: 50, status: 'FAILED', passed_tests: 5, failed_tests: 5 }),
    run(2, { pass_rate: 70, status: 'FAILED', passed_tests: 7, failed_tests: 3 }),
    run(3, { pass_rate: 95, status: 'PASSED' }),
  ])

  it('draws every build, OLDEST first, so the latest point is the newest build', () => {
    const model = buildPipelineModel(runs)
    expect(model.passRateSeries).toEqual([50, 70, 95])
    render(<BuildPassRate model={model} />)
    const spark = within(passRateCell()).getByRole('img')
    expect(spark).toHaveAccessibleName(
      'Pass rate per build, oldest first: 3 points, latest 95.0%, min 50.0%, max 95.0%',
    )
  })

  it('leaves a gap for a build with no result yet — never a 0, never joined across', () => {
    const model = buildPipelineModel(
      newestFirst([
        run(1, { pass_rate: 80 }),
        run(2, { pass_rate: 0, status: 'RUNNING', passed_tests: 0, failed_tests: 0 }),
        run(3, { pass_rate: 0, total_tests: 0, passed_tests: 0, failed_tests: 0 }),
        run(4, { pass_rate: 90 }),
      ]),
    )
    expect(model.passRateSeries).toEqual([80, null, null, 90])
    const { container } = render(<BuildPassRate model={model} />)
    expect(within(passRateCell()).getByRole('img')).toHaveAccessibleName(
      /2 points, .*min 80\.0%.*2 not measured$/,
    )
    // Two runs of one point each: two subpaths, not one line across the gap.
    const d = container.querySelector('path[data-part="line"]')?.getAttribute('d') ?? ''
    expect(d.match(/M /g)).toHaveLength(2)
  })

  it('averages the pass rate over MEASURED builds only — the number agrees with the line', () => {
    // 80 and 90 are measured; the running build and the 0-test build have no
    // pass rate. Counting them as 0 % read 42.5 %.
    const model = buildPipelineModel(
      newestFirst([
        run(1, { pass_rate: 80 }),
        run(2, { pass_rate: 0, status: 'RUNNING', passed_tests: 0, failed_tests: 0 }),
        run(3, { pass_rate: 0, total_tests: 0, passed_tests: 0, failed_tests: 0 }),
        run(4, { pass_rate: 90 }),
      ]),
    )
    expect(model.avgPassRate).toBe(85)
    render(<BuildPassRate model={model} />)
    const cell = passRateCell()
    expect(cell).toHaveTextContent('85.0%')
    expect(cell).toHaveTextContent('2 of 4 builds measured')
  })

  it('shows "—", not measured, when no build in the window has a pass rate — never 0 %', () => {
    const model = buildPipelineModel(
      newestFirst([
        run(1, { pass_rate: 0, status: 'RUNNING', passed_tests: 0, failed_tests: 0 }),
        run(2, { pass_rate: 0, status: 'IN_PROGRESS', passed_tests: 0, failed_tests: 0 }),
      ]),
    )
    expect(model.avgPassRate).toBeNull()
    render(<BuildPassRate model={model} />)
    const cell = passRateCell()
    expect(cell).toHaveTextContent('—')
    expect(cell).toHaveTextContent('not measured')
    expect(cell).not.toHaveTextContent('0.0')
    expect(cell).not.toHaveTextContent('%')
  })

  it('draws no pass-rate sparkline from fewer than two measured builds', () => {
    render(<BuildPassRate model={buildPipelineModel([run(1)])} />)
    expect(within(passRateCell()).queryByRole('img')).toBeNull()
    expect(passRateCell().querySelector(GLYPH_SVG)).toBeNull()
  })
})

describe('SignatureClusters — the side panel splits the failed builds by signature', () => {
  function renderClusters(runs: TestRun[]) {
    const model = buildPipelineModel(runs)
    return render(
      <MemoryRouter>
        <SignatureClusters
          primaryCluster={model.primaryCluster}
          outlierClusters={model.outlierClusters}
          totalRuns={model.totalRuns}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
  }

  it('splits the failed builds into the primary signature vs the rest', () => {
    renderClusters(
      newestFirst([
        run(1, { status: 'FAILED', passed_tests: 8, failed_tests: 2 }),
        run(2, { status: 'FAILED', passed_tests: 8, failed_tests: 2 }),
        run(3, { status: 'FAILED', passed_tests: 6, failed_tests: 4 }),
        run(4),
      ]),
    )
    const meter = screen.getByRole('meter', { name: 'Failed builds by signature' })
    expect(meter).toHaveAttribute('aria-valuetext', '3 of 3; Primary signature 2, Other signatures 1')
    expect(screen.getByText(/2 signatures across 3 failed builds/)).toBeInTheDocument()
    expect(screen.getByText(/8 pass \/ 2 fail \/ 10 total/)).toBeInTheDocument()
  })

  it('draws no split when nothing failed', () => {
    renderClusters([run(1), run(2)])
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.getByText(/nothing to cluster/i)).toBeInTheDocument()
  })
})

describe('BuildVelocityCard — the kit DayStrip, one cell per build', () => {
  it('keeps the aggregate name the visual spec finds it by, and marks the newest build', () => {
    const model = buildPipelineModel(
      newestFirst([run(1), run(2, { status: 'FAILED' }), run(3, { status: 'RUNNING' }), run(4)]),
    )
    render(<BuildVelocityCard cells={model.velocityCells} redStreak={model.redStreak} />)
    expect(screen.getByRole('img', { name: /^Build velocity over the last 14 builds/ })).toHaveAccessibleName(
      'Build velocity over the last 14 builds: 2 passed, 1 failed, 11 no-build cells.',
    )
    expect(model.velocityCells).toHaveLength(14)
    // R2 F2 / R1 F8: the empty cells pad the OLDEST end, so the newest build
    // sits under "Now" and nothing invented is newer than it.
    expect(model.velocityCells.map((c) => c.label)).toEqual([
      ...Array(10).fill('no build'),
      '#101 · pass',
      '#102 · fail',
      '#103 · no result',
      '#104 · pass',
    ])
    expect(model.velocityCells.filter((c) => c.marker === 'today').map((c) => c.key)).toEqual(['run-4'])
    expect(model.velocityCells[13].key).toBe('run-4')
    // R1 F7: 14 cells, the newest "Now", so the oldest is 13 builds before it.
    const legend = screen.getByRole('img', { name: /^Build velocity/ }).closest('[data-day-strip]')?.querySelector('[data-day-strip-legend]')
    expect(legend).toHaveTextContent(/^13 builds ago/)
    expect(legend).toHaveTextContent(/Now$/)
  })

  it('reads the newest build at the "Now" end: End on the strip names it', () => {
    const model = buildPipelineModel(newestFirst([run(1), run(2, { status: 'FAILED' })]))
    render(<BuildVelocityCard cells={model.velocityCells} redStreak={model.redStreak} />)
    const strip = screen.getByRole('group', { name: /^Build velocity/ })
    strip.focus()
    fireEvent.keyDown(strip, { key: 'End' })
    const readout = strip.closest('[data-day-strip]')?.querySelector('[data-chart-readout]') as HTMLElement
    expect(readout.textContent).toContain('#102 · fail')
    expect(readout.textContent).not.toMatch(/no build/i)
  })
})

describe('HealthMeter — the kit GaugeBar', () => {
  it('is a meter named "Pipeline health" whose value text carries the band in words', () => {
    const model = { ...buildPipelineModel([run(1)]), composite: 72 }
    render(<HealthMeter model={model} verdict="HEALTHY" />)
    const meter = screen.getByRole('meter', { name: 'Pipeline health' })
    expect(meter).toHaveAttribute('aria-valuenow', '72')
    expect(meter).toHaveAttribute('aria-valuetext', '72 of 100, Stable')
  })

  it('is not measured (an empty track, no marker) while the verdict is PENDING', () => {
    const { container } = render(<HealthMeter model={buildPipelineModel([])} verdict="PENDING" />)
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.getByRole('img', { name: 'Pipeline health: not measured' })).toBeInTheDocument()
    expect(container.querySelector('[data-gauge-marker]')).toBeNull()
  })
})

// ── The page ────────────────────────────────────────────────────────────────

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()

/** One green build, then three red ones sharing a signature, none carrying
 *  branch / release metadata: every removed block used to render here. */
function brokenWindow(): TestRun[] {
  return newestFirst([
    run(1, { status: 'PASSED', passed_tests: 10, failed_tests: 0, pass_rate: 100, created_at: hoursAgo(30) }),
    run(2, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80, created_at: hoursAgo(20) }),
    run(3, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80, created_at: hoursAgo(10) }),
    run(4, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80, created_at: hoursAgo(2) }),
  ])
}

/** Every build green: no cluster, nothing to bisect. */
function greenWindow(): TestRun[] {
  return newestFirst([
    run(1, { status: 'PASSED', passed_tests: 10, failed_tests: 0, pass_rate: 100, created_at: hoursAgo(30) }),
    run(2, { status: 'PASSED', passed_tests: 10, failed_tests: 0, pass_rate: 100, created_at: hoursAgo(5) }),
  ])
}

function renderRunsPage(runs: TestRun[], path = '/runs') {
  vi.mocked(useRuns).mockReturnValue({
    data: { items: runs, total: runs.length, page: 1, size: 500, pages: 1 },
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  } as never)
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={[path]}><RunsPage /></MemoryRouter>
    </SWRConfig>,
  )
}

const banner = () => document.querySelector('[data-status-banner]') as HTMLElement
const primary = () => document.querySelector('[data-primary]') as HTMLElement
const disclosureButton = (title: string) => screen.getByRole('button', { name: new RegExp(`^${title}`) })
const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

beforeEach(() => {
  flags.manual_upload = false
  perms.isQaEngineer = false
  useTimeWindowStore.setState({ days: 30 })
  vi.mocked(agentService.bulkTriggerPipelines).mockClear()
})

// UX redesign P2: anything not built is not rendered. The workflow ribbon
// (an invented "stages × 7" evidence count), the provenance footer ("+7",
// "3 tools", a toast-only "Decision trail"), the recommended-actions card
// (invented @team-checkout / @release-qa / @releng and "Auto-deploy is
// currently armed") and every toast-only CTA are gone.
describe('RunsPage — renders only what it really does (P2)', () => {
  it('has no workflow ribbon, provenance footer or recommended actions', () => {
    renderRunsPage(brokenWindow())
    expect(banner()).not.toBeNull()
    expect(screen.queryByRole('region', { name: 'Run workflow' })).toBeNull()
    expect(screen.queryByText(/Run workflow/i)).toBeNull()
    expect(screen.queryByText(/^compact$/i)).toBeNull()
    expect(screen.queryByText(/evidence/i)).toBeNull()
    expect(screen.queryByText(/stages/i)).toBeNull()
    expect(screen.queryByText(/3 tools/)).toBeNull()
    expect(screen.queryByText(/Provenance/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Decision trail/i })).toBeNull()
    expect(screen.queryByText('Recommended actions')).toBeNull()
  })

  it('invents no owner handles and no deploy state', () => {
    renderRunsPage(brokenWindow())
    for (const handle of [/@team-checkout/, /@release-qa/, /@releng/]) {
      expect(screen.queryByText(handle)).toBeNull()
    }
    expect(screen.queryByText(/currently armed/i)).toBeNull()
  })

  it('renders no toast-only CTA: Diff against HEAD, Hold deploys, Open cluster, Fix reporter, Triage cluster', () => {
    renderRunsPage(brokenWindow())
    for (const name of [
      /Diff against HEAD/i, /Hold/i, /Open cluster/i, /Fix reporter/i, /Triage cluster/i, /^Start$/,
    ]) {
      expect(screen.queryByRole('button', { name }), String(name)).toBeNull()
    }
  })
})

// UX redesign P3 (`02-design-spec.md` §2, §5 "Runs"): header (Upload report ·
// ⋯) · toolbar · StatusBanner · the runs table · Disclosures; the signatures in
// a side panel. The VerdictCard and the KPI strip that repeated it are gone.
describe('RunsPage — the page template (P3)', () => {
  it('puts the runs table first: the primary content precedes every Disclosure and tab bar', () => {
    renderRunsPage(brokenWindow())
    const main = primary()
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    expect(within(main).getByRole('table')).toBeInTheDocument()
    expect(within(main).getAllByRole('row')).toHaveLength(5)
    const disclosures = Array.from(document.querySelectorAll('[data-disclosure]'))
    expect(disclosures).toHaveLength(2)
    for (const later of [...disclosures, ...document.querySelectorAll('[role="tablist"]')]) {
      expect(follows(main, later)).toBe(true)
    }
    // Above it, in order: the header, the toolbar, the banner.
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const toolbar = document.querySelector('[data-runs-toolbar]') as HTMLElement
    expect(follows(header, toolbar)).toBe(true)
    expect(follows(toolbar, banner())).toBe(true)
    expect(follows(banner(), main)).toBe(true)
  })

  it('deletes the verdict card and the KPI strip that repeated the banner', () => {
    renderRunsPage(brokenWindow())
    expect(screen.queryByRole('region', { name: 'Pipeline verdict' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Run KPIs' })).toBeNull()
    for (const label of ['Builds failed', 'Unique failures', 'Last green build']) {
      expect(screen.queryByText(label, { exact: true }), label).toBeNull()
    }
    // The verdict's prose became the banner's one line.
    expect(screen.queryByText(/Stop re-running/)).toBeNull()
  })

  it('states the verdict in one banner: failing builds, last green, red streak, average pass rate, Bisect', () => {
    renderRunsPage(brokenWindow())
    expect(banner()).toHaveAttribute('data-status-banner', 'fail')
    expect(banner()).toHaveTextContent('FAILING')
    expect(banner()).toHaveTextContent('Pipeline broken · 3 of 4 builds failed with the same signature')
    const facts = Array.from(banner().querySelectorAll('[data-banner-fact]'), (f) => f.textContent)
    expect(facts).toEqual([
      'Failing builds 3 of 4',
      'Last green #101 · 30h ago',
      'Red streak 3 in a row',
      'Avg pass rate 85.0%',
    ])
    expect(within(banner()).getByRole('button', { name: /^Bisect from last green/ })).toBeInTheDocument()
  })

  it('a green window: OK, no Bisect, no signatures button', () => {
    renderRunsPage(greenWindow())
    expect(banner()).toHaveAttribute('data-status-banner', 'ok')
    expect(banner()).toHaveTextContent('Pipeline healthy · 2 builds passing')
    expect(within(banner()).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Failure signatures/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /show the failure signatures/ })).toBeNull()
  })

  it('header: the help topic, one secondary action, and ⋯ with the window-wide actions', async () => {
    perms.isQaEngineer = true
    renderRunsPage(brokenWindow())
    expect(screen.getByRole('heading', { level: 1, name: 'Test Runs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Help: Test Runs' })).toHaveAttribute('data-help-topic', 'ingestion')
    // The trigger-all / deep-all buttons left the header row for ⋯.
    expect(screen.queryByRole('button', { name: /Trigger all failed/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Deep all failed (3)' })).toBeEnabled()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Trigger all failed (3)' }))
    await waitFor(() => expect(agentService.bulkTriggerPipelines).toHaveBeenCalledWith(['run-4', 'run-3', 'run-2']))
  })

  it('the window-wide actions are disabled without the QA Engineer role', () => {
    renderRunsPage(brokenWindow())
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Trigger all failed (3)' })).toBeDisabled()
    expect(screen.getByRole('menuitem', { name: 'Deep all failed (3)' })).toBeDisabled()
  })

  it('one toolbar: the global WindowPicker drives the runs request', () => {
    renderRunsPage(brokenWindow())
    const picker = screen.getByRole('radiogroup', { name: 'Time window' })
    expect(within(picker).getAllByRole('radio').map((r) => r.textContent)).toEqual(['24h', '7d', '14d', '30d', '90d'])
    expect(within(picker).getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(picker).getByRole('radio', { name: '7d' }))
    expect(useTimeWindowStore.getState().days).toBe(7)
    expect(vi.mocked(useRuns).mock.lastCall?.[0]).toMatchObject({ days: 7, size: 500 })
    expect(screen.getByRole('combobox', { name: 'Run status' })).toBeInTheDocument()
  })
})

describe('RunsPage — Upload report stays the primary action (MRU-4, MRU-17)', () => {
  it('flag on: Upload report is the header\'s first action and opens the upload panel', () => {
    flags.manual_upload = true
    perms.isQaEngineer = true
    renderRunsPage(brokenWindow())
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const actions = within(header).getAllByRole('button').filter((b) => !b.hasAttribute('data-help-topic'))
    expect(actions[0]).toHaveAccessibleName('Upload report')
    expect(screen.queryByTestId('upload-report-modal')).toBeNull()
    fireEvent.click(actions[0])
    expect(screen.getByTestId('upload-report-modal')).toBeInTheDocument()
  })

  it('flag off: no Upload report anywhere', () => {
    perms.isQaEngineer = true
    renderRunsPage(brokenWindow())
    expect(screen.queryByRole('button', { name: 'Upload report' })).toBeNull()
  })

  it('the ?upload=1 deep link still opens the panel', () => {
    flags.manual_upload = true
    perms.isQaEngineer = true
    renderRunsPage(brokenWindow(), '/runs?upload=1')
    expect(screen.getByTestId('upload-report-modal')).toBeInTheDocument()
  })

  it('the ?upload=1 deep link without the role says why, and opens nothing', () => {
    flags.manual_upload = true
    renderRunsPage(brokenWindow(), '/runs?upload=1')
    expect(screen.getByText('QA Engineer role required to upload reports.')).toBeInTheDocument()
    expect(screen.queryByTestId('upload-report-modal')).toBeNull()
    expect(screen.getByRole('button', { name: 'Upload report' })).toBeDisabled()
  })
})

describe('RunsPage — below the table, collapsed (P3 Disclosures)', () => {
  it('"How this verdict is computed": the health gauge, its four weighted dimensions, the metadata gap', () => {
    renderRunsPage(brokenWindow())
    expect(screen.queryByRole('meter', { name: 'Pipeline health' })).toBeNull()
    const toggle = disclosureButton('How this verdict is computed')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('meter', { name: 'Pipeline health' })).toHaveAttribute('aria-valuetext', '30 of 100, Blocked')
    for (const label of ['Build success', 'Signature diversity', 'Fix velocity', 'Metadata coverage']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText(/Branch, release, and duration absent on 100% of rows/)).toBeInTheDocument()
  })

  it('"Build history": the 14-build strip, the per-build pass rate, the last green build and Start bisect', () => {
    renderRunsPage(brokenWindow())
    expect(screen.queryByRole('region', { name: 'Last green build' })).toBeNull()
    fireEvent.click(disclosureButton('Build history'))
    expect(screen.getByRole('img', { name: /^Build velocity over the last 14 builds/ })).toBeInTheDocument()
    expect(passRateCell()).toHaveTextContent('85.0%')
    const callout = screen.getByRole('region', { name: 'Last green build' })
    expect(within(callout).getByText('Bisect target')).toBeInTheDocument()
    expect(within(callout).getByText('Hours ago')).toBeInTheDocument()
    expect(within(callout).getAllByRole('button').map(b => b.textContent?.trim())).toEqual(['Start bisect'])
  })
})

describe('RunsPage — the failure signatures open in a side panel (P3 drill-down)', () => {
  const panel = () => screen.queryByRole('complementary', { name: 'Failure signatures' })

  it('the header button opens it; Close closes it', () => {
    renderRunsPage(brokenWindow())
    expect(panel()).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Failure signatures/ }))
    const opened = panel() as HTMLElement
    expect(opened).not.toBeNull()
    expect(within(opened).getByText(/3 of 4 builds match/)).toBeInTheDocument()
    expect(within(opened).getByRole('meter', { name: 'Failed builds by signature' })).toHaveAttribute(
      'aria-valuetext',
      '3 of 3; Primary signature 3, Other signatures 0',
    )
    fireEvent.click(within(opened).getByRole('button', { name: 'Close panel' }))
    expect(panel()).toBeNull()
  })

  it('a row\'s signature chip opens it too, and the cluster no longer sits under the table', () => {
    renderRunsPage(brokenWindow())
    expect(document.querySelector('[data-signature-clusters]')).toBeNull()
    const chips = within(primary()).getAllByRole('button', { name: /^Signature .*: show the failure signatures$/ })
    expect(chips).toHaveLength(4)
    fireEvent.click(chips[0])
    expect(panel()).not.toBeNull()
  })
})
