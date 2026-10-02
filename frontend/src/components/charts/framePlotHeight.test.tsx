/**
 * VIZ-601 × VIZ-608: a chart's plot grows with its frame in full screen,
 * keeping back room for its own notes, and is untouched outside it.
 */
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { usePresentationStore } from '@/store/presentationStore'
import { ChartFrameContext, type ChartFrameContextValue } from './chartFrameContext'
import {
  PRESENTATION_MODE_SCALE,
  PRESENTATION_SCALE,
  useFramePlotHeight,
  useFramePlotLayoutHeight,
  usePresentationScale,
} from './framePlotHeight'

function Probe({ height, reserve }: { height: number; reserve?: number }) {
  return <output data-height={useFramePlotHeight(height, reserve)} />
}

function LayoutProbe({ height, reserve }: { height: number; reserve?: number }) {
  return <output data-height={useFramePlotLayoutHeight(height, reserve)} />
}

const heightIn = (value: ChartFrameContextValue | null, height: number, reserve?: number, layout = false) => {
  const probe = layout ? <LayoutProbe height={height} reserve={reserve} /> : <Probe height={height} reserve={reserve} />
  const { container } = render(value ? <ChartFrameContext.Provider value={value}>{probe}</ChartFrameContext.Provider> : probe)
  return Number(container.querySelector('output')?.getAttribute('data-height'))
}

describe('useFramePlotHeight', () => {
  it.each([
    ['outside any frame: the page height', null, 280, 100, 280],
    ['in a frame, not full screen: the page height', { fullscreen: false, bodyHeight: null, portalContainer: null }, 280, 100, 280],
    ['full screen: the body, less the notes', { fullscreen: true, bodyHeight: 900, portalContainer: null }, 280, 100, 800],
    ['full screen, no notes: the whole body', { fullscreen: true, bodyHeight: 900, portalContainer: null }, 280, 0, 900],
    ['full screen on a short screen: never less than the page asked for', { fullscreen: true, bodyHeight: 300, portalContainer: null }, 280, 100, 280],
  ] as const)('%s', (_what, value, height, reserve, expected) => {
    expect(heightIn(value, height, reserve)).toBe(expected)
  })
})

/**
 * PR 165 on Linux: at 640×480 the time series' full-screen drawing was laid
 * out at 280 / (15/11) = 205 px — smaller than on the page — so its wrapped
 * legend left the plot 65 px and the rate title ran out of the svg.
 */
describe('useFramePlotLayoutHeight — a full-screen drawing is never laid out smaller than the page', () => {
  it.each([
    ['outside any frame: the page height', null, 280, 100, 280],
    ['in a frame, not full screen: the page height', { fullscreen: false, bodyHeight: null, portalContainer: null }, 280, 100, 280],
    ['full screen: the body less the notes, scaled back down', { fullscreen: true, bodyHeight: 900, portalContainer: null }, 280, 100, Math.round(800 / PRESENTATION_SCALE)],
    ['full screen on a short screen: the page height, not 280 scaled down', { fullscreen: true, bodyHeight: 300, portalContainer: null }, 280, 100, 280],
    ['full screen where the body scaled down is just short of the page: the page height', { fullscreen: true, bodyHeight: 480, portalContainer: null }, 280, 112, 280],
  ] as const)('%s', (_what, value, height, reserve, expected) => {
    expect(heightIn(value, height, reserve, true)).toBe(expected)
  })
})

/**
 * VIZ-106 (PLAN 3.5, OD-11): full screen and presentation mode are ONE scale
 * seam. Each alone keeps its own size (15/11 and 16/11); together the larger
 * wins, so full screen inside presentation mode never shrinks the drawing.
 */
describe('usePresentationScale — the larger of full screen and presentation mode', () => {
  function ScaleProbe() {
    return <output data-scale={usePresentationScale()} />
  }
  const scaleIn = (fullscreen: boolean, presenting: boolean) => {
    usePresentationStore.setState({ enabled: presenting })
    const value: ChartFrameContextValue = { fullscreen, bodyHeight: fullscreen ? 900 : null, portalContainer: null }
    const { container, unmount } = render(
      <ChartFrameContext.Provider value={value}>
        <ScaleProbe />
      </ChartFrameContext.Provider>,
    )
    const scale = Number(container.querySelector('output')?.getAttribute('data-scale'))
    unmount()
    return scale
  }
  afterEach(() => usePresentationStore.setState({ enabled: false }))

  it.each([
    ['neither: the page drawing, unscaled', false, false, 1],
    ['full screen alone: 15/11, as before presentation mode existed', true, false, PRESENTATION_SCALE],
    ['presentation mode alone: 16/11 on the page', false, true, PRESENTATION_MODE_SCALE],
    ['both: the larger, 16/11', true, true, PRESENTATION_MODE_SCALE],
  ] as const)('%s', (_what, fullscreen, presenting, expected) => {
    expect(scaleIn(fullscreen, presenting)).toBeCloseTo(expected, 10)
  })

  it('16/11 turns the 11 px axis text into 16 px; 15/11 into 15 px', () => {
    expect(11 * PRESENTATION_MODE_SCALE).toBeCloseTo(16, 10)
    expect(11 * PRESENTATION_SCALE).toBeCloseTo(15, 10)
  })

  it('in presentation mode a drawing on the page is still LAID OUT at the page height', () => {
    usePresentationStore.setState({ enabled: true })
    expect(heightIn({ fullscreen: false, bodyHeight: null, portalContainer: null }, 280, 100, true)).toBe(280)
  })
})
