/**
 * Regression: the per-run "previous project" tracker in useProjectChange must
 * keep working after the ref was renamed `previousProjectId` → `previousProjectIdRef`
 * to satisfy the (now error-level) react-hooks/immutability rule. The rename is
 * cosmetic only — the ref still has to:
 *   • not fire on first render (no spurious redirect/reset on mount)
 *   • fire exactly once when activeProjectId changes while enabled
 *   • silently absorb changes while disabled (so re-enabling doesn't replay them)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useProjectChangeReset } from './useProjectChange'

let activeProjectId: string | null = 'p1'

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  useProjectStore: (selector: (s: { activeProjectId: string | null }) => unknown) =>
    selector({ activeProjectId }),
}))

beforeEach(() => {
  activeProjectId = 'p1'
})

describe('useProjectChangeReset (previousProjectIdRef regression)', () => {
  it('does not fire onChange on first render', () => {
    const onChange = vi.fn()
    renderHook(() => useProjectChangeReset(onChange))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('fires onChange once when the active project changes while enabled', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(() => useProjectChangeReset(onChange))

    activeProjectId = 'p2'
    rerender()

    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('does not fire while disabled, and does not replay the missed change on re-enable', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(
      ({ enabled }) => useProjectChangeReset(onChange, enabled),
      { initialProps: { enabled: false } },
    )

    // Project changes while disabled — the ref tracks it silently.
    activeProjectId = 'p2'
    rerender({ enabled: false })
    expect(onChange).not.toHaveBeenCalled()

    // Re-enabling must NOT replay the missed change (prev already advanced to p2).
    rerender({ enabled: true })
    expect(onChange).not.toHaveBeenCalled()

    // A fresh change while enabled still fires.
    activeProjectId = 'p3'
    rerender({ enabled: true })
    expect(onChange).toHaveBeenCalledTimes(1)
  })
})
