/**
 * Inbox › Approvals in All Projects mode (moved from `ReviewsPage.test.tsx` with
 * the unrouted Review Queue page shell's deletion, W25c HYG-01; `/reviews`
 * redirects here).
 *
 * Every approval queue belongs to one project, so the tab shows the project
 * prompt instead of a queue: it returns before any hook of `Approvals` runs —
 * no source picker, no queue, no count request (the unit-test network guard
 * would fail on one). Only the project store is replaced; the real
 * `ALL_PROJECTS_ID` sentinel is kept.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/store/projectStore', async (importActual) => {
  const actual = await importActual<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector: (s: Record<string, unknown>) => unknown) =>
      selector({ activeProjectId: actual.ALL_PROJECTS_ID, projects: [], setActiveProject: vi.fn() }),
  }
})

import ApprovalsTab from './ApprovalsTab'

describe('ApprovalsTab — All Projects mode', () => {
  it('shows the project picker instead of a queue, and no approval source picker', () => {
    render(
      <MemoryRouter>
        <ApprovalsTab />
      </MemoryRouter>,
    )
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(screen.queryByTestId('review-row')).not.toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Approval source' })).toBeNull()
  })
})
