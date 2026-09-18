import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import App from './App'

vi.mock('@/components/auth/ProtectedRoute', () => ({
  default: () => <Outlet />,
}))
vi.mock('@/components/layout/AppLayout', () => ({
  default: () => <Outlet />,
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true }),
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
  it('renders the Releases page for a release detail deep link', async () => {
    render(
      <MemoryRouter initialEntries={['/releases/release-1']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Release detail route' })).toBeVisible()
  })
})
