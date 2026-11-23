/**
 * S-D-8: Home's activity panel linked "View all" to `/activity` for every
 * role, and `/activity` is a management route: a viewer, tester or QA engineer
 * who followed it was sent back home with no word. The panel itself (the API's
 * activity reads serve any member) stays for everyone; the link is a QA lead's.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/store/authStore'
import RecentActivityPanel from './RecentActivityPanel'

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: object) => unknown) => selector({ activeProjectId: 'proj-1' }),
}))
vi.mock('@/hooks/useActivityFeed', () => ({
  useActivityFeed: () => ({ events: [], isLoading: false, error: undefined }),
}))

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })
afterEach(() => useAuthStore.setState({ user: null }))

function renderPanel() {
  return render(
    <MemoryRouter>
      <RecentActivityPanel />
    </MemoryRouter>,
  )
}

describe('RecentActivityPanel "View all" (S-D-8)', () => {
  it.each(['VIEWER', 'TESTER', 'QA_ENGINEER'])('a %s reads the panel with no link to the QA-lead page', (role) => {
    signInAs(role)
    renderPanel()
    expect(screen.getByText('Runs, releases and changes in this project')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'View all' })).toBeNull()
  })

  it.each(['QA_LEAD', 'ADMIN'])('a %s gets the link to /activity', (role) => {
    signInAs(role)
    renderPanel()
    expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/activity')
  })
})
