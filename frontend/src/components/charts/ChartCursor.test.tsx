/**
 * `useChartCursor`, fix round A (M2): the readout a sighted keyboard user
 * reads WRAPS. It was one `truncate`d line, so a long day (a 382-character
 * anomaly explanation) showed its first third and an ellipsis.
 */
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { cursorPoint, useChartCursor } from './ChartCursor'
import { buildTooltipNode, sampleRow, tipContent, tooltipText } from './tooltip'
import { readTooltip } from './tooltipTestUtils'

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

describe('VIZ-601: the readout and the speech come from the tooltip content', () => {
  const content = tipContent('checkout', [{ label: 'Failures', value: '41' }, sampleRow(50)])

  function ContentProbe() {
    const cursor = useChartCursor({ title: 'T', chartType: 'Bar chart', points: [cursorPoint('a', content)] })
    return (
      <div data-probe="" {...cursor.surfaceProps}>
        {cursor.readout}
      </div>
    )
  }

  it('speaks tooltipText(content) and draws the SAME content with the tooltip markup, wrapping, in the flow', () => {
    const { container } = render(
      <ChartAnnouncerProvider>
        <ContentProbe />
      </ChartAnnouncerProvider>,
    )
    const surface = container.querySelector('[data-probe]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(container.querySelector('[data-chart-announcer="assertive"]')?.textContent).toBe(`T: ${tooltipText(content)}`)
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    expect(readTooltip(readout)).toEqual(readTooltip(buildTooltipNode(content)))
    expect(readout.getAttribute('aria-hidden')).toBe('true')
    expect(readout.className).toMatch(/\bwhitespace-normal\b/)
    expect(readout.className).not.toMatch(/\babsolute\b/)
    // The readout is NOT the hover tooltip: specs count `[data-chart-tooltip]` as the pointer's.
    expect(readout.querySelector('[data-chart-tooltip]')).toBeNull()
  })

  it('a pointer sweeping over the chart re-renders nothing; after Escape, one move asks for the tooltip back', () => {
    // Called once per render with the tooltip key that render produced.
    const rendered = vi.fn<(tipKey: string) => void>()
    const renders = () => rendered.mock.calls.length
    const tipKey = () => rendered.mock.calls[rendered.mock.calls.length - 1][0]
    function Counted() {
      const cursor = useChartCursor({ title: 'T', chartType: 'Bar chart', points: [cursorPoint('a', content)] })
      rendered(cursor.tipKey)
      return <div data-probe="" {...cursor.surfaceProps} />
    }
    const { container } = render(
      <ChartAnnouncerProvider>
        <Counted />
      </ChartAnnouncerProvider>,
    )
    const surface = container.querySelector('[data-probe]') as HTMLElement
    const before = renders()
    for (let i = 0; i < 50; i++) fireEvent.pointerMove(surface, { clientX: i, clientY: i })
    expect(renders() - before).toBe(0)

    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(tipKey()).toBe('dismissed')
    fireEvent.pointerMove(surface)
    expect(tipKey()).toBe('live')
  })
})
