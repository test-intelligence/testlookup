/**
 * The trailing TopBar controls must sit at the right edge of the header.
 *
 * The header is `flex items-center gap-4` and the search box is
 * `flex-1 max-w-md`. Once the search box hits its max width it stops growing,
 * so on a wide screen the trailing controls used to bunch up immediately after
 * it, mid-header — leaving the right side empty, and anchoring their panels
 * over the middle of the page instead of against the edge.
 *
 * `ml-auto` on the first of the trailing siblings absorbs the free space and
 * pushes that item *and every sibling after it* to the right edge. Since the
 * UX redesign P1 the trailing group is Help, the bell, the account menu.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useNotifications', () => ({
  useUnreadCount: () => ({ count: 0 }),
  useNotificationHistory: () => ({ logs: [], isLoading: false }),
  invalidateNotifications: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: false }),
}))

import TopBar from './TopBar'

function renderTopBar() {
  return render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>,
  )
}

describe('TopBar trailing controls', () => {
  it('pins the trailing group to the right edge', () => {
    const { container } = renderTopBar()
    const header = container.querySelector('header')
    if (!header) throw new Error('TopBar rendered no <header>')

    const pushed = header.querySelectorAll(':scope > .ml-auto')
    expect(pushed.length).toBe(1)
  })

  it('Help absorbs the free space, then the bell, then the account menu', () => {
    const { container } = renderTopBar()
    const header = container.querySelector('header')
    if (!header) throw new Error('TopBar rendered no <header>')
    const children = Array.from(header.children)
    const indexOf = (el: Element) => children.findIndex((child) => child.contains(el))

    const pushIndex = children.findIndex((el) => el.classList.contains('ml-auto'))
    const help = indexOf(screen.getByRole('button', { name: 'Help' }))
    const bell = indexOf(screen.getByRole('button', { name: /^Notifications/ }))
    const account = indexOf(screen.getByRole('button', { name: 'Account menu' }))

    // Only siblings that come *after* the auto-margin get pushed right with it.
    expect(help).toBe(pushIndex)
    expect(bell).toBeGreaterThan(help)
    expect(account).toBeGreaterThan(bell)
  })
})
