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
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { useRuns } from '@/hooks/useRuns'
import agentService from '@/services/agentService'
import { aiHistoryService } from '@/services/aiHistoryService'
import { getData } from '@/services/http'
import toast from 'react-hot-toast'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import RunsPage, {
  buildBisectHref, buildPipelineModel, BuildPassRate, BuildVelocityCard, HealthMeter, SignatureClusters,
} from './RunsPage'
import { buildSignatureModel } from './runsList/runSignatures'

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
// W03 (D42): the per-row "Compare to previous run" asks the server.
vi.mock('@/services/aiHistoryService', () => ({ aiHistoryService: { getPreviousRun: vi.fn() } }))
vi.mock('@/services/agentService', () => ({
  default: {
    triggerPipeline: vi.fn(async () => ({})),
    triggerDeepPipeline: vi.fn(async () => ({})),
    bulkTriggerPipelines: vi.fn(async (ids: string[]) => ({ queued: ids.length })),
  },
}))
// The page's batch reads (FAIL-04 / FAIL-03 / FAIL-05), answered from `api`
// per test: ONE `GET /runs/ai-verdicts` for the runs on screen (a run not
// listed has no snapshot and was never analysed), ONE `GET
// /runs/failure-signatures` for the failed builds (a run not listed fails
// with `DEFAULT_SIGNATURE`), and the project's branches. Anything else fails.
const api = vi.hoisted(() => ({
  verdicts: {} as Record<string, Record<string, unknown>>,
  unavailable: new Set<string>(),
  verdictsFail: false,
  signatures: {} as Record<string, Array<Record<string, unknown>>>,
  signaturesFail: false,
  signaturesPending: false,
  branches: [] as string[],
}))
const DEFAULT_SIGNATURE = {
  signature: 'assertionerror: expected # but was #', label: 'AssertionError: expected 3 but was 4',
  count: 2, test_count: 1, tests: ['test_checkout'],
}
vi.mock('@/services/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/http')>()),
  getData: vi.fn(async (url: string, config?: { params?: Record<string, unknown> }) => {
    const ids = ([] as string[]).concat((config?.params?.run_id as string[] | undefined) ?? [])
    if (url === '/api/v1/runs/ai-verdicts') {
      if (api.verdictsFail) throw new Error('503')
      return {
        items: ids.filter((id) => !api.unavailable.has(id)).map((id) => ({
          run_id: id, snapshot: false, stale: null, generated_at: null, analysed: false,
          verdict_inputs: null, review: null, ...(api.verdicts[id] ?? {}),
        })),
        unavailable: ids.filter((id) => api.unavailable.has(id)),
        max_ids: 25,
      }
    }
    if (url === '/api/v1/runs/failure-signatures') {
      if (api.signaturesFail) throw new Error('503')
      if (api.signaturesPending) return new Promise(() => undefined)
      return {
        items: ids.map((id) => {
          const signatures = api.signatures[id] ?? [DEFAULT_SIGNATURE]
          const failing = signatures.reduce((s, e) => s + Number(e.count), 0)
          return {
            run_id: id, failing_tests: failing, read: failing, capped: false,
            dominant: (signatures[0]?.signature as string | undefined) ?? null, signatures,
          }
        }),
        unavailable: [], max_ids: 50, rows_per_run_cap: 500,
      }
    }
    if (url === '/api/v1/runs/branches') {
      return {
        items: api.branches.map((branch) => ({ branch, runs: 1, last_run_at: null })),
        total: api.branches.length, truncated: false, window_days: 30,
      }
    }
    throw new Error(`unexpected request ${url}`)
  }),
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

/** A failure signature as `GET /runs/failure-signatures` lists it. */
const sigEntry = (signature: string, label: string, tests: string[], count = 2) =>
  ({ signature, label, count, test_count: tests.length, tests })
const ASSERT_SIG = sigEntry('assertionerror: expected # but was #', 'AssertionError: expected 3 but was 4', ['test_cart', 'test_pay'])
const NPE_SIG = sigEntry('nullpointerexception at foo', 'NullPointerException at Foo', ['test_refund'])

/** The signature model of `runs`, every failed build answered with `byRun[id]` (else ASSERT_SIG). */
function signaturesOf(runs: TestRun[], byRun: Record<string, ReturnType<typeof sigEntry>[]> = {}, capped: Record<string, [number, number]> = {}) {
  const failed = runs.filter((r) => /fail|broken|error/i.test(r.status))
  return buildSignatureModel(runs, {
    items: failed.map((r) => {
      const signatures = byRun[r.id] ?? [ASSERT_SIG]
      const [read, failing] = capped[r.id] ?? [signatures.reduce((s, e) => s + e.count, 0), signatures.reduce((s, e) => s + e.count, 0)]
      return { run_id: r.id, failing_tests: failing, read, capped: read < failing, dominant: signatures[0]?.signature ?? null, signatures }
    }),
    unavailable: [], max_ids: 50, rows_per_run_cap: 500,
  })
}

describe('SignatureClusters — the side panel groups the failed builds by their error (FAIL-03)', () => {
  function renderClusters(model: ReturnType<typeof signaturesOf>, failed = false) {
    return render(
      <MemoryRouter>
        <SignatureClusters model={model} failed={failed} onJumpToRow={() => undefined} />
      </MemoryRouter>,
    )
  }
  const window4 = () => newestFirst([
    run(1, { status: 'FAILED' }),
    run(2, { status: 'FAILED' }),
    run(3, { status: 'FAILED' }),
    run(4),
  ])

  it('splits the failed builds into the primary error vs the rest, each group titled by its error\'s first line', () => {
    const runs = window4()
    renderClusters(signaturesOf(runs, { 'run-3': [NPE_SIG] }))
    const meter = screen.getByRole('meter', { name: 'Failed builds by signature' })
    expect(meter).toHaveAttribute('aria-valuetext', '3 of 3; Primary signature 2, Other signatures 1')
    expect(screen.getByText('2 signatures across 3 failed builds')).toBeInTheDocument()
    const groups = document.querySelectorAll('[data-signature-group]')
    expect(Array.from(groups, (g) => g.querySelector('code')?.textContent)).toEqual([
      'AssertionError: expected 3 but was 4', 'NullPointerException at Foo',
    ])
    expect(groups[0].querySelector('code')).toHaveAttribute('title', 'assertionerror: expected # but was #')
    expect(screen.getAllByText('primary')).toHaveLength(2)
    expect(screen.getAllByText('other')).toHaveLength(1)
  })

  it('says "the same test fails" only when every member names it', () => {
    const runs = window4()
    const { unmount } = renderClusters(signaturesOf(runs, {
      'run-1': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_cart', 'test_pay'])],
      'run-2': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_pay'])],
      'run-3': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_pay', 'test_zip'])],
    }))
    expect(screen.getByText('test_pay fails with this error in all 3 builds.')).toBeInTheDocument()
    unmount()
    renderClusters(signaturesOf(runs, {
      'run-1': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_cart'])],
      'run-2': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_pay'])],
      'run-3': [sigEntry(ASSERT_SIG.signature, ASSERT_SIG.label, ['test_zip'])],
    }))
    expect(screen.queryByText(/fails with this error in all/)).toBeNull()
    expect(screen.getByText('3 builds fail with this error.')).toBeInTheDocument()
  })

  it('describes the other errors from the data, never with an invented cause', () => {
    // The panel used to print a fixed sentence blaming "an older Jenkinsfile"
    // for every outlier, whatever the data said (OSS-release audit E2).
    const runs = window4()
    const { container } = renderClusters(signaturesOf(runs, { 'run-3': [NPE_SIG] }))
    expect(screen.getByText('1 failed build carries a different error. Open it before counting it as a separate incident.')).toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/Jenkinsfile/)
  })

  it('the old tuple copy is gone', () => {
    const runs = window4()
    const { container } = renderClusters(signaturesOf(runs, { 'run-3': [NPE_SIG] }))
    const text = container.textContent ?? ''
    for (const old of [/same test failing across all of them/, /outlier · old config/, /pass \/ \d+ fail \/ \d+ total/, /\bmatch\b/]) {
      expect(text).not.toMatch(old)
    }
  })

  it('says when it grouped fewer builds than the window failed, and when a build was capped', () => {
    const runs = window4()
    const model = buildSignatureModel(runs, {
      items: [
        { run_id: 'run-1', failing_tests: 600, read: 500, capped: true, dominant: ASSERT_SIG.signature, signatures: [ASSERT_SIG] },
        { run_id: 'run-2', failing_tests: 2, read: 2, capped: false, dominant: ASSERT_SIG.signature, signatures: [ASSERT_SIG] },
      ],
      unavailable: [], max_ids: 50, rows_per_run_cap: 500,
    })
    renderClusters(model)
    expect(screen.getByText('1 signature across 2 failed builds · grouped over the newest 2 of 3 failed builds')).toBeInTheDocument()
    expect(screen.getByText('(grouped over 500 of 600 failing tests)')).toBeInTheDocument()
  })

  it('a hostile error line renders as text, never markup', () => {
    const runs = window4()
    const hostile = sigEntry('<img src=x onerror=alert(1)>', '<img src=x onerror=alert(1)>', ['<b>t</b>'])
    const { container } = renderClusters(signaturesOf(runs, { 'run-1': [hostile], 'run-2': [hostile], 'run-3': [hostile] }))
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(screen.getByText('<b>t</b> fails with this error in all 3 builds.')).toBeInTheDocument()
  })

  it('draws no split when nothing failed; says so when the request failed', () => {
    const { unmount } = renderClusters(signaturesOf([run(1), run(2)]))
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.getByText('No failures in this window — nothing to group.')).toBeInTheDocument()
    unmount()
    renderClusters(buildSignatureModel(window4(), undefined), true)
    expect(screen.getByText('Signatures unavailable: the request failed.')).toBeInTheDocument()
    expect(document.querySelector('[data-signature-group]')).toBeNull()
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
  api.verdicts = {}
  api.unavailable.clear()
  api.verdictsFail = false
  api.signatures = {}
  api.signaturesFail = false
  api.signaturesPending = false
  api.branches = []
  vi.mocked(getData).mockClear()
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

  it('states the verdict in one banner: the failing count in the title once, last green, red streak, average pass rate, Bisect', async () => {
    renderRunsPage(brokenWindow())
    expect(banner()).toHaveAttribute('data-status-banner', 'fail')
    expect(banner()).toHaveTextContent('Failing')
    // FAIL-03: "the same error" once the signatures show the three share one.
    await waitFor(() => expect(banner()).toHaveTextContent('Pipeline broken · 3 of 4 builds failed with the same error'))
    const facts = Array.from(banner().querySelectorAll('[data-banner-fact]'), (f) => f.textContent)
    expect(facts).toEqual([
      'Last green #101 · 1d ago',
      'Red streak 3 in a row',
      'Avg pass rate 85.0%',
    ])
    expect(within(banner()).getByRole('button', { name: /^Bisect from last green/ })).toBeInTheDocument()
  })

  it('a build still in progress neither breaks the red streak nor reads as a red 0.0 % (browser E2E pass)', () => {
    renderRunsPage(newestFirst([
      run(1, { status: 'PASSED', passed_tests: 10, failed_tests: 0, pass_rate: 100, created_at: hoursAgo(172) }),
      run(2, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80, created_at: hoursAgo(20) }),
      run(3, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80, created_at: hoursAgo(10) }),
      // As the list sends it: counts so far, no pass rate yet.
      run(4, { status: 'IN_PROGRESS', passed_tests: 10, failed_tests: 1, total_tests: 11, pass_rate: null, created_at: hoursAgo(1) }),
    ]))
    const facts = Array.from(banner().querySelectorAll('[data-banner-fact]'), (f) => f.textContent)
    // It read "Red streak 0 in a row" under "Pipeline broken", and "172h ago".
    expect(facts).toEqual(['Last green #101 · 7d ago', 'Red streak 2 in a row', 'Avg pass rate 86.7%'])
    const rate = (id: string) => primary().querySelector(`#run-row-${id} [data-run-pass-rate]`) as HTMLElement
    expect(rate('run-4')).toHaveTextContent(/^—$/)
    expect(rate('run-4').firstElementChild).toHaveAttribute('title', 'No pass rate: the run is still in progress')
    expect(rate('run-3')).toHaveTextContent(/^80\.0%$/)
  })

  it('a finished build that reported no tests has no pass rate either', () => {
    renderRunsPage(newestFirst([
      run(1, { status: 'PASSED', created_at: hoursAgo(5) }),
      run(2, { status: 'FAILED', passed_tests: 0, failed_tests: 0, total_tests: 0, pass_rate: 0, created_at: hoursAgo(1) }),
    ]))
    const cell = primary().querySelector('#run-row-run-2 [data-run-pass-rate]') as HTMLElement
    expect(cell).toHaveTextContent(/^—$/)
    expect(cell.firstElementChild).toHaveAttribute('title', 'No pass rate: the run reported no tests')
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
  it('"How this verdict is computed": the health gauge, its four weighted dimensions, the metadata gap', async () => {
    renderRunsPage(brokenWindow())
    await waitFor(() => expect(primary().querySelectorAll('[data-signature-chip]')).toHaveLength(3))
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

describe('RunsPage — the failure signatures open in a side panel (P3 drill-down, FAIL-03)', () => {
  const panel = () => screen.queryByRole('complementary', { name: 'Failure signatures' })
  const sigUrls = () => vi.mocked(getData).mock.calls.filter(([url]) => url === '/api/v1/runs/failure-signatures')

  it('the header button opens it; Close closes it', async () => {
    renderRunsPage(brokenWindow())
    expect(panel()).toBeNull()
    const button = await screen.findByRole('button', { name: /^Failure signatures/ })
    expect(button).toHaveTextContent(/Failure signatures\s*1$/)
    expect(button).toHaveAttribute('title', 'Failed builds grouped by the error most of their failing tests share')
    fireEvent.click(button)
    const opened = panel() as HTMLElement
    expect(opened).not.toBeNull()
    expect(within(opened).getByText('1 signature across 3 failed builds')).toBeInTheDocument()
    expect(within(opened).getByRole('meter', { name: 'Failed builds by signature' })).toHaveAttribute(
      'aria-valuetext',
      '3 of 3; Primary signature 3, Other signatures 0',
    )
    fireEvent.click(within(opened).getByRole('button', { name: 'Close panel' }))
    expect(panel()).toBeNull()
  })

  it('asks ONCE for every failed build of the window, and a failed row\'s chip opens the panel; passed rows read a plain "—"', async () => {
    renderRunsPage(brokenWindow())
    expect(document.querySelector('[data-signature-clusters]')).toBeNull()
    await waitFor(() => expect(within(primary()).getAllByRole('button', { name: /^Signature .*: show the failure signatures$/ })).toHaveLength(3))
    expect(sigUrls()).toHaveLength(1)
    expect(sigUrls()[0][1]).toMatchObject({ params: { run_id: ['run-2', 'run-3', 'run-4'] } })
    const chip = within(primary().querySelector('#run-row-run-4') as HTMLElement).getByRole('button', { name: /^Signature/ })
    expect(chip).toHaveAttribute('title', 'AssertionError: expected 3 but was 4')
    expect(chip).toHaveTextContent(/^[0-9a-f]{3}-cluster$/)
    const passed = primary().querySelector('#run-row-run-1 [data-run-signature]') as HTMLElement
    expect(passed).toHaveTextContent(/^—$/)
    expect(within(passed).queryByRole('button')).toBeNull()
    fireEvent.click(chip)
    expect(panel()).not.toBeNull()
  })

  it('two builds with the same pass / fail / total but different errors are two groups; the other one is tinted', async () => {
    api.signatures['run-3'] = [{ signature: 'nullpointerexception at foo', label: 'NullPointerException at Foo', count: 2, test_count: 1, tests: ['test_refund'] }]
    renderRunsPage(brokenWindow())
    const button = await screen.findByRole('button', { name: /^Failure signatures/ })
    await waitFor(() => expect(button).toHaveTextContent(/Failure signatures\s*2$/))
    const chipOf = (id: string) => within(primary().querySelector(`#run-row-${id}`) as HTMLElement).getByRole('button', { name: /^Signature/ })
    expect(chipOf('run-3').textContent).not.toBe(chipOf('run-4').textContent)
    expect(chipOf('run-3')).toHaveClass('italic')
    expect(chipOf('run-4')).not.toHaveClass('italic')
    // 2 of 3 is not "the same error"; two errors score a higher diversity.
    expect(banner()).toHaveTextContent('Mixed · 3 of 4 builds failed')
    expect(banner()).not.toHaveTextContent('same error')
  })

  it('asks for at most 50 failed builds, the visible page first: a later page is asked when it is shown', async () => {
    const many = newestFirst(Array.from({ length: 60 }, (_, i) =>
      run(i + 1, { status: 'FAILED', created_at: hoursAgo(120 - i) })))
    renderRunsPage(many)
    await waitFor(() => expect(sigUrls()).toHaveLength(1))
    const first = (sigUrls()[0][1] as { params: { run_id: string[] } }).params.run_id
    expect(first).toHaveLength(50)
    // run-1 is the oldest: off the first page and beyond the newest 50.
    expect(first).not.toContain('run-1')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    // Page 3 holds run-1 … run-10: they are asked first, so their chips come back.
    await waitFor(() => expect(sigUrls()).toHaveLength(2))
    const third = (sigUrls()[1][1] as { params: { run_id: string[] } }).params.run_id
    expect(third).toHaveLength(50)
    expect(third).toContain('run-1')
    await waitFor(() => expect(primary().querySelector('#run-row-run-1 [data-signature-chip]')).not.toBeNull())
  })

  it('a hostile error line renders as text in the chip title and the panel', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    for (const id of ['run-2', 'run-3', 'run-4']) api.signatures[id] = [{ signature: 'h', label: hostile, count: 1, test_count: 1, tests: ['t'] }]
    renderRunsPage(brokenWindow())
    fireEvent.click(await screen.findByRole('button', { name: /^Failure signatures/ }))
    expect(document.querySelector('img')).toBeNull()
    expect(within(panel() as HTMLElement).getByText(hostile)).toBeInTheDocument()
  })

  it('the old tuple copy appears nowhere on the page', async () => {
    renderRunsPage(brokenWindow())
    fireEvent.click(await screen.findByRole('button', { name: /^Failure signatures/ }))
    for (const old of [/same test failing across all of them/, /outlier · old config/, /pass \/ fail \/ total/]) {
      expect(screen.queryByText(old)).toBeNull()
    }
  })

  it('Signature diversity is "—" while the signatures load, and the composite re-weights the other three', () => {
    api.signaturesPending = true
    renderRunsPage(brokenWindow())
    fireEvent.click(disclosureButton('How this verdict is computed'))
    const tile = document.querySelector('[data-dimension="signature_diversity"]') as HTMLElement
    expect(tile).toHaveAttribute('data-dimension-measured', 'false')
    expect(tile.querySelector('[data-dimension-value]')).toHaveTextContent(/^—$/)
    // Build success 25 × 0.40 + fix velocity (30 h since green) 58.3 × 0.25 + metadata 0 × 0.20, over 0.85.
    expect(screen.getByRole('meter', { name: 'Pipeline health' })).toHaveAttribute('aria-valuetext', '29 of 100, Blocked')
  })

  it('a failed signatures request: the panel says so, the rows read "—"', async () => {
    api.signaturesFail = true
    renderRunsPage(brokenWindow())
    await waitFor(() => expect(primary().querySelector('#run-row-run-4 [data-signature-none]')).toHaveAttribute('title', 'Signature unavailable: the request failed'))
    expect(screen.queryByRole('button', { name: /^Failure signatures/ })).toBeNull()
  })
})

// UX redesign P4, owner decision D2: the `/intelligence` list retired into an
// AI-verdict column of the runs table. FAIL-04: the verdicts of the runs on
// screen come from ONE batch read of their stored snapshots; "—" when a run
// has none, with the reason; the separate "Intel" link went.
describe('RunsPage — the AI-verdict column (P4, D2, FAIL-04)', () => {
  const decided = (recommendation: string, state = 'accepted') => ({
    snapshot: true, stale: false, analysed: true,
    verdict_inputs: {
      structured_summary: null, release_decision: { recommendation }, intelligence_available: true, top_analyses: null,
    },
    review: { state },
  })
  const row = (id: string) => primary().querySelector(`#run-row-${id}`) as HTMLElement
  const cell = (id: string) => row(id).querySelector('[data-ai-verdict-cell]') as HTMLElement
  const verdictCalls = () => vi.mocked(getData).mock.calls.filter(([url]) => url === '/api/v1/runs/ai-verdicts')
  const urls = () => vi.mocked(getData).mock.calls.map(([url]) => url)

  it('sits after Status, and each run reads its own verdict; the verdict opens the run\'s Analysis tab', async () => {
    api.verdicts['run-4'] = decided('NO_GO', 'pending_review')
    api.verdicts['run-3'] = decided('CONDITIONAL_GO', 'rejected')
    api.verdicts['run-2'] = decided('GO')
    renderRunsPage(brokenWindow())
    const headers = within(primary()).getAllByRole('columnheader').map((h) => h.textContent?.trim())
    expect(headers.indexOf('AI verdict')).toBe(headers.indexOf('Status') + 1)

    const noGo = await within(cell('run-4')).findByRole('link')
    expect(noGo).toHaveTextContent(/^No-Godraft$/)
    expect(noGo).toHaveAttribute('href', '/runs/run-4?tab=analysis')
    expect(noGo).toHaveAttribute('title', expect.stringContaining('awaiting human review'))
    expect(await within(cell('run-3')).findByRole('link')).toHaveTextContent(/^Conditionalrejected$/)
    expect(await within(cell('run-2')).findByRole('link')).toHaveTextContent(/^Go$/)
    // Never analysed: "—", with the reason, and no link.
    await waitFor(() => expect(cell('run-1')).toHaveTextContent(/^—$/))
    expect(within(cell('run-1')).queryByRole('link')).toBeNull()
    expect(cell('run-1').querySelector('[data-ai-verdict]')).toHaveAttribute('title', 'No AI verdict: this run has not been analysed')
  })

  it('has no Test Suite column: the suite sits under its run number, in the Build cell', () => {
    renderRunsPage(brokenWindow())
    const headers = within(primary()).getAllByRole('columnheader').map((h) => h.textContent?.trim())
    // A column of its own put the table 66 px past its card at 1280 px on CI's
    // font, and the row actions out of sight (fold-runs.spec.ts measures it).
    expect(headers).not.toContain('Test Suite')
    expect(headers.indexOf('Signature')).toBe(headers.findIndex((h) => h?.startsWith('Build')) + 1)
    const build = row('run-1').querySelectorAll('td')[1] as HTMLElement
    expect(build.querySelector('a[href="/runs/run-1"]')).not.toBeNull()
    expect(build.querySelector('[data-run-suite]')).not.toBeNull()
  })

  it('asks ONCE for the runs on screen (ids sorted), never per run, never the per-run report', async () => {
    renderRunsPage(brokenWindow())
    await waitFor(() => expect(primary().querySelectorAll('[data-ai-verdict="none"]')).toHaveLength(4))
    expect(verdictCalls()).toHaveLength(1)
    expect(verdictCalls()[0][1]).toMatchObject({ params: { run_id: ['run-1', 'run-2', 'run-3', 'run-4'] }, suppressToast: true })
    expect(urls().filter((u) => /\/intelligence$/.test(String(u)))).toEqual([])
  })

  it('analysed but not summarised: "—" that says how to build it', async () => {
    api.verdicts['run-4'] = { snapshot: false, analysed: true }
    renderRunsPage(brokenWindow())
    await waitFor(() => expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute('data-ai-verdict-reason', 'not-summarised'))
    expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute(
      'title', "No AI verdict on this list yet: the run was analysed but its summary is not built. Open the run's analysis to build it.",
    )
  })

  it('a run the batch lists as unavailable says so, in its own words', async () => {
    api.unavailable.add('run-4')
    renderRunsPage(brokenWindow())
    await waitFor(() => expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute('title', 'AI verdict unavailable: the run is not available to you'))
    expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute('data-ai-verdict-reason', 'unavailable')
  })

  it('makes no verdict up: a run still in flight is not asked, and reads "—"', async () => {
    renderRunsPage(newestFirst([
      run(1, { status: 'PASSED', created_at: hoursAgo(5) }),
      run(2, { status: 'IN_PROGRESS', passed_tests: 0, failed_tests: 0, total_tests: 0, pass_rate: 0, created_at: hoursAgo(1) }),
    ]))
    const running = cell('run-2').querySelector('[data-ai-verdict]') as HTMLElement
    expect(running).toHaveTextContent('—')
    expect(running).toHaveAttribute('title', 'No AI verdict: the run is still in progress')
    await waitFor(() => expect(verdictCalls()).toHaveLength(1))
    expect(verdictCalls()[0][1]).toMatchObject({ params: { run_id: ['run-1'] } })
  })

  it('a failed request reads "—" (unavailable), not a verdict, and raises no toast', async () => {
    api.verdictsFail = true
    renderRunsPage(brokenWindow())
    await waitFor(() =>
      expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute('data-ai-verdict-reason', 'unavailable'),
    )
    expect(cell('run-4')).toHaveTextContent(/^—$/)
    expect(cell('run-4').querySelector('[data-ai-verdict]')).toHaveAttribute('title', 'AI verdict unavailable: the request failed')
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled()
  })

  it('drops the separate Intel link, and nothing links to the retired routes', () => {
    renderRunsPage(brokenWindow())
    expect(within(primary()).queryByRole('link', { name: /Intel/ })).toBeNull()
    const hrefs = Array.from(primary().querySelectorAll('a'), (a) => a.getAttribute('href') ?? '')
    expect(hrefs.filter((h) => /\/intelligence(\?|$)/.test(h))).toEqual([])
  })
})

// W03 (D42): the per-row "Compare to previous run" asks the SERVER for the
// previous run (same suite AND branch) instead of picking from the runs loaded
// on the page, so it opens the same pair as the run page and its Analysis tab.
describe('RunsPage — Compare to previous run is the server\'s rule', () => {
  function renderWithLocation(runs: TestRun[]) {
    vi.mocked(useRuns).mockReturnValue({
      data: { items: runs, total: runs.length, page: 1, size: 500, pages: 1 },
      isLoading: false,
      error: undefined,
      mutate: vi.fn(),
    } as never)
    const seen: string[] = []
    function Probe() {
      const where = useLocation()
      seen.push(`${where.pathname}${where.search}`)
      return null
    }
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
        <MemoryRouter initialEntries={['/runs']}>
          <Routes>
            <Route path="*" element={<><RunsPage /><Probe /></>} />
          </Routes>
        </MemoryRouter>
      </SWRConfig>,
    )
    return seen
  }

  const last = (seen: string[]) => seen[seen.length - 1]
  const compareButton = (runId: string) =>
    within(document.querySelector(`#run-row-${runId}`) as HTMLElement).getByTitle('Compare to the previous run of "Auth"')

  const suiteRuns = () => newestFirst([
    run(1, { status: 'PASSED', primary_suite_name: 'Auth', branch: 'release', created_at: hoursAgo(30) }),
    run(2, { status: 'FAILED', primary_suite_name: 'Auth', branch: 'main', created_at: hoursAgo(2) }),
  ])

  beforeEach(() => {
    vi.mocked(aiHistoryService.getPreviousRun).mockReset()
    vi.mocked(toast).mockClear()
  })

  it('opens the pair the server names (here a run that is not even on the page)', async () => {
    vi.mocked(aiHistoryService.getPreviousRun).mockResolvedValue({
      run_id: 'run-2', rule: null, current: null, reason: null, message: null,
      baseline: { run_id: 'run-from-server', build_number: '99', run_seq: 9, branch: 'main', primary_suite_name: 'Auth', status: 'PASSED', created_at: null, finished_at: null },
    } as never)
    const seen = renderWithLocation(suiteRuns())
    fireEvent.click(compareButton('run-2'))
    await waitFor(() => expect(last(seen)).toBe('/runs/compare?mode=manual&left=run-from-server&right=run-2&suite=Auth'))
    expect(aiHistoryService.getPreviousRun).toHaveBeenCalledWith('run-2')
  })

  it('no previous run on this branch: the server\'s sentence, no navigation', async () => {
    const message = 'No earlier completed run of "Auth" on branch "main" found.'
    vi.mocked(aiHistoryService.getPreviousRun).mockResolvedValue({
      run_id: 'run-2', rule: null, current: null, baseline: null, reason: 'no_previous_run', message,
    } as never)
    const seen = renderWithLocation(suiteRuns())
    fireEvent.click(compareButton('run-2'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith(message, expect.anything()))
    expect(last(seen)).toBe('/runs')
  })

  it('a failed lookup says so and stays', async () => {
    vi.mocked(aiHistoryService.getPreviousRun).mockRejectedValue(new Error('503'))
    const seen = renderWithLocation(suiteRuns())
    fireEvent.click(compareButton('run-2'))
    await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalledWith('Could not look up the previous run — try again in a moment.'))
    expect(last(seen)).toBe('/runs')
  })
})

// ── W29b UIX-06 / UIX-09: the ONE table, panel and KPI tile ─────────────────
describe('RunsPage — the one table, the one panel, the one tile (W29b)', () => {
  it('the runs table is the compact Table: numbers right-aligned, Timing the one sort button, no inert arrow', () => {
    renderRunsPage(brokenWindow())
    const table = within(primary()).getByRole('table')
    expect(table).toHaveAttribute('data-density', 'compact')
    const header = (label: string) => within(table).getAllByRole('columnheader').find((th) => th.textContent?.trim() === label) as HTMLElement
    for (const label of ['Tests', 'Pass rate', 'Timing']) expect(header(label), label).toHaveClass('th', 'th-num')
    expect(header('Build')).not.toHaveAttribute('aria-sort')
    expect(header('Pass rate')).not.toHaveAttribute('aria-sort')
    const timing = header('Timing')
    expect(timing).toHaveAttribute('aria-sort', 'descending')
    expect(within(timing).getByRole('button', { name: 'Timing' })).toHaveClass('th-sort')
    // A sort control is a chevron, never a text arrow; Build and Pass rate lost their inert "↕".
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent ?? '').join('')).not.toMatch(/[↕↑↓]/)
    expect(primary().querySelector('#run-row-run-1 [data-run-pass-rate]')).toHaveClass('td', 'td-num')
    expect(primary().querySelector('#run-row-run-1')).toHaveClass('table-row')
  })

  it('the table and the build velocity are Panels; the dimensions are the in-panel KPI tile, on no white sheen', () => {
    renderRunsPage(brokenWindow())
    const panel = primary().querySelector('[data-panel]') as HTMLElement
    expect(panel).toHaveClass('card')
    expect(within(panel).getByRole('heading', { level: 3, name: /^Runs/ })).toBeInTheDocument()
    fireEvent.click(disclosureButton('How this verdict is computed'))
    const tiles = Array.from(document.querySelectorAll<HTMLElement>('[data-dimension]'))
    expect(tiles).toHaveLength(4)
    for (const tile of tiles) {
      expect(tile).toHaveAttribute('data-metric-card', 'stat')
      expect(tile.outerHTML).not.toMatch(/rgba\(/)
    }
    fireEvent.click(disclosureButton('Build history'))
    expect(screen.getByRole('heading', { level: 3, name: 'Build velocity · 14d' }).closest('[data-panel]')).not.toBeNull()
    expect(screen.getByRole('region', { name: 'Average pass rate' }).querySelector('[data-metric-card="compact"]')).not.toBeNull()
  })
})
