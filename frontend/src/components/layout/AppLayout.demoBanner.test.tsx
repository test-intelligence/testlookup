/**
 * The public demo's banner in the app shell: shown only for a session opened
 * by the demo's one-click sign-in (`authStore.demoSession`), lazy-loaded, and
 * never fetched or shown for an ordinary session.
 *
 * Its own file, not AppLayout.test.tsx: that suite's help-drawer test imports
 * a large module graph against a 10 s limit, and an extra lazy render in the
 * same worker pushed it past the limit on a loaded machine.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppLayout from './AppLayout'
import { useAuthStore } from '@/store/authStore'

vi.mock('./TopBar', () => ({ default: () => <div>TopBar Stub</div> }))
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

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/overview']}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/overview" element={<div>Overview Content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppLayout demo banner', () => {
  afterEach(() => {
    // Unmount first, so resetting the store re-renders nothing.
    cleanup()
    useAuthStore.setState({ demoSession: false })
    localStorage.removeItem('auth-storage')
  })

  it('is absent for an ordinary session', async () => {
    renderShell()
    expect(await screen.findByText('Overview Content')).toBeInTheDocument()
    expect(screen.queryByTestId('demo-banner')).not.toBeInTheDocument()
  })

  it('loads for a session opened by demo sign-in', async () => {
    useAuthStore.setState({ demoSession: true })
    renderShell()
    expect(await screen.findByTestId('demo-banner')).toHaveTextContent(/live demo/i)
  })
})
