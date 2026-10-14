/**
 * HON-09, through the real route table: an unknown path renders "Page not
 * found" inside the shell with the URL as typed (the catch-all used to
 * `<Navigate to="/overview" replace />`), and `/audit` (the /releases rail's
 * old "View full audit log" href, never a route) forwards to the Activity feed
 * on its Release category.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

const permissions = vi.hoisted(() => ({ canAccessManagement: true }))

vi.mock('@/components/auth/ProtectedRoute', () => ({
  default: () => <Outlet />,
}))
// The shell, reduced to a marker around the outlet: "inside the shell" is
// what the not-found page must keep (the sidebar and top bar stay).
vi.mock('@/components/layout/AppLayout', () => ({
  default: () => (
    <div data-testid="app-shell">
      <Outlet />
    </div>
  ),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => permissions,
}))
vi.mock('@/hooks/useWebVitals', () => ({ useWebVitals: () => undefined }))
vi.mock('@/pages/OverviewPage', () => ({ default: () => <h1>Overview route</h1> }))
vi.mock('@/pages/ActivityPage', () => ({ default: () => <h1>Activity route</h1> }))

/** The router's location, rendered so a test can read the URL the app ends on. */
function Location() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Location />
    </MemoryRouter>,
  )
}

describe('the in-shell catch-all (HON-09)', () => {
  beforeEach(() => {
    permissions.canAccessManagement = true
  })

  it('renders "Page not found" inside the shell for an unknown path, and keeps the URL', async () => {
    renderAt('/no-such-page?from=email')

    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByText('This page does not exist.')).toBeInTheDocument()
    expect(screen.getByTestId('not-found-path')).toHaveTextContent('/no-such-page?from=email')
    expect(screen.getByTestId('app-shell')).toContainElement(screen.getByTestId('not-found-page'))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/no-such-page\?from=email$/)
    expect(screen.queryByRole('heading', { name: 'Overview route' })).toBeNull()
    await waitFor(() => expect(document.title).toBe('Page not found · TestLookup'))
  })

  it('offers Overview, Search and Activity, and Activity only to a role that can open it', async () => {
    renderAt('/this-route-does-not-exist')
    const nav = await screen.findByRole('navigation', { name: 'Where to go instead' })
    expect(Array.from(nav.querySelectorAll('a'), (a) => [a.textContent?.trim(), a.getAttribute('href')])).toEqual([
      ['Overview', '/overview'],
      ['Search', '/search'],
      ['Activity', '/activity'],
    ])
  })

  it('leaves Activity out for a role it would bounce', async () => {
    permissions.canAccessManagement = false
    renderAt('/this-route-does-not-exist')
    const nav = await screen.findByRole('navigation', { name: 'Where to go instead' })
    expect(Array.from(nav.querySelectorAll('a'), (a) => a.textContent?.trim())).toEqual(['Overview', 'Search'])
  })

  it('shows a hostile path as text, never as markup', async () => {
    renderAt('/%3Cimg%20src=x%20onerror=alert(1)%3E')
    const path = await screen.findByTestId('not-found-path')
    expect(path.querySelector('img')).toBeNull()
    expect(path.children).toHaveLength(0)
  })

  it('forwards the old /audit bookmark to Activity on its Release category', async () => {
    renderAt('/audit')

    expect(await screen.findByRole('heading', { name: 'Activity route' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/activity\?category=release$/)
    expect(screen.queryByText('Page not found')).toBeNull()
  })

  it('still serves a declared route and the index redirect', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { name: 'Overview route' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/overview$/)
  })
})
