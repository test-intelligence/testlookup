/**
 * UX redesign P2: seed data is a development-build page, like its Settings card.
 * The route stays; a production build redirects it to /settings.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SeedDataPage from './SeedDataPage'

vi.mock('@/hooks/useSeedStatus', () => ({
  useSeedStatus: () => ({ seeded: false, isLoading: false, isError: false, refresh: vi.fn() }),
}))

function renderAt() {
  return render(
    <MemoryRouter initialEntries={['/settings/seed-data']}>
      <Routes>
        <Route path="/settings/seed-data" element={<SeedDataPage />} />
        <Route path="/settings" element={<div>Settings index</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('SeedDataPage', () => {
  it('a production build redirects to Settings', () => {
    vi.stubEnv('DEV', false)
    renderAt()
    expect(screen.getByText('Settings index')).toBeInTheDocument()
  })

  it('a development build shows the page', () => {
    vi.stubEnv('DEV', true)
    renderAt()
    expect(screen.queryByText('Settings index')).toBeNull()
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
  })
})
