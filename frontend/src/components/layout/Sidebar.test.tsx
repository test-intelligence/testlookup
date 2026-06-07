import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import Sidebar from './Sidebar'

// Mock usePermissions to control what the Sidebar renders
const mockPermissions = {
  role: 'ADMIN' as const,
  isAdmin: true,
  isQaLead: true,
  isQaEngineer: true,
  canManageUsers: true,
  canManageProjectMembers: true,
  canGenerateApiKeys: true,
  canTriggerLlm: true,
  canAccessManagement: true,
  canViewSettings: true,
  canEditSettings: true,
  hasRole: () => true,
}

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => mockPermissions,
}))

// Control the manual_upload rollout flag (MRU-17).
const mockUpload = vi.hoisted(() => ({ enabled: false }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: () => mockUpload.enabled,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))

describe('Sidebar', () => {
  beforeEach(() => {
    mockPermissions.role = 'ADMIN'
    mockPermissions.canAccessManagement = true
    mockPermissions.canViewSettings = true
    mockUpload.enabled = false
  })

  it('hides Upload Report when the manual_upload flag is off, shows it when on', () => {
    // Render within a Testing route so that group is expanded (children render).
    const { rerender } = render(
      <MemoryRouter initialEntries={['/runs']}><Sidebar /></MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /Coverage/ })).toBeInTheDocument()  // group is open
    expect(screen.queryByRole('link', { name: /Upload Report/ })).not.toBeInTheDocument()

    mockUpload.enabled = true
    rerender(<MemoryRouter initialEntries={['/runs']}><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: /Upload Report/ })).toBeInTheDocument()
  })

  it('renders branding and top-level group links', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    // Group headers are always visible
    expect(screen.getByRole('link', { name: /Dashboard/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Testing/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /AI Reports/ })).toBeInTheDocument()
  })

  it('shows Management group for admin/QA Lead users', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: /Management/ })).toBeInTheDocument()
  })

  it('shows Settings link for admin/QA Lead users', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  })

  it('hides Management group for VIEWER role', () => {
    mockPermissions.canAccessManagement = false

    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: /Management/ })).not.toBeInTheDocument()
  })

  it('hides Settings link for non-management roles', () => {
    mockPermissions.canAccessManagement = false

    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument()
  })
})
