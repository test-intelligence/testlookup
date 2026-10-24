/**
 * W11 GATE-08a: `/release-gate/:runId` follows the top bar's release. The hook
 * acts on a CHANGE of the effective release only — never on mount, never for a
 * project switch (the project redirect's) — and records without acting while
 * disabled.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useReleaseChangeRedirect } from './useReleaseChangeRedirect'

const scope = vi.hoisted(() => ({ projectId: 'proj-1' as string | null, releaseId: null as string | null }))
const navigate = vi.hoisted(() => vi.fn())

vi.mock('react-router-dom', async (importOriginal) => ({
  ...await importOriginal<typeof import('react-router-dom')>(),
  useNavigate: () => navigate,
}))
vi.mock('./useReleaseScope', () => ({ useReleaseScope: () => scope.releaseId }))
vi.mock('./useProjectScopedSWR', () => ({ useActiveProjectId: () => scope.projectId }))

beforeEach(() => {
  scope.projectId = 'proj-1'
  scope.releaseId = 'rel-11'
  navigate.mockReset()
})

function mount(enabled = true) {
  return renderHook(({ on }: { on: boolean }) => useReleaseChangeRedirect('/release-gate', on), {
    initialProps: { on: enabled },
  })
}

describe('useReleaseChangeRedirect', () => {
  it('does not navigate on mount', () => {
    const { rerender } = mount()
    rerender({ on: true })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('a release change navigates to the target, replacing the entry', () => {
    const { rerender } = mount()
    scope.releaseId = 'rel-10'
    rerender({ on: true })
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('/release-gate', { replace: true })
  })

  it('choosing All releases (the release cleared on the same project) navigates too', () => {
    const { rerender } = mount()
    scope.releaseId = null
    rerender({ on: true })
    expect(navigate).toHaveBeenCalledWith('/release-gate', { replace: true })
  })

  it('while disabled a change is only recorded: enabling later does not replay it', () => {
    const { rerender } = mount(false)
    scope.releaseId = 'rel-10'
    rerender({ on: false })
    expect(navigate).not.toHaveBeenCalled()
    rerender({ on: true })
    expect(navigate).not.toHaveBeenCalled()
    scope.releaseId = 'rel-12'
    rerender({ on: true })
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('a project switch (the scope falls to null with it) does not navigate a second time', () => {
    const { rerender } = mount()
    scope.projectId = 'proj-2'
    scope.releaseId = null
    rerender({ on: true })
    // The picker then clears its stale release: still null, still nothing.
    rerender({ on: true })
    expect(navigate).not.toHaveBeenCalled()
    // A release picked on the new project is a change again.
    scope.releaseId = 'rel-20'
    rerender({ on: true })
    expect(navigate).toHaveBeenCalledTimes(1)
  })
})
