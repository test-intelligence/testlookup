/**
 * FeatureFlagsPage — ADMIN flag management (Settings › System).
 *
 * No coverage before UX redesign P5. What P5 changed is the header: compact,
 * with the route's help topic, "New Flag" its one primary action. The page is a
 * table, so its layout did not move; the tests also pin the table rows and the
 * create form (already two fields per row) opening from that action.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FeatureFlagsPage from './FeatureFlagsPage'
import type { FeatureFlag } from '@/services/featureFlagService'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const FLAG: FeatureFlag = {
  id: 'flag-1',
  key: 'manual_upload',
  description: 'Upload report files by hand',
  enabled_global: true,
  enabled_projects: null,
  enabled_roles: null,
  rollout_percent: 100,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  updated_by_user_id: null,
}

const permissions = { isAdmin: true }
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))

const flagsEnabled: unknown[] = []
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureFlags: (enabled?: boolean) => {
    flagsEnabled.push(enabled)
    return { flags: [FLAG], isLoading: false, isError: false, refresh: vi.fn() }
  },
}))

vi.mock('@/services/featureFlagService', () => ({
  featureFlagService: { update: vi.fn(), remove: vi.fn(), create: vi.fn() },
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  permissions.isAdmin = true
  flagsEnabled.length = 0
})

describe('FeatureFlagsPage', () => {
  it('has the compact header with the route help topic; New Flag is its one action', () => {
    render(<FeatureFlagsPage />)
    expectTemplateHeader('Feature Flags', '/settings/feature-flags')
    expect(screen.getByRole('button', { name: /new flag/i }).closest('[data-page-header]')).not.toBeNull()
  })

  it('each flag\'s delete bin is named after the flag, with a tooltip (browser E2E pass: it had no name)', () => {
    render(<FeatureFlagsPage />)
    const del = screen.getByRole('button', { name: 'Delete flag manual_upload' })
    expect(del).toHaveAttribute('title', 'Delete flag manual_upload')
  })

  it('lists the flags in the table and opens the create form from the header action', () => {
    render(<FeatureFlagsPage />)
    expect(screen.getByRole('table')).toHaveTextContent('manual_upload')
    expect(screen.getByRole('button', { name: 'Toggle manual_upload' })).toHaveTextContent('ON')

    fireEvent.click(screen.getByRole('button', { name: /new flag/i }))
    expect(screen.getByText('New feature flag')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('cypress_ingest')).toBeInTheDocument()
  })

  it('a non-admin: the header and the admin-required state, no action, and the ADMIN-only list never asked for', () => {
    permissions.isAdmin = false
    render(<FeatureFlagsPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Feature Flags' })).toBeInTheDocument()
    expect(screen.getByText('Admin access required')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /new flag/i })).toBeNull()
    // Asked, it answered 403 and toasted "Requires at least ADMIN role" over
    // the page's own message (browser E2E pass).
    expect(flagsEnabled).toEqual([false])
  })
})
