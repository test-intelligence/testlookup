/**
 * UX redesign P5: every settings and admin page sits in the settings layout —
 * a grouped sub-nav beside the page — role-filtered, with the current page
 * marked, and "All settings" (the index) for those who can open it: the way
 * back BUG-009 guaranteed, now that the breadcrumb is gone.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsLayout from './SettingsLayout'

const perms = vi.hoisted(() => ({ value: { canAccessManagement: true, isAdmin: false, canGenerateApiKeys: true } }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => perms.value }))

function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <SettingsLayout>
        <div data-testid="page">Page</div>
      </SettingsLayout>
    </MemoryRouter>,
  )
  return screen.getByRole('navigation', { name: 'Settings' })
}

const current = (nav: HTMLElement) => Array.from(nav.querySelectorAll('[aria-current="page"]'), (a) => a.textContent)

describe('SettingsLayout', () => {
  beforeEach(() => {
    perms.value = { canAccessManagement: true, isAdmin: false, canGenerateApiKeys: true }
  })

  it('a QA lead or admin: All settings, then the seven groups, the page beside them', () => {
    const nav = renderAt('/settings/github')
    expect(within(nav).getByRole('link', { name: 'All settings' })).toHaveAttribute('href', '/settings')
    expect(Array.from(nav.querySelectorAll('[data-settings-group]'), (g) => g.getAttribute('data-settings-group'))).toEqual([
      'account', 'project', 'release-governance', 'integrations', 'ai', 'security', 'system',
    ])
    expect(current(nav)).toEqual(['GitHub'])
    expect(screen.getByTestId('page')).toBeInTheDocument()
  })

  it('a viewer: their own pages and the AI pipeline pages; no index link, nothing that would redirect them', () => {
    perms.value = { canAccessManagement: false, isAdmin: false, canGenerateApiKeys: false }
    const nav = renderAt('/settings/profile')
    expect(within(nav).queryByRole('link', { name: 'All settings' })).toBeNull()
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Profile', 'My notifications', 'Workflow editor', 'Pipeline runs',
    ])
    expect(current(nav)).toEqual(['Profile'])
  })

  it('SSO and Feature flags for an admin only: a QA lead got 403s there (browser E2E pass)', () => {
    const links = () => within(renderAt('/settings/profile')).getAllByRole('link').map((a) => a.textContent)
    const lead = links()
    expect(lead).toContain('MFA & lockout')
    expect(lead).not.toContain('SSO & identity')
    expect(lead).not.toContain('Feature flags')
    cleanup()
    perms.value = { ...perms.value, isAdmin: true }
    expect(links()).toEqual(expect.arrayContaining(['SSO & identity', 'Feature flags']))
  })

  it('the sub-nav scrolls on its own: seven groups are taller than the window', () => {
    const nav = renderAt('/settings/profile')
    expect(nav.className.split(/\s+/)).toEqual(expect.arrayContaining(['lg:sticky', 'lg:overflow-y-auto']))
  })

  it('one page, two items told apart by ?tab=: Users and Members & access', () => {
    expect(current(renderAt('/users?tab=project-members'))).toEqual(['Members & access'])
  })

  it('on the index, All settings is the current page', () => {
    expect(current(renderAt('/settings'))).toEqual(['All settings'])
  })
})
