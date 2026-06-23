/**
 * Regression guard for the web-vitals v4 → v5 migration.
 *
 * v5 retired FID (First Input Delay) in favour of INP (Interaction to Next
 * Paint), removing the `onFID` export. This test verifies useWebVitals registers
 * exactly the v5 Core Web Vitals (CLS, LCP, FCP, TTFB, INP) and never references
 * the removed `onFID`, so a future bump/revert can't silently drop a metric.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const onCLS = vi.fn()
const onLCP = vi.fn()
const onFCP = vi.fn()
const onTTFB = vi.fn()
const onINP = vi.fn()

vi.mock('web-vitals', () => ({ onCLS, onLCP, onFCP, onTTFB, onINP }))
vi.mock('../utils/errorReporting', () => ({ reportWebVital: vi.fn() }))

describe('useWebVitals', () => {
  it('registers the v5 Core Web Vitals (no FID)', async () => {
    const { useWebVitals } = await import('./useWebVitals')
    renderHook(() => useWebVitals())

    // The hook dynamically imports web-vitals, so wait for the microtask.
    await waitFor(() => expect(onINP).toHaveBeenCalledTimes(1))

    for (const fn of [onCLS, onLCP, onFCP, onTTFB]) {
      expect(fn).toHaveBeenCalledTimes(1)
    }
  })
})
