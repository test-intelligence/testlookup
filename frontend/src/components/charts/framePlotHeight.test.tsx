/**
 * VIZ-601 × VIZ-608: a chart's plot grows with its frame in full screen,
 * keeping back room for its own notes, and is untouched outside it.
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChartFrameContext, type ChartFrameContextValue } from './chartFrameContext'
import { useFramePlotHeight } from './framePlotHeight'

function Probe({ height, reserve }: { height: number; reserve?: number }) {
  return <output data-height={useFramePlotHeight(height, reserve)} />
}

const heightIn = (value: ChartFrameContextValue | null, height: number, reserve?: number) => {
  const probe = <Probe height={height} reserve={reserve} />
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
