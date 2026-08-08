/**
 * The trailing TopBar controls must sit at the right edge of the header.
 *
 * The header is `flex items-center gap-4` and the search box is
 * `flex-1 max-w-md`. Once the search box hits its max width it stops growing,
 * so on a wide screen the notification bell / theme picker / profile menu used
 * to bunch up immediately after it, mid-header — leaving the right side empty.
 *
 * That is what made the theme picker look like it overlapped page content: its
 * panel is `absolute right-0 w-64`, so anchored mid-header it dropped a 16rem
 * card over the middle of the page instead of hugging the edge the way the
 * other menus do.
 *
 * `ml-auto` on the first of the trailing siblings absorbs the free space and
 * pushes that item *and every sibling after it* to the right edge.
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
    expect(pushed.length).toBeGreaterThan(0)
  })

  it('puts the theme picker after the element that absorbs the free space', () => {
    const { container } = renderTopBar()
    const header = container.querySelector('header')
    if (!header) throw new Error('TopBar rendered no <header>')
    const children = Array.from(header.children)

    const pushIndex = children.findIndex((el) => el.classList.contains('ml-auto'))
    const themeButton = screen.getByTitle('Change color theme')
    const themeIndex = children.findIndex((el) => el.contains(themeButton))

    expect(pushIndex).toBeGreaterThanOrEqual(0)
    expect(themeIndex).toBeGreaterThanOrEqual(0)
    // Order is what makes ml-auto reach the theme picker: only siblings that
    // come *after* the auto-margin get pushed right with it.
    expect(themeIndex).toBeGreaterThan(pushIndex)
  })

  it('keeps the theme panel right-anchored so it opens toward the edge', () => {
    renderTopBar()
    const themeButton = screen.getByTitle('Change color theme')
    const panelParent = themeButton.parentElement
    if (!panelParent) throw new Error('theme button has no wrapper element')
    // The picker positions its dropdown with `absolute right-0`; the wrapper
    // must stay `relative` or the panel would anchor to the page instead.
    expect(panelParent.className).toContain('relative')
  })
})
