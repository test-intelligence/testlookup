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

  it('keeps Build shrinkable and Timing floored so Timing cannot be pushed off-screen', async () => {
    await renderPage()
    const build = await screen.findByRole('columnheader', { name: /Build/i })
    // `min-w-0` on a cell does NOT shrink an auto-layout table column — it
    // still sizes to content. On the live deployment that left Build at 376px
    // and the table at 1007px inside a 641px panel, so Pass rate and Timing
    // were scrolled out of view at a normal laptop width. `max-w-0` is what
    // actually lets a column shrink past its content and truncate, and Build
    // is the column that must give.
    expect(build.className.split(' ')).toContain('max-w-0')

    // This used to also assert `w-full` on Build and `not max-w-0` on
    // everything else — i.e. Build as the SOLE flexible column. That design is
    // what pooled every spare pixel into Build (53.8% of the row at 1920px) and
    // then, when the table was capped to stop it, left an empty band at the
    // right-hand edge instead. Columns now take a percentage share of the slack
    // and carry floors.
    //
    // What still has to hold is the concern this test was written for: Timing
    // keeps a floor, so it cannot be squeezed out of view.
    const timing = screen.getByRole('columnheader', { name: /Timing/i })
    expect(timing.className).toMatch(/min-w-\[\d+px\]/)
    // That the table never actually overflows OR falls short of its panel is
    // measured, at two viewports, in
    // `tests/ci-e2e/intelligence-table-geometry.spec.ts`.
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
  const RUN = {
    id: 'r1', build_number: '42', status: 'PASSED', failed_tests: 0, passed_tests: 1,
    broken_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 100, branch: 'main',
    created_at: '2026-04-03T15:00:00Z', start_time: '2026-04-03T15:00:00Z',
    end_time: '2026-04-03T15:08:12Z', duration_ms: 492000,
  }

  /** The width-shaping classes on an element, in a stable order. */
  const widthClasses = (el: Element): string[] =>
    el.className
      .split(' ')
      .filter((c) => /^(w-|min-w-|max-w-)/.test(c))
      .sort()

  async function renderTable() {
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [RUN] }, isLoading: false })
    const { container } = render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes><Route path="/intelligence" element={<IntelligenceHubPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('columnheader', { name: /Timing/i })
    return container
  }

  it('applies the same width budget to the cells as to the headers', async () => {
    // THE invariant, and the only one worth pinning as class strings: an
    // auto-layout column cannot shrink below its CELLS' min-content width, so a
    // budget applied to the <th> alone does nothing. That is what left the
    // table 35px over its wrapper on the deployment.
    //
    // Deliberately NOT asserting the numbers. They have now moved twice — the
    // 86px Pass rate clipped its own percentage, and the 860px table cap left
    // an empty band at the right edge — and a test that re-pins them just has
    // to be edited again next time. The behaviour is owned by
    // `tests/ci-e2e/intelligence-table-geometry.spec.ts`, which measures
    // computed geometry at two viewports.
    const container = await renderTable()
    const headers = [...container.querySelectorAll('thead th')]
    const cells = [...container.querySelectorAll('tbody tr td')]
    expect(headers).toHaveLength(5)
    expect(cells).toHaveLength(5)

    for (let i = 0; i < headers.length; i += 1) {
      expect(
        widthClasses(cells[i]),
        `column ${i} (${headers[i].textContent?.trim()}): the cell's width budget ` +
          'does not match its header, so the column cannot shrink to it',
      ).toEqual(widthClasses(headers[i]))
    }
  })

  it('floors every column so none can collapse to an unreadable sliver', async () => {
    // Floors, not caps, are what the current budget is built on: each column
    // takes a percentage share of the slack and may not fall below what its
    // content needs. Build additionally carries `max-w-0`, which is what lets a
    // column shrink past its content min-width at all.
    const container = await renderTable()
    const headers = [...container.querySelectorAll('thead th')]

    for (const th of headers) {
      expect(
        widthClasses(th).some((c) => c.startsWith('min-w-')),
        `column "${th.textContent?.trim()}" has no floor, so it can collapse`,
      ).toBe(true)
    }
    const build = screen.getByRole('columnheader', { name: /Build/i })
    expect(
      widthClasses(build),
      'Build needs max-w-0 to shrink past its content min-width',
    ).toContain('max-w-0')
  })
})
