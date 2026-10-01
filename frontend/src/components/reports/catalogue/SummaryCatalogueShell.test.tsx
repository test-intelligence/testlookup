/**
 * The Summary page's stand-in for its catalogue sections (R1-2, R1 (a)) must
 * take EXACTLY the space the real sections take, or the swap moves the
 * tables under it and the real takeaway becomes a new (later) LCP entry.
 *
 * jsdom has no layout, so this holds the inputs of the layout equal instead,
 * frame by frame, the real `SummaryCatalogue` beside the shell for the same
 * report: the header markup (tags, classes, text: the title, the takeaway and
 * every toolbar button), the body's floor and the plot box it holds, and the
 * footer row. The browser then lays both out alike in any font
 * (`tests/ci-e2e/rollout-summary-layout.spec.ts` measures it).
 */
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartState } from '@/components/charts/chartState'
import { PRESENTATION_MODE_SCALE } from '@/components/charts/framePlotHeight'
import { usePresentationStore } from '@/store/presentationStore'
import type { TrendResponse } from '@/types/metrics'
import type { SummaryReport, SummaryTopFailingTest } from '@/types/summaryReport'

vi.mock('@/components/reports/catalogue/useTrendsSeries', () => ({
  useTrendsSeries: (): ChartState<TrendResponse> => ({ status: 'loading' }),
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

import SummaryCatalogue from './SummaryCatalogue'
import SummaryCatalogueShell from './SummaryCatalogueShell'
import { SUMMARY_TREND_CHROME_PX, SUMMARY_TREND_HEIGHT } from './summaryCatalogueWords'

function suite(name: string, passed: number, failed: number, skipped: number, broken: number) {
  const total = passed + failed + skipped + broken
  return {
    suite_name: name,
    total,
    passed,
    failed,
    skipped,
    broken,
    pass_rate_pct: total ? (passed / total) * 100 : 0,
    weighted_pass_rate_pct: total ? (passed / total) * 100 : 0,
    last_run_at: '2026-05-15T00:00:00+00:00',
  }
}

const suites = (n: number) => Array.from({ length: n }, (_, i) => suite(`suite-${i}`, 10 + i, i % 4, i % 2, i % 3))

function makeReport(overrides: Partial<SummaryReport> = {}): SummaryReport {
  return {
    project_id: 'p1',
    project_name: 'GoogleProject',
    mode: 'latest',
    window_days: 30,
    generated_at: '2026-05-16T00:00:00+00:00',
    period_start: '2026-04-16T00:00:00+00:00',
    period_end: '2026-05-16T00:00:00+00:00',
    totals: {
      total_test_cases: 200,
      passed: 180,
      failed: 15,
      skipped: 3,
      broken: 2,
      evaluated: 197,
      pass_rate_pct: 90.0,
      pass_rate_basis_label: 'per unique test',
      fail_rate_pct: 7.5,
      skip_rate_pct: 1.5,
      broken_rate_pct: 1.0,
      weighted_pass_rate_pct: 91.4,
    },
    run_count: 4,
    runs_per_day: null,
    avg_duration_ms: 12_345,
    latest_run_at: '2026-05-15T00:00:00+00:00',
    flaky_test_count: 3,
    flaky_rate_pct: 1.5,
    suites: suites(3),
    top_failing_tests: failing([8, 5, 3]),
    ...overrides,
  }
}

function failing(counts: number[]): SummaryTopFailingTest[] {
  return counts.map((failures, i) => ({ suite_name: `s${i % 3}`, class_name: null, test_name: `test_${i}`, failures }))
}

/**
 * An element as layout sees it: tag, classes, its own text, then its children,
 * in order. An icon is its box: lucide's `svg` in the real toolbar, the
 * shell's sized `span` (no icon library in the page's chunk), both by their
 * size classes.
 */
function shape(el: Element, root = true): string {
  if (el.tagName.toLowerCase() === 'svg' || el.hasAttribute('data-summary-shell-icon')) {
    const size = Array.from(el.classList).filter((c) => /^[hw]-/.test(c)).join(' ')
    return `<icon class="${size}"></icon>`
  }
  const classes = Array.from(el.classList)
    // The shell's toolbar and footer are the real ones, invisible.
    .filter((c) => !(root && c === 'invisible'))
    .join(' ')
  const own = Array.from(el.childNodes)
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent ?? '')
    .join('')
  const children = Array.from(el.children).map((child) => shape(child, false))
  return `<${el.tagName.toLowerCase()} class="${classes}">${own}${children.join('')}</${el.tagName.toLowerCase()}>`
}

interface FrameParts {
  frame: Element
  header: Element
  heading: Element
  takeaway: Element
  toolbar: Element | null
  body: HTMLElement
  /** The box the plot (or the skeleton) holds inside the body, px; `null` when nothing is drawn. */
  plotBox: number | null
  footer: Element | null
}

const px = (value: string | undefined | null) => (value ? Number.parseFloat(value) : NaN)

function realFrame(sectionId: string): FrameParts {
  const frame = document.querySelector(`[data-catalogue-section="${sectionId}"] [data-chart-frame]`) as Element
  expect(frame, sectionId).not.toBeNull()
  const header = frame.firstElementChild as Element
  const body = frame.querySelector('[data-chart-body]') as HTMLElement
  // `ChartResponsive`: its scaled box in presentation mode, else Recharts' container at the plot height.
  const scaled = body.querySelector<HTMLElement>('[data-chart-presentation]')
  const container = body.querySelector<HTMLElement>('.recharts-responsive-container')
  const plotBox = scaled ? px(scaled.style.height) : container ? px(container.style.height) : null
  return {
    frame,
    header,
    heading: header.querySelector('h2') as Element,
    takeaway: header.querySelector('[data-chart-takeaway]') as Element,
    toolbar: header.querySelector('[data-chart-toolbar]'),
    body,
    plotBox,
    footer: frame.querySelector('[data-chart-footer]'),
  }
}

function shellFrame(title: string): FrameParts {
  const frame = document.querySelector(`[data-summary-shell-frame="${title}"]`) as Element
  expect(frame, title).not.toBeNull()
  const header = frame.firstElementChild as Element
  const body = header.nextElementSibling as HTMLElement
  const skeleton = body.querySelector<HTMLElement>('[data-summary-shell-plot]')
  return {
    frame,
    header,
    heading: header.querySelector('h2') as Element,
    takeaway: header.querySelector('[data-summary-shell-takeaway]') as Element,
    toolbar: header.querySelector('[data-summary-shell-toolbar]'),
    body,
    plotBox: skeleton ? px(skeleton.style.height) : null,
    footer: body.nextElementSibling,
  }
}

/** Everything that decides the frame's box, real and shell side by side. */
function expectSameBox(real: FrameParts, shell: FrameParts, what: string) {
  expect(shell.frame.className, `${what}: frame`).toBe(real.frame.className)
  expect(shell.header.className, `${what}: header row`).toBe(real.header.className)
  expect((shell.heading.parentElement as Element).className, `${what}: title column`).toBe(
    (real.heading.parentElement as Element).className,
  )
  expect(shape(shell.heading), `${what}: title`).toBe(shape(real.heading))
  expect(shape(shell.takeaway), `${what}: takeaway`).toBe(shape(real.takeaway))
  expect(shell.toolbar === null, `${what}: a toolbar or none`).toBe(real.toolbar === null)
  if (real.toolbar && shell.toolbar) expect(shape(shell.toolbar), `${what}: toolbar`).toBe(shape(real.toolbar))
  expect(shell.body.style.minHeight, `${what}: body floor`).toBe(real.body.style.minHeight)
  // A drawn chart's plot box, or (nothing drawn) the state message filling the floor.
  const realBox = real.plotBox ?? px(real.body.style.minHeight)
  expect(shell.plotBox, `${what}: the box inside the body`).toBe(realBox)
  expect(shell.footer === null, `${what}: a footer row or none`).toBe(real.footer === null)
  if (real.footer && shell.footer) expect(shape(shell.footer), `${what}: footer`).toBe(shape(real.footer))
}

function compareHeadline(report: SummaryReport, mode: 'latest' | 'window' = 'latest', days = 30) {
  const real = render(<SummaryCatalogue part="headline" report={report} days={days} mode={mode} />)
  const donut = realFrame('summary-donut')
  const bars = realFrame('summary-suites')
  const realParts = { donut: { ...donut }, bars: { ...bars } }
  const shell = render(<SummaryCatalogueShell part="headline" report={report} days={days} mode={mode} />)
  expectSameBox(realParts.donut, shellFrame('Status breakdown'), 'donut')
  expectSameBox(realParts.bars, shellFrame('Results by suite'), 'suites')
  real.unmount()
  shell.unmount()
}

function compareTopFailing(report: SummaryReport, days = 30) {
  const real = render(<SummaryCatalogue part="top-failing" report={report} days={days} mode="latest" />)
  const shell = render(<SummaryCatalogueShell part="top-failing" report={report} days={days} mode="latest" />)
  expectSameBox(realFrame('summary-top-failing'), shellFrame('Failures by test'), 'failures')
  real.unmount()
  shell.unmount()
}

beforeEach(() => {
  act(() => usePresentationStore.getState().setEnabled(false))
})
afterEach(() => {
  act(() => usePresentationStore.getState().setEnabled(false))
})

describe('SummaryCatalogueShell — the same boxes as the real frames', () => {
  it('headline, three suites: the plot heights the page asks for', () => {
    compareHeadline(makeReport())
  })

  it('headline, both aggregation modes and the one-day window (the takeaways follow the page)', () => {
    compareHeadline(makeReport({ mode: 'window' }), 'window', 1)
  })

  it('headline, an older payload without the basis label', () => {
    const report = makeReport()
    report.totals = { ...report.totals, pass_rate_basis_label: undefined }
    compareHeadline(report)
  })

  it('headline, 12 and 50 suites: the bars plot grows with its rows', () => {
    compareHeadline(makeReport({ suites: suites(12) }))
    compareHeadline(makeReport({ suites: suites(50) }))
  })

  it('headline, 51 and 120 suites: paged bars, with the notes and page controls in the footer', () => {
    compareHeadline(makeReport({ suites: suites(51) }))
    compareHeadline(makeReport({ suites: suites(120) }))
  })

  it('headline, nothing to draw: no toolbar, the state message at the floor', () => {
    compareHeadline(
      makeReport({
        suites: [suite('empty', 0, 0, 0, 0)],
        totals: { ...makeReport().totals, passed: 0, failed: 0, skipped: 0, broken: 0 },
      }),
    )
    compareHeadline(makeReport({ suites: [] }))
  })

  it('presentation mode: the plot boxes grow by the mode’s scale, as the drawings do', () => {
    act(() => usePresentationStore.getState().setEnabled(true))
    compareHeadline(makeReport())
    compareHeadline(makeReport({ suites: suites(30) }))
    compareTopFailing(makeReport())
  })

  it('top failing, under ten tests: the requested height', () => {
    compareTopFailing(makeReport())
  })

  it('top failing, a tie at the 10th: the tied bars are kept and the footer says so', () => {
    // 12 tests, the 10th and 11th tied (the visual fixture's shape).
    compareTopFailing(makeReport({ top_failing_tests: failing([14, 11, 9, 8, 7, 6, 5, 4, 4, 3, 3, 1]) }))
  })

  it('top failing, a tie past the cap', () => {
    compareTopFailing(makeReport({ top_failing_tests: failing([9, 8, 7, 6, 5, 4, 3, 2, 1, ...Array(20).fill(1)]) }))
  })
})

describe('SummaryCatalogueShell — what it is', () => {
  it('draws the titles and takeaways from the page’s report, with no chart', () => {
    render(<SummaryCatalogueShell part="headline" report={makeReport()} days={30} mode="latest" />)
    const root = document.querySelector('[data-summary-catalogue-shell="headline"]') as HTMLElement
    expect(root.getAttribute('aria-busy')).toBe('true')
    expect(Array.from(root.querySelectorAll('h2'), (h) => h.textContent)).toEqual(['Status breakdown', 'Results by suite'])
    expect(root.textContent).toMatch(/Tests by status, per unique test · latest run per suite/)
    // Not a chart: no svg drawing, no frame contract specs could mistake for one, nothing to tab to.
    expect(root.querySelector('svg.recharts-surface, [data-chart-frame], [data-catalogue-section]')).toBeNull()
    for (const button of Array.from(root.querySelectorAll('button'))) {
      expect(button.tabIndex).toBe(-1)
      expect(button.closest('[aria-hidden="true"]')).not.toBeNull()
    }
  })

  it('holds the lazy trend’s place exactly as the section’s LazySection placeholder does', () => {
    render(<SummaryCatalogueShell part="headline" report={makeReport()} days={30} mode="latest" />)
    const root = document.querySelector('[data-summary-catalogue-shell="headline"]') as HTMLElement
    const trend = root.lastElementChild as HTMLElement
    expect(trend.getAttribute('aria-hidden')).toBe('true')
    expect(trend.style.minHeight).toBe(`${SUMMARY_TREND_HEIGHT + SUMMARY_TREND_CHROME_PX}px`)
  })

  it('scales by the kit’s presentation factor', () => {
    // The shell keeps its own copy (no chart import); this holds it to the kit's.
    act(() => usePresentationStore.getState().setEnabled(true))
    render(<SummaryCatalogueShell part="headline" report={makeReport()} days={30} mode="latest" />)
    const skeleton = shellFrame('Status breakdown').plotBox
    expect(skeleton).toBe(Math.round(240 * PRESENTATION_MODE_SCALE))
  })
})
