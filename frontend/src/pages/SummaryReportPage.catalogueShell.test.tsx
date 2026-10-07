/**
 * Summary report, while the catalogue chunk is still on its way (R1-2,
 * R1 (a)): the page draws the frames' boxes itself, from the report it holds,
 * so nothing below them moves when the charts arrive; and it starts that chunk
 * on mount, not after the report has rendered.
 *
 * The page's lazy loader is held on a gate this file opens, so "before the
 * chunk arrives" is a state a test can sit in; once open, the REAL sections
 * load. The preload is `SummaryReportPage.preload.test.tsx`'s; the shell itself
 * is compared with the real frames in `SummaryCatalogueShell.test.tsx`.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SummaryReport } from '@/types/summaryReport'
import { SUMMARY_TREND_CHROME_PX, SUMMARY_TREND_HEIGHT } from '@/components/reports/catalogue/summaryCatalogueWords'

const mockGet = vi.fn()
// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: { get: (...args: unknown[]) => mockGet(...args), downloadPdf: vi.fn() },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ activeProjectId: 'p1', activeProject: { id: 'p1', name: 'GoogleProject' } }),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

// The trend's request held loading, and the top bar's cached release list (as in SummaryReportPage.test.tsx).
vi.mock('@/components/reports/catalogue/useTrendsSeries', () => ({ useTrendsSeries: () => ({ status: 'loading' }) }))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))

/** The section chunk's arrival: `lazy()` resolves only after `open()`. */
const chunk = vi.hoisted(() => {
  let release = () => {}
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  return { gate, open: () => release() }
})
vi.mock('@/utils/lazyWithRetry', async () => {
  const { lazy } = await import('react')
  return {
    lazyWithRetry: <T,>(load: () => Promise<{ default: T }>) => lazy(async () => {
      await chunk.gate
      return load() as never
    }),
  }
})

import SummaryReportPage from './SummaryReportPage'

function makeReport(): SummaryReport {
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
    suites: [
      {
        suite_name: 'checkout-api',
        total: 120, passed: 102, failed: 13, skipped: 3, broken: 2,
        pass_rate_pct: 85.0, weighted_pass_rate_pct: 87.2,
        last_run_at: '2026-05-15T00:00:00+00:00',
      },
    ],
    top_failing_tests: [{ suite_name: 'checkout-api', class_name: 'CheckoutTests', test_name: 'test_pay', failures: 8 }],
  }
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/reports/summary']}>
        <SummaryReportPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  try { localStorage.removeItem('summary-report.mode') } catch { /* ignore */ }
})

// Order matters in this file: the gate opens once, in the last test.
describe('SummaryReportPage — the catalogue while its chunk loads', () => {
  it('until the chunk answers, the page draws the suite frame from its report, in place', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    // UX redesign P3: the primary is the suite bars alone (`part="suites"`), so is its stand-in.
    const suites = document.querySelector('[data-summary-catalogue-shell="suites"]') as HTMLElement
    expect(suites).not.toBeNull()
    expect(Array.from(suites.querySelectorAll('h2'), (h) => h.textContent)).toEqual(['Results by suite'])
    expect(suites.textContent).toMatch(/Tests by status in each suite, most failed and broken first, per unique test · latest run per suite/)
    // No donut (the KPI row has its counts), no headline, and no top-failing part ("Failures by test", §5).
    expect(screen.queryByRole('heading', { name: 'Status breakdown' })).toBeNull()
    expect(document.querySelectorAll('[data-summary-catalogue-shell]')).toHaveLength(1)
    // Where the section goes: in the primary content, above the per-suite table.
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(document.querySelector('[data-primary]')?.contains(suites)).toBe(true)
    expect(follows(suites, screen.getByRole('heading', { name: /Per-suite breakdown/ }))).toBe(true)
  })

  it('opening "Trend" before the chunk answers holds the trend’s box in the disclosure', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    expect(document.querySelector('[data-summary-catalogue-shell="trend"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Trend/ }))
    const trend = document.querySelector('[data-summary-catalogue-shell="trend"]') as HTMLElement
    expect(trend.closest('[data-disclosure]')).not.toBeNull()
    expect((trend.firstElementChild as HTMLElement).style.minHeight).toBe(`${SUMMARY_TREND_HEIGHT + SUMMARY_TREND_CHROME_PX}px`)
  })

  it('the real sections REPLACE the stand-in when the chunk answers', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    expect(document.querySelector('[data-summary-catalogue-shell]')).not.toBeNull()
    await act(async () => {
      chunk.open()
      await chunk.gate
    })
    // The real section, where the stand-in was.
    await waitFor(
      () => expect(document.querySelector('[data-catalogue-section="summary-suites"]')).not.toBeNull(),
      { timeout: 10_000 },
    )
    expect(document.querySelector('[data-catalogue-section="summary-donut"]')).toBeNull()
    expect(document.querySelector('[data-summary-catalogue-shell]')).toBeNull()
  })
})
