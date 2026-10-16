/**
 * The failure-signature table paginates.
 *
 * The failure-signature list (`SignatureClusters`, in the side panel since UX
 * redesign P3; a card under the table before; FAIL-03 groups by the real
 * error) once rendered every member of the primary cluster plus every outlier
 * in one flat list. The card sat beside
 * the scorecard in a fixed-height row, so a window containing more than a
 * handful of matching builds stretched the page — and the cluster is
 * *expected* to be large, since its whole premise is "many builds share one
 * signature".
 *
 * Two behaviours worth pinning beyond "it slices":
 *
 *  - the page resets when the cluster changes (project switch / time window),
 *    otherwise a viewer parked on page 3 lands on an empty table;
 *  - the page index is clamped during render, so a cluster that shrinks cannot
 *    show a blank table for the frame before any reset takes effect.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useRuns', () => ({ useRuns: () => ({ runs: [], isLoading: false }) }))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: () => ({ options: [] }) }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: false }) }))

import type { TestRun } from '@/types/runs'
import { SignatureClusters, SIGNATURE_ROWS_PER_PAGE } from './RunsPage'
import { buildSignatureModel } from './runsList/runSignatures'

function makeRun(i: number): TestRun {
  return {
    id: `run-${i}`,
    build_number: `${1000 + i}`,
    status: 'FAILED',
    passed_tests: 40,
    failed_tests: 1,
    skipped_tests: 0,
    total_tests: 41,
    pass_rate: 97.6,
    created_at: new Date(2026, 0, 1 + i).toISOString(),
    branch: 'main',
  }
}

/** n failed builds sharing one dominant error: one group of n. */
function makeCluster(n: number) {
  const runs = Array.from({ length: n }, (_, i) => makeRun(i))
  return buildSignatureModel(runs, {
    items: runs.map((r) => ({
      run_id: r.id, failing_tests: 1, read: 1, capped: false, dominant: 'timeout after #s',
      signatures: [{ signature: 'timeout after #s', label: 'Timeout after 30s', count: 1, test_count: 1, tests: ['test_slow'] }],
    })),
    unavailable: [], max_ids: 50, rows_per_run_cap: 500,
  })
}

function renderCard(memberCount: number) {
  return render(
    <MemoryRouter>
      <SignatureClusters
        model={makeCluster(memberCount)}
        onJumpToRow={() => undefined}
      />
    </MemoryRouter>,
  )
}

function visibleBuildNumbers() {
  return screen.getAllByText(/^#?10\d\d$/).map((el) => el.textContent ?? '')
}

describe('failure-signature table pagination', () => {
  it('renders at most one page of rows', () => {
    renderCard(SIGNATURE_ROWS_PER_PAGE * 3)
    expect(visibleBuildNumbers().length).toBeLessThanOrEqual(SIGNATURE_ROWS_PER_PAGE)
  })

  it('shows the pager only when there is more than one page', () => {
    const { unmount } = renderCard(SIGNATURE_ROWS_PER_PAGE)
    expect(screen.queryByText(/Page \d+ of \d+/)).toBeNull()
    unmount()

    renderCard(SIGNATURE_ROWS_PER_PAGE + 1)
    expect(screen.getByText(/Page 1 of 2/)).toBeTruthy()
  })

  it('advances to the next page and shows different rows', () => {
    renderCard(SIGNATURE_ROWS_PER_PAGE * 2)
    const firstPage = visibleBuildNumbers()

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))

    expect(screen.getByText(/Page 2 of 2/)).toBeTruthy()
    expect(visibleBuildNumbers()).not.toEqual(firstPage)
  })

  it('reports the full row count, not the page size', () => {
    const total = SIGNATURE_ROWS_PER_PAGE * 2
    renderCard(total)
    expect(screen.getByText(`${total} total results`)).toBeTruthy()
  })

  it('resets to page 1 when the cluster changes underneath the viewer', () => {
    const { rerender } = render(
      <MemoryRouter>
        <SignatureClusters
          model={makeCluster(SIGNATURE_ROWS_PER_PAGE * 3)}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText(/Page 2 of 3/)).toBeTruthy()

    // Project switch / time-window change → a smaller cluster.
    rerender(
      <MemoryRouter>
        <SignatureClusters
          model={makeCluster(SIGNATURE_ROWS_PER_PAGE + 1)}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Page 1 of 2/)).toBeTruthy()
    expect(visibleBuildNumbers().length).toBeGreaterThan(0)
  })

  it('never renders an empty table when the cluster shrinks', () => {
    const { rerender } = render(
      <MemoryRouter>
        <SignatureClusters
          model={makeCluster(SIGNATURE_ROWS_PER_PAGE * 4)}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    rerender(
      <MemoryRouter>
        <SignatureClusters
          model={makeCluster(2)}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    expect(visibleBuildNumbers().length).toBe(2)
  })

  it('still renders the empty state when there is no cluster', () => {
    render(
      <MemoryRouter>
        <SignatureClusters
          model={buildSignatureModel([], undefined)}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText(/nothing to group/i)).toBeTruthy()
  })
})
