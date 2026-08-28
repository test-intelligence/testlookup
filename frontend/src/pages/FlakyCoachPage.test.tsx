/**
 * Regression: the Flaky Coach header hardcoded plural nouns, so a project with
 * exactly one flaky test read "1 flaky tests · 1 quarantine candidates".
 *
 * Found by exploratory testing — ingesting three runs where one test flipped
 * fail → pass → fail produced precisely that state, which is also the ordinary
 * one: a single flaky test is the common case, not an edge case.
 *
 * Sibling of #894, but a different sub-shape: there the noun was pluralised by
 * count and the verb was not; here the noun carried no pluralisation at all, so
 * the #894 guard could not see it.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mockCoach = vi.hoisted(() => ({
  value: { total_flaky: 1, quarantine_candidates: 1, entries: [] } as {
    total_flaky: number
    quarantine_candidates: number
    entries: unknown[]
  },
}))

vi.mock('@/hooks/useTestHealth', () => ({
  useFlakyCoach: () => ({ coach: mockCoach.value, isLoading: false, refresh: vi.fn() }),
}))

vi.mock('@/services/testHealthService', () => ({
  testHealthService: { refreshFlakyCoach: vi.fn() },
}))

vi.mock('@/store/projectStore', async (importActual) => {
  // Spread the real module so constants like ALL_PROJECTS_ID stay available;
  // only the store hook is replaced.
  const actual = await importActual<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (sel: (s: unknown) => unknown) =>
      sel({ activeProjectId: 'p1', projects: [{ id: 'p1', name: 'Proj' }] }),
  }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

import FlakyCoachPage from './FlakyCoachPage'

const renderPage = () =>
  render(
    <MemoryRouter>
      <FlakyCoachPage />
    </MemoryRouter>,
  )

describe('FlakyCoachPage header counts', () => {
  it('uses singular nouns when exactly one of each', () => {
    mockCoach.value = { total_flaky: 1, quarantine_candidates: 1, entries: [] }
    renderPage()

    expect(screen.getByText(/1 flaky test · 1 quarantine candidate/)).toBeInTheDocument()
    // The bug rendered these; assert they are gone rather than only that the
    // singular form appears, since "1 flaky test" is a substring of "1 flaky tests".
    expect(screen.queryByText(/1 flaky tests/)).toBeNull()
    expect(screen.queryByText(/1 quarantine candidates/)).toBeNull()
  })

  it('still pluralises for zero and for many', () => {
    mockCoach.value = { total_flaky: 0, quarantine_candidates: 3, entries: [] }
    renderPage()

    expect(screen.getByText(/0 flaky tests · 3 quarantine candidates/)).toBeInTheDocument()
  })
})
