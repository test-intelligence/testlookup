/**
 * The link that knows whether its destination can show anything.
 *
 * `OverviewPage` offered "Open flaky coach" from a card holding a CROSS-project
 * count while All Projects — the default selection — is exactly the state that
 * page refuses to render in. The link was not wrong to exist. It was wrong to
 * look identical to the six beside it that work.
 *
 * The assertion that matters most is the one about NOT hiding it: with the
 * destination now carrying a picker, following the link is a working route to
 * the data, and suppressing it would remove the only path there.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ALL_PROJECTS_ID } from '@/store/projectStore'

const mocked = vi.hoisted(() => ({
  state: { activeProjectId: null as string | null },
}))

vi.mock('@/store/projectStore', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.state) => unknown) =>
      selector ? selector(mocked.state) : mocked.state,
  }
})

import ScopedLink from './ScopedLink'

const A_PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'

const renderLink = (to: string) =>
  render(
    <MemoryRouter>
      <ScopedLink to={to}>Open flaky coach</ScopedLink>
    </MemoryRouter>,
  )

beforeEach(() => {
  mocked.state.activeProjectId = ALL_PROJECTS_ID
})

describe('ScopedLink', () => {
  it('warns before the click when the destination needs a project', () => {
    renderLink('/flaky-coach')
    expect(screen.getByText(/pick a project/)).toBeTruthy()
  })

  it('still renders a working link — it does not hide it', () => {
    // Hiding would remove the only path to the data for an admin browsing all
    // projects, who can resolve the scope on the destination itself.
    renderLink('/flaky-coach')
    const link = screen.getByRole('link', { name: /Open flaky coach/ })
    expect(link.getAttribute('href')).toBe('/flaky-coach')
  })

  it('explains the extra step in the title', () => {
    renderLink('/flaky-coach')
    expect(
      screen.getByRole('link', { name: /Open flaky coach/ }).getAttribute('title'),
    ).toMatch(/one project at a time/)
  })

  it('leaves an unrestricted destination completely alone', () => {
    // The control: a component that always rendered the qualifier would pass
    // the first test and make every link in the app noisier.
    renderLink('/runs')
    expect(screen.queryByText(/pick a project/)).toBeNull()
    expect(
      screen.getByRole('link', { name: /Open flaky coach/ }).getAttribute('title'),
    ).toBeNull()
  })

  it('drops the qualifier once a project is pinned', () => {
    mocked.state.activeProjectId = A_PROJECT
    renderLink('/flaky-coach')
    expect(screen.queryByText(/pick a project/)).toBeNull()
  })

  it('warns when nothing is selected at all', () => {
    mocked.state.activeProjectId = null
    renderLink('/flaky-coach')
    expect(screen.getByText(/pick a project/)).toBeTruthy()
  })

  it('sees through a query string on the destination', () => {
    // Real call sites carry them. A scope check that silently fails to match
    // reads as "reachable", which is the original bug with an extra step.
    renderLink('/flaky-coach?days=30')
    expect(screen.getByText(/pick a project/)).toBeTruthy()
  })

  it('can warn in the title alone, for a link inside a sentence', () => {
    // FirstRunGuide's setup instructions read "Generate a project key under
    // Settings → API Keys and pass it as X-API-Key". A visible qualifier lands
    // mid-sentence and reads worse than no warning; the prose already says "a
    // project key". The title still carries it.
    render(
      <MemoryRouter>
        <ScopedLink to="/settings/api-keys" hintInTitleOnly>
          Settings → API Keys
        </ScopedLink>
      </MemoryRouter>,
    )

    expect(screen.queryByText(/pick a project/)).toBeNull()
    expect(
      screen.getByRole('link', { name: /API Keys/ }).getAttribute('title'),
    ).toMatch(/one project at a time/)
  })

  it('still renders nothing extra in title-only mode when reachable', () => {
    // The control for the control: a component that always set a title would
    // pass the test above and mark every link in the app.
    mocked.state.activeProjectId = A_PROJECT
    render(
      <MemoryRouter>
        <ScopedLink to="/settings/api-keys" hintInTitleOnly>
          Settings → API Keys
        </ScopedLink>
      </MemoryRouter>,
    )
    expect(
      screen.getByRole('link', { name: /API Keys/ }).getAttribute('title'),
    ).toBeNull()
  })

  it('keeps the qualifier readable by a screen reader', () => {
    // "One more step before you see data" is exactly what a screen-reader user
    // needs BEFORE following a link, so it must not be aria-hidden.
    renderLink('/flaky-coach')
    const hint = screen.getByText(/pick a project/)
    expect(hint.getAttribute('aria-hidden')).toBeNull()
  })
})
