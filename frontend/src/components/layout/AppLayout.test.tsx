import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from './AppLayout'

vi.mock('./TopBar', () => ({
  default: () => <div>TopBar Stub</div>,
}))

describe('AppLayout', () => {
  it('renders sidebar, topbar, and outlet content', () => {
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/overview" element={<div>Overview Content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('TopBar Stub')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Overview Content')).toBeInTheDocument()
  })
})
