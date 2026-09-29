/**
 * Tests for ``buildBisectHref`` — the pure helper that powers every
 * "Bisect from last green" / "Start bisect" / "Bisect from green" CTA
 * on the /runs page.
 *
 * The CTAs used to toast ``"Bisect modal — coming in Phase 2"``; they
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
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { TestRun } from '@/types/runs'
import { buildBisectHref, buildPipelineModel, BuildVelocityCard, HealthMeter, RunKpiStrip } from './RunsPage'

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

// ── Wave 2.5 (VIZ-104, OD-1): the KPI glyphs, the health meter and the build
// strip draw only what the runs say. ────────────────────────────────────────

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

/** Any drawing in a KPI cell other than its lucide label icon. */
const GLYPH_SVG = 'svg:not(.lucide)'

/** A KPI cell by its label: the label sits in the cell's first row. */
function kpiCell(label: string): HTMLElement {
  const cell = screen.getByText(label).closest('div')?.parentElement
  if (!cell) throw new Error(`no KPI cell labelled ${label}`)
  return cell
}

describe('RunKpiStrip — a glyph only where the runs have one (OD-1)', () => {
  const runs = newestFirst([
    run(1, { pass_rate: 50, status: 'FAILED', passed_tests: 5, failed_tests: 5 }),
    run(2, { pass_rate: 70, status: 'FAILED', passed_tests: 7, failed_tests: 3 }),
    run(3, { pass_rate: 95, status: 'PASSED' }),
  ])

  it('draws Avg pass rate from every build, OLDEST first, so the latest point is the newest build', () => {
    const model = buildPipelineModel(runs)
    expect(model.passRateSeries).toEqual([50, 70, 95])
    render(<RunKpiStrip model={model} />)
    const spark = within(kpiCell('Avg pass rate')).getByRole('img')
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
    const { container } = render(<RunKpiStrip model={model} />)
    expect(within(kpiCell('Avg pass rate')).getByRole('img')).toHaveAccessibleName(
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
    render(<RunKpiStrip model={model} />)
    const cell = kpiCell('Avg pass rate')
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
    render(<RunKpiStrip model={model} />)
    const cell = kpiCell('Avg pass rate')
    expect(cell).toHaveTextContent('—')
    expect(cell).toHaveTextContent('not measured')
    expect(cell).not.toHaveTextContent('0.0')
    expect(cell).not.toHaveTextContent('%')
  })

  it('draws no pass-rate sparkline from fewer than two measured builds', () => {
    render(<RunKpiStrip model={buildPipelineModel([run(1)])} />)
    expect(within(kpiCell('Avg pass rate')).queryByRole('img')).toBeNull()
    expect(kpiCell('Avg pass rate').querySelector(GLYPH_SVG)).toBeNull()
  })

  it('splits Unique failures into the primary signature vs the rest', () => {
    const model = buildPipelineModel(
      newestFirst([
        run(1, { status: 'FAILED', passed_tests: 8, failed_tests: 2 }),
        run(2, { status: 'FAILED', passed_tests: 8, failed_tests: 2 }),
        run(3, { status: 'FAILED', passed_tests: 6, failed_tests: 4 }),
        run(4),
      ]),
    )
    render(<RunKpiStrip model={model} />)
    const meter = within(kpiCell('Unique failures')).getByRole('meter', { name: 'Failed builds by signature' })
    expect(meter).toHaveAttribute('aria-valuetext', '3 of 3; Primary signature 2, Other signatures 1')
  })

  it('draws no Unique-failures bar when nothing failed', () => {
    render(<RunKpiStrip model={buildPipelineModel([run(1), run(2)])} />)
    expect(within(kpiCell('Unique failures')).queryByRole('meter')).toBeNull()
  })

  it('deletes the three decorative glyphs: Builds failed, Last green build, Red streak', () => {
    render(<RunKpiStrip model={buildPipelineModel(runs)} />)
    for (const label of ['Builds failed', 'Last green build', 'Red streak']) {
      const cell = kpiCell(label)
      expect(cell.querySelector(GLYPH_SVG), label).toBeNull()
      expect(within(cell).queryByRole('img'), label).toBeNull()
      expect(within(cell).queryByRole('meter'), label).toBeNull()
    }
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
