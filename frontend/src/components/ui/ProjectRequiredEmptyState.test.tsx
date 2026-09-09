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
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The prompt renders a `Create a project` Link when the list is empty, so its
// tests mount it inside a router. AppLayout supplies one in the real app.
const renderInRouter = (ui: ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>)

const mocked = vi.hoisted(() => ({
  canAccessManagement: true,
  state: {
    projects: [] as { id: string; name: string }[],
    setActiveProject: vi.fn(),
  },
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: mocked.canAccessManagement }),
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
  mocked.canAccessManagement = true
  mocked.state.projects = [CHECKOUT, SEARCH]
  mocked.state.setActiveProject = vi.fn()
})

describe('ProjectRequiredEmptyState', () => {
  it('keeps the exact title the page tests assert on', () => {
    // `GitLabIntegrationPage.test.tsx` and `RetentionPage.test.tsx` both do
    // findByText('Select a project'). Those assertions are still correct — the
    // prompt should keep saying what it says — so the string is part of the
    // contract, not an accident.
    renderInRouter(<ProjectRequiredEmptyState description="Per project." />)
    expect(screen.getByText('Select a project')).toBeTruthy()
  })

  it('says why THIS page needs one, not a generic sentence', () => {
    // A generic message gives the reader no way to tell a deliberate
    // restriction from a fault.
    renderInRouter(
      <ProjectRequiredEmptyState description="API keys are scoped to a single project." />,
    )
    expect(
      screen.getByText('API keys are scoped to a single project.'),
    ).toBeTruthy()
  })

  it('offers the projects to choose from', () => {
    renderInRouter(<ProjectRequiredEmptyState description="x" />)
    const picker = screen.getByLabelText('Choose a project to continue')
    expect(picker).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Checkout' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Search' })).toBeTruthy()
  })

  it('actually changes the active project when one is chosen', () => {
    // The whole point. A picker that renders but does not commit is the dead
    // end again, now with a control that reads as broken.
    renderInRouter(<ProjectRequiredEmptyState description="x" />)

    fireEvent.change(screen.getByLabelText('Choose a project to continue'), {
      target: { value: 'p-2' },
    })

    expect(mocked.state.setActiveProject).toHaveBeenCalledWith(SEARCH)
  })

  it('passes the project object, never null', () => {
    // `setActiveProject(null)` maps back to ALL_PROJECTS_ID, which re-renders
    // this very prompt — a control that appears to do nothing. Guarding the
    // lookup rather than passing `?? null` is what prevents that.
    renderInRouter(<ProjectRequiredEmptyState description="x" />)
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
    renderInRouter(<ProjectRequiredEmptyState description="x" />)

    expect(screen.getByText('No projects loaded')).toBeTruthy()
    expect(screen.queryByText(/loading/i)).toBeNull()
  })

  it('offers a create path when the list is empty — the one dead end the picker cannot resolve', () => {
    // A picker of zero projects is the same "go find the control elsewhere"
    // this component exists to remove. The fresh self-hoster who lands here
    // before any project exists needs a way forward on THIS screen.
    mocked.state.projects = []
    renderInRouter(<ProjectRequiredEmptyState description="x" />)

    const create = screen.getByRole('link', { name: 'Create a project' })
    expect(create.getAttribute('href')).toBe('/projects')
  })

  it('does not nag an established deployment to create another project', () => {
    // When projects exist, picking one is the right move; a create link would
    // just be noise on the common case.
    mocked.state.projects = [CHECKOUT, SEARCH]
    renderInRouter(<ProjectRequiredEmptyState description="x" />)

    expect(screen.queryByRole('link', { name: 'Create a project' })).toBeNull()
  })

  it('gives users without management access a truthful next step', () => {
    mocked.state.projects = []
    mocked.canAccessManagement = false
    renderInRouter(<ProjectRequiredEmptyState description="x" />)

    expect(screen.queryByRole('link', { name: 'Create a project' })).toBeNull()
    expect(
      screen.getByText('Ask a QA lead or administrator to create a project.'),
    ).toBeTruthy()
  })
})
