import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it } from 'vitest'
import {
  ChartFrameContext,
  useChartFrameHeight,
  useChartFullscreen,
  useChartPortalContainer,
  type ChartFrameContextValue,
} from './chartFrameContext'

const inside = (value: ChartFrameContextValue) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return <ChartFrameContext.Provider value={value}>{children}</ChartFrameContext.Provider>
  }

describe('chartFrameContext', () => {
  it('outside any frame: the height passes through, no portal container, not full screen', () => {
    expect(renderHook(() => useChartFrameHeight(240)).result.current).toBe(240)
    expect(renderHook(() => useChartPortalContainer()).result.current).toBeNull()
    expect(renderHook(() => useChartFullscreen()).result.current).toBe(false)
  })

  it('in a frame that is NOT full screen, a stale body height is ignored', () => {
    const wrapper = inside({ fullscreen: false, bodyHeight: 900, portalContainer: null })
    expect(renderHook(() => useChartFrameHeight(240), { wrapper }).result.current).toBe(240)
  })

  it('in a full-screen frame: the body height and the frame as the portal container', () => {
    const element = document.createElement('div')
    const wrapper = inside({ fullscreen: true, bodyHeight: 900, portalContainer: element })
    expect(renderHook(() => useChartFrameHeight(240), { wrapper }).result.current).toBe(900)
    expect(renderHook(() => useChartPortalContainer(), { wrapper }).result.current).toBe(element)
    expect(renderHook(() => useChartFullscreen(), { wrapper }).result.current).toBe(true)
  })
})
