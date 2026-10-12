import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from './AppLayout'
import { openHelp, useHelpStore } from '@/store/helpStore'

vi.mock('./TopBar', () => ({
  default: () => <div>TopBar Stub</div>,
}))
vi.mock('@/components/guide/MermaidDiagram', () => ({ default: () => null }))
// The shell's own data hooks, so no request leaves the test (src/test/networkGuard.ts).
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useAIConfig')>(),
  useAIConfig: () => ({ data: undefined }),
}))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useFeatureFlags')>(),
  useFeatureEnabled: () => false,
  useFeatureFlagStatus: () => false,
}))
vi.mock('@/hooks/useLiveRunningCount', () => ({ NAV_LIVE_POLL_MS: 30_000, useLiveRunningCount: () => 0 }))
vi.mock('@/hooks/useSystemHealth', () => ({
  useSystemHealth: () => ({ data: null, unavailable: [], criticalUnavailable: [], isDegraded: false, isUnreachable: false }),
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
    expect(screen.getByRole('link', { name: 'Home' })).toBeInTheDocument()
    expect(screen.getByText('Overview Content')).toBeInTheDocument()
  })

  it('moves keyboard focus past navigation to the main landmark', () => {
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/overview" element={<div>Overview Content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    const skipLink = screen.getByRole('link', { name: 'Skip to main content' })
    const main = screen.getByRole('main')
    skipLink.focus()
    fireEvent.click(skipLink)

    expect(skipLink).toHaveAttribute('href', '#main-content')
    expect(main).toHaveAttribute('id', 'main-content')
    expect(main).toHaveFocus()
  })

  // UX redesign P1: the drawer is loaded the first time help opens (the eager
  // bundle budget); closed, it is not in the tree at all.
  it('loads and shows the help drawer when help opens, and removes it when it closes', async () => {
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/overview" element={<div>Overview Content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    expect(document.querySelector('[data-help-topic]')).toBeNull()
    act(() => openHelp('flaky'))
    // The first open imports the drawer's whole module graph (react-markdown,
    // Mermaid, every docs page): 1 s, findBy's default, timed out with five
    // agents' suites running beside it. Loading, not behaviour, is what is slow.
    expect(await screen.findByRole('button', { name: 'Close help' }, { timeout: 10_000 })).toBeInTheDocument()
    expect(document.querySelector('[data-help-topic="flaky"]')).not.toBeNull()
    act(() => useHelpStore.getState().close())
    expect(document.querySelector('[data-help-topic]')).toBeNull()
  }, 20_000)
})
