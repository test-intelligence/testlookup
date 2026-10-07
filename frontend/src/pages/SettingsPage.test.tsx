/**
 * UX redesign P5: the settings index is the sub-nav's groups as compact lists
 * (one line per page: its name and what it is for), not 22 cards.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'
import { SETTINGS_GROUPS } from '@/components/layout/settingsNav'

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ canAccessManagement: true }) }))

describe('SettingsPage (the /settings index)', () => {
  it('lists every group of the sub-nav, each page on one line with what it is for', () => {
    const { container } = render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )
    const sections = screen.getAllByRole('region')
    expect(sections.map((s) => within(s).getByRole('heading', { level: 2 }).textContent)).toEqual(
      SETTINGS_GROUPS.map((g) => g.label),
    )
    const integrations = screen.getByRole('region', { name: 'Integrations' })
    const github = within(integrations).getByRole('link', { name: /^GitHub/ })
    expect(github).toHaveAttribute('href', '/settings/github')
    expect(github).toHaveTextContent('Repository, checks and pull-request comments')
    // Members & access is the Users page's tab.
    expect(within(screen.getByRole('region', { name: 'Project' })).getByRole('link', { name: /^Members & access/ })).toHaveAttribute(
      'href',
      '/users?tab=project-members',
    )
    // No card grid: one list per group.
    expect(container.querySelectorAll('[data-settings-index] section ul')).toHaveLength(SETTINGS_GROUPS.length)
  })
})
