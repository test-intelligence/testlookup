import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import IntelligenceHubPage from './IntelligenceHubPage'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/store/projectStore', () => {
  const ALL_PROJECTS_ID = 'all'
  return {
    ALL_PROJECTS_ID,
    useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) =>
      selector({ activeProjectId: ALL_PROJECTS_ID }),
  }
})

/**
 * Regression cover for UX audit issue 1: the "Recent runs analyzed" table
 * rendered Started and End as two separate full-`toLocaleString()` columns
 * under `whitespace-nowrap`, inside an `overflow-x-auto` wrapper — so at laptop
 * widths they scrolled out of sight, and below 768px `hidden md:table-cell`
 * removed them outright even though timing is the sort key.
 */
describe('IntelligenceHubPage timing column', () => {
  const renderPage = async () => {
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [{
          id: 'run-1', build_number: '42', status: 'PASSED', failed_tests: 0,
          passed_tests: 120, broken_tests: 0, skipped_tests: 0, total_tests: 120,
          pass_rate: 100, branch: 'main', project_name: 'Project One',
          created_at: '2026-04-03T15:00:00Z', start_time: '2026-04-03T15:00:00Z',
          end_time: '2026-04-03T15:08:12Z', duration_ms: 492000,
        }],
      },
      isLoading: false,
    })
    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes><Route path="/intelligence" element={<IntelligenceHubPage />} /></Routes>
      </MemoryRouter>,
    )
  }

  it('exposes a single Timing column instead of separate Started and End', async () => {
    await renderPage()
    expect(await screen.findByRole('columnheader', { name: /Timing/i })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /^End$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /^Started$/i })).not.toBeInTheDocument()
  })

  it('keeps the timing cell visible at every width', async () => {
    await renderPage()
    await screen.findByRole('columnheader', { name: /Timing/i })
    // The sort key must never be breakpoint-hidden.
    document.querySelectorAll('th, td').forEach(cell => {
      expect(cell.className).not.toMatch(/md:table-cell/)
    })
  })

  it('makes Build the flexible column so Timing cannot be pushed off-screen', async () => {
    await renderPage()
    const build = await screen.findByRole('columnheader', { name: /Build/i })
    // `min-w-0` on a cell does NOT shrink an auto-layout table column — it
    // still sizes to content. On the live deployment that left Build at 376px
    // and the table at 1007px inside a 641px panel, so Pass rate and Timing
    // were scrolled out of view at a normal laptop width. `w-full max-w-0` is
    // the pair that actually lets the column shrink and truncate.
    expect(build.className.split(' ')).toContain('w-full')
    expect(build.className.split(' ')).toContain('max-w-0')
    // Only Build flexes; the rest must stay content-sized.
    const timing = screen.getByRole('columnheader', { name: /Timing/i })
    expect(timing.className.split(' ')).not.toContain('max-w-0')
  })

  it('no longer renders a full toLocaleString datetime in the row', async () => {
    await renderPage()
    await screen.findByRole('columnheader', { name: /Timing/i })
    const full = new Date('2026-04-03T15:00:00Z').toLocaleString()
    expect(screen.queryByText(full)).not.toBeInTheDocument()
  })
})

/**
 * Regression cover for the column BUDGET, measured on the live deployment.
 *
 * Making Build flexible was necessary but not sufficient: the four fixed
 * columns were consuming 631px of a 641px panel, so Build collapsed to 28px
 * and the table still overflowed by 19px. A `max-width` on the <th> alone does
 * not help either — an auto-layout column cannot shrink below its CELLS'
 * min-content width, so the cap has to land on both. That invariant is what
 * these two tests exist for and it has not changed.
 *
 * The NUMBERS changed on 2026-09-18 (BUG-005). The original budget was taken
 * at a single viewport, and the 86px Pass rate cap was narrower than the
 * meter-plus-percentage it contains, so the number was cut mid-glyph; Build,
 * as the only flexible column, then took 53.8% of the row at 1920px. Pass rate
 * is now 120px (its own content width) and the table is capped so Build stops
 * growing.
 *
 * These assertions are on class strings, which pin the mechanism. The
 * behaviour — "the user can actually read the number, at more than one
 * viewport" — is owned by
 * `tests/ci-e2e/intelligence-table-geometry.spec.ts`, which measures computed
 * geometry at 1345px and 1920px. Class names alone could never have caught the
 * defect that produced these numbers.
 */
describe('IntelligenceHubPage column budget', () => {
  const CAPPED = [
    { name: /Suite/i, cap: 'max-w-[110px]' },
    { name: /Status/i, cap: 'max-w-[88px]' },
    { name: /Pass rate/i, cap: 'max-w-[120px]' },
    { name: /Timing/i, cap: 'max-w-[230px]' },
  ]

  it('caps every fixed column so Build keeps a readable share', async () => {
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{
        id: 'r1', build_number: '42', status: 'PASSED', failed_tests: 0, passed_tests: 1,
        broken_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 100, branch: 'main',
        created_at: '2026-04-03T15:00:00Z', start_time: '2026-04-03T15:00:00Z',
        end_time: '2026-04-03T15:08:12Z', duration_ms: 492000,
      }] },
      isLoading: false,
    })
    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes><Route path="/intelligence" element={<IntelligenceHubPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('columnheader', { name: /Timing/i })

    for (const { name, cap } of CAPPED) {
      const th = screen.getByRole('columnheader', { name })
      expect(th.className.split(' ')).toContain(cap)
    }
    // Build floors so it can never collapse to an unreadable sliver.
    const build = screen.getByRole('columnheader', { name: /Build/i })
    expect(build.className.split(' ')).toContain('min-w-[150px]')
  })

  it('caps the CELLS too, not just the headers', async () => {
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{
        id: 'r1', build_number: '42', status: 'PASSED', failed_tests: 0, passed_tests: 1,
        broken_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 100, branch: 'main',
        created_at: '2026-04-03T15:00:00Z', start_time: '2026-04-03T15:00:00Z',
        end_time: '2026-04-03T15:08:12Z', duration_ms: 492000,
      }] },
      isLoading: false,
    })
    const { container } = render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes><Route path="/intelligence" element={<IntelligenceHubPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('columnheader', { name: /Timing/i })
    const cells = [...container.querySelectorAll('tbody tr td')]
    expect(cells).toHaveLength(5)
    // Header-only caps left the table 35px over its wrapper on the deployment.
    expect(cells[1].className).toContain('max-w-[110px]')
    expect(cells[2].className).toContain('max-w-[88px]')
    expect(cells[3].className).toContain('max-w-[120px]')
    expect(cells[4].className).toContain('max-w-[230px]')
  })
})
