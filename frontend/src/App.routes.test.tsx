import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

vi.mock('@/components/auth/ProtectedRoute', () => ({
  default: () => <Outlet />,
}))
vi.mock('@/components/layout/AppLayout', () => ({
  default: () => <Outlet />,
}))
const perms = vi.hoisted(() => ({ canAccessManagement: true }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => perms,
}))
vi.mock('@/hooks/useWebVitals', () => ({ useWebVitals: () => undefined }))
vi.mock('@/hooks/useDocumentTitle', () => ({
  DocumentTitleRouteKeyContext: {
    Provider: ({ children }: { children: React.ReactNode }) => children,
  },
  useRouteDocumentTitle: () => 'test-route',
}))
vi.mock('@/pages/ReleasesPage', () => ({
  default: () => <h1>Release detail route</h1>,
}))

describe('management routes', () => {
  beforeEach(() => {
    perms.canAccessManagement = true
  })

  it('renders the Releases page for a release detail deep link', async () => {
    render(
      <MemoryRouter initialEntries={['/releases/release-1']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Release detail route' })).toBeVisible()
  })

  // D-118 (owner decision 4): Releases is read for every role; the page hides
  // its writes below QA lead. A QA engineer or viewer is no longer sent home.
  it.each(['/releases', '/releases/release-1'])('renders %s for a role below QA lead, not a redirect', async (path) => {
    perms.canAccessManagement = false
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Release detail route' })).toBeVisible()
  })
})