/**
 * `useChartCursor`, fix round A (M2): the readout a sighted keyboard user
 * reads WRAPS. It was one `truncate`d line, so a long day (a 382-character
 * anomaly explanation) showed its first third and an ellipsis.
 */
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { useChartCursor } from './ChartCursor'

const LONG = `2026-03-23 (UTC), ${'a long explanation of why this day was flagged, '.repeat(8)}end`

function Probe() {
  const cursor = useChartCursor({ title: 'T', chartType: 'Line chart', points: [{ key: 'a', text: LONG }] })
  return (
    <div data-probe="" {...cursor.surfaceProps}>
      {cursor.readout}
    </div>
  )
}

describe('the cursor readout', () => {
  it('wraps instead of truncating, so its whole text is on screen', () => {
    const { container } = render(
      <ChartAnnouncerProvider>
        <Probe />
      </ChartAnnouncerProvider>,
    )
    const surface = container.querySelector('[data-probe]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    expect(readout.textContent).toBe(LONG)
    expect(readout.className).not.toMatch(/\btruncate\b/)
    expect(readout.className).not.toMatch(/\bwhitespace-nowrap\b/)
    expect(readout.className).toMatch(/\bwhitespace-normal\b/)
    expect(readout.className).toMatch(/\bbreak-words\b/)
    // Below the plot, in the flow — never laid over the data it describes.
    expect(readout.className).not.toMatch(/\babsolute\b/)
  })
})
