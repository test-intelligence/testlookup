/**
 * The prompt that used to be a dead end.
 *
 * Six pages rendered an icon, a title and a sentence telling the reader to go
 * and use the top bar — on a screen that had just replaced everything they came
 * for. Reported after clicking "Open flaky coach" from the dashboard as "shows
 * a blank page"; it was not blank, it was this, with nothing on it to press.
 *
 * So the load-bearing assertion here is not that it renders. It is that
 * choosing a project from it actually changes the active project — otherwise
 * this is the same dead end with a decorative select on it.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocked = vi.hoisted(() => ({
  state: {
    projects: [] as { id: string; name: string }[],
    setActiveProject: vi.fn(),
  },
}))

vi.mock('@/store/projectStore', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.state) => unknown) =>
      selector ? selector(mocked.state) : mocked.state,
  }
})

import ProjectRequiredEmptyState from './ProjectRequiredEmptyState'

const CHECKOUT = { id: 'p-1', name: 'Checkout' }
const SEARCH = { id: 'p-2', name: 'Search' }

beforeEach(() => {
  mocked.state.projects = [CHECKOUT, SEARCH]
  mocked.state.setActiveProject = vi.fn()
})

describe('ProjectRequiredEmptyState', () => {
  it('keeps the exact title the page tests assert on', () => {
    // `GitLabIntegrationPage.test.tsx` and `RetentionPage.test.tsx` both do
    // findByText('Select a project'). Those assertions are still correct — the
    // prompt should keep saying what it says — so the string is part of the
    // contract, not an accident.
    render(<ProjectRequiredEmptyState description="Per project." />)
    expect(screen.getByText('Select a project')).toBeTruthy()
  })

  it('says why THIS page needs one, not a generic sentence', () => {
    // A generic message gives the reader no way to tell a deliberate
    // restriction from a fault.
    render(
      <ProjectRequiredEmptyState description="API keys are scoped to a single project." />,
    )
    expect(
      screen.getByText('API keys are scoped to a single project.'),
    ).toBeTruthy()
  })

  it('offers the projects to choose from', () => {
    render(<ProjectRequiredEmptyState description="x" />)
    const picker = screen.getByLabelText('Choose a project to continue')
    expect(picker).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Checkout' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Search' })).toBeTruthy()
  })

  it('actually changes the active project when one is chosen', () => {
    // The whole point. A picker that renders but does not commit is the dead
    // end again, now with a control that reads as broken.
    render(<ProjectRequiredEmptyState description="x" />)

    fireEvent.change(screen.getByLabelText('Choose a project to continue'), {
      target: { value: 'p-2' },
    })

    expect(mocked.state.setActiveProject).toHaveBeenCalledWith(SEARCH)
  })

  it('passes the project object, never null', () => {
    // `setActiveProject(null)` maps back to ALL_PROJECTS_ID, which re-renders
    // this very prompt — a control that appears to do nothing. Guarding the
    // lookup rather than passing `?? null` is what prevents that.
    render(<ProjectRequiredEmptyState description="x" />)
    // Drive the change handler with an id the store does not know, which is
    // what a stale option would produce.
    fireEvent.change(screen.getByLabelText('Choose a project to continue'), {
      target: { value: 'no-such-project' },
    })

    expect(mocked.state.setActiveProject).not.toHaveBeenCalled()
  })

  it('claims neither "loading" nor "none" when the list is empty', () => {
    // The component cannot tell a fetch in flight from a deployment with no
    // projects, so it asserts neither.
    mocked.state.projects = []
    render(<ProjectRequiredEmptyState description="x" />)

    expect(screen.getByText('No projects loaded')).toBeTruthy()
    expect(screen.queryByText(/loading/i)).toBeNull()
  })
})
