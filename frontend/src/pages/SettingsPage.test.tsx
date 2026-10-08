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
    // One column (P5 baselines: two columns beside the sub-nav cut every
    // description to ten characters at 1280 px).
    expect((container.querySelector('[data-settings-index]') as HTMLElement).className).not.toMatch(/grid-cols-2/)
    // No card grid: one list per group.
    expect(container.querySelectorAll('[data-settings-index] section ul')).toHaveLength(SETTINGS_GROUPS.length)
  })

  it('marks the grouped lists as the page’s one primary content (P6 fold budget)', () => {
    const { container } = render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    expect(primaries[0]).toHaveAttribute('data-settings-index')
    // Below the header, never around it.
    const header = container.querySelector('[data-page-header]') as Element
    expect(header.compareDocumentPosition(primaries[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(primaries[0].contains(header)).toBe(false)
  })
})
