/**
 * The failure-signature table paginates.
 *
 * `SignatureClusterCard` rendered every member of the primary cluster plus every
 * outlier in one flat list. The card sits beside the scorecard in a fixed-height
 * row, so a window containing more than a handful of matching builds stretched
 * the page — and the cluster is *expected* to be large, since its whole premise
 * is "many builds share one signature".
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

import { SignatureClusterCard, SIGNATURE_ROWS_PER_PAGE } from './RunsPage'

function makeRun(i: number) {
  return {
    id: `run-${i}`,
    build_number: `${1000 + i}`,
    status: 'COMPLETED',
    passed_tests: 40,
    failed_tests: 1,
    total_tests: 41,
    created_at: new Date(2026, 0, 1 + i).toISOString(),
    branch: 'main',
  } as never
}

function makeCluster(n: number) {
  return {
    signature: '40|1|41',
    members: Array.from({ length: n }, (_, i) => makeRun(i)),
  } as never
}

function renderCard(memberCount: number) {
  return render(
    <MemoryRouter>
      <SignatureClusterCard
        primaryCluster={makeCluster(memberCount)}
        outlierClusters={[]}
        totalRuns={memberCount}
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

    const buttons = screen.getAllByRole('button')
    const next = buttons[buttons.length - 1]
    fireEvent.click(next)

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
        <SignatureClusterCard
          primaryCluster={makeCluster(SIGNATURE_ROWS_PER_PAGE * 3)}
          outlierClusters={[]}
          totalRuns={SIGNATURE_ROWS_PER_PAGE * 3}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    const buttons = screen.getAllByRole('button')
    fireEvent.click(buttons[buttons.length - 1])
    expect(screen.getByText(/Page 2 of 3/)).toBeTruthy()

    // Project switch / time-window change → a smaller cluster.
    rerender(
      <MemoryRouter>
        <SignatureClusterCard
          primaryCluster={makeCluster(SIGNATURE_ROWS_PER_PAGE + 1)}
          outlierClusters={[]}
          totalRuns={SIGNATURE_ROWS_PER_PAGE + 1}
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
        <SignatureClusterCard
          primaryCluster={makeCluster(SIGNATURE_ROWS_PER_PAGE * 4)}
          outlierClusters={[]}
          totalRuns={SIGNATURE_ROWS_PER_PAGE * 4}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    rerender(
      <MemoryRouter>
        <SignatureClusterCard
          primaryCluster={makeCluster(2)}
          outlierClusters={[]}
          totalRuns={2}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    expect(visibleBuildNumbers().length).toBe(2)
  })

  it('still renders the empty state when there is no cluster', () => {
    render(
      <MemoryRouter>
        <SignatureClusterCard
          primaryCluster={null}
          outlierClusters={[]}
          totalRuns={0}
          onJumpToRow={() => undefined}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText(/nothing to cluster/i)).toBeTruthy()
  })
})
