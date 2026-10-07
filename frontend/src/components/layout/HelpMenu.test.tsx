import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useHelpStore } from '@/store/helpStore'
import HelpMenu from './HelpMenu'

vi.mock('./AppVersionBadge', () => ({ default: () => <div data-stub="app-version">v9.9.9</div> }))

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <HelpMenu />
    </MemoryRouter>,
  )
}

afterEach(() => {
  act(() => useHelpStore.getState().close())
})

describe('HelpMenu (UX redesign P1)', () => {
  it('"Help for this page" opens the topic mapped to the current route', () => {
    renderAt('/defects')
    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Help for this page/ }))
    expect(useHelpStore.getState()).toMatchObject({ topic: 'failure-analysis', anchor: 'promoting-to-a-defect' })
    expect(screen.queryByRole('menuitem', { name: /Help for this page/ })).toBeNull()
  })

  it('lists the guides that left the sidebar, and the build line', () => {
    renderAt('/runs')
    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    const hrefs = screen.getAllByRole('menuitem').map((el) => el.getAttribute('href')).filter(Boolean)
    expect(hrefs).toEqual(['/getting-started', '/docs', '/docs/charts', '/docs/troubleshooting'])
    expect(screen.getByText('v9.9.9')).toBeInTheDocument()
  })
})
