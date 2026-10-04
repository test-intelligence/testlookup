/**
 * Summary report, flag on, while the catalogue chunk is still on its way
 * (R1-2, R1 (a)): the page draws the frames' boxes itself, from the report it
 * holds, so nothing below them moves when the charts arrive; and it starts
 * that chunk as soon as the flag answers, not after the report has rendered.
 *
 * The page's lazy loader is held on a gate this file opens, so "before the
 * chunk arrives" is a state a test can sit in; once open, the REAL sections
 * load. The preload is `SummaryReportPage.preload.test.tsx`'s; the shell itself
 * is compared with the real frames in `SummaryCatalogueShell.test.tsx`.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SummaryReport } from '@/types/summaryReport'

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

const rollout = vi.hoisted(() => ({ on: false }))
vi.mock('@/components/reports/catalogue/useCatalogueRollout', () => ({
  useCatalogueRollout: () => rollout.on,
  useCatalogueRolloutStatus: () => rollout.on,
  useAdvancedRollout: () => false,
}))

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
afterEach(() => {
  rollout.on = false
})

// Order matters in this file: the gate opens once, in the last test.
describe('SummaryReportPage — the catalogue while its chunk loads', () => {
  it('flag off: no stand-in is drawn', async () => {
    rollout.on = false
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    expect(document.querySelector('[data-summary-catalogue-shell]')).toBeNull()
  })

  it('flag on: until the chunk answers, the page draws both parts’ frames from its report, in place', async () => {
    rollout.on = true
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    const headline = document.querySelector('[data-summary-catalogue-shell="headline"]') as HTMLElement
    const failing = document.querySelector('[data-summary-catalogue-shell="top-failing"]') as HTMLElement
    expect(headline).not.toBeNull()
    expect(failing).not.toBeNull()
    expect(Array.from(headline.querySelectorAll('h2'), (h) => h.textContent)).toEqual(['Status breakdown', 'Results by suite'])
    expect(headline.textContent).toMatch(/Tests by status in each suite, most failed and broken first, per unique test · latest run per suite/)
    expect(failing.querySelector('h2')?.textContent).toBe('Failures by test')
    expect(failing.textContent).toMatch(/The tests that failed most in the last 30 days, latest run per suite/)
    // Where the sections go: above the per-suite table and above the top-failing table.
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(headline, screen.getByRole('heading', { name: /Per-suite breakdown/ }))).toBe(true)
    expect(follows(failing, screen.getByRole('heading', { name: /^Top failing tests/ }))).toBe(true)
  })

  it('flag on: the real sections REPLACE the stand-in when the chunk answers', async () => {
    rollout.on = true
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    expect(document.querySelector('[data-summary-catalogue-shell]')).not.toBeNull()
    await act(async () => {
      chunk.open()
      await chunk.gate
    })
    // The real sections, where the stand-in was.
    await waitFor(
      () => expect(document.querySelector('[data-catalogue-section="summary-top-failing"]')).not.toBeNull(),
      { timeout: 10_000 },
    )
    expect(document.querySelector('[data-catalogue-section="summary-donut"]')).not.toBeNull()
    expect(document.querySelector('[data-summary-catalogue-shell]')).toBeNull()
  })
})
