/**
 * R1-5 (Wave 2.6 fix round): the ring's number in presentation mode.
 *
 * Every metric value on a page reaches a room size (40 px) in presentation
 * mode through the `--text-stat-*` tokens; the ring's number is drawn by the
 * ring itself (`text-xl`, measured to fit a fixed ring), so the gate's risk
 * score stayed at 20 px in a 120 px ring. `RingGauge` now reads the mode and
 * grows the ring and both texts by one room factor (x2), and measures the
 * text at the size it DRAWS — measuring at 20 while drawing at 40 would let a
 * wide value overrun the hole.
 *
 * Desk mode is pinned byte for byte by `RingGauge.defaultRender.test.tsx`
 * (which runs with the mode off); this file adds the explicit desk check and
 * the presentation ones.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usePresentationStore } from '@/store/presentationStore'
import PassRateGauge from './PassRateGauge'
import RingGauge from './RingGauge'
import { ringTextWidth } from './gaugeBar.model'

// jsdom lays nothing out: the container takes the gauge's own box.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 120, height: 120 })
        : null,
  }
})

// A font whose every character is `em` x the font size wide: the measurement
// depends on the size it is asked about, as a real one does. `null` = no canvas.
const measured = vi.hoisted(() => ({ em: null as number | null }))
vi.mock('./textMeasure', () => ({
  measureTextWidth: (text: string, font: string) => {
    if (measured.em === null) return null
    const px = Number.parseFloat(/([\d.]+)px/.exec(font)?.[1] ?? 'NaN')
    return text.length * measured.em * px
  },
}))

/** jsdom gives every node a zero box; the fit only runs on a laid-out span. */
function layOut() {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 50 } as DOMRect)
}

afterEach(() => {
  measured.em = null
  usePresentationStore.setState({ enabled: false })
  vi.restoreAllMocks()
})

const box = (container: HTMLElement) => (container.firstElementChild as HTMLElement).style
const px = (node: HTMLElement) => node.style.fontSize

describe('RingGauge in presentation mode (R1-5)', () => {
  it('desk mode: the 120 px ring and the classes\' own sizes, exactly as before', () => {
    layOut()
    measured.em = 0.5 // "37" fits easily: no inline size at all
    const { container, getByText } = render(<RingGauge value={37} caption="Risk Score" format={String} animate={false} />)
    expect(box(container).width).toBe('120px')
    expect(box(container).height).toBe('120px')
    expect(px(getByText('37'))).toBe('')
    expect(px(getByText('Risk Score'))).toBe('')
  })

  it('presentation: the ring and both texts grow x2 — 240 px, 40 px value, 20 px caption', () => {
    usePresentationStore.setState({ enabled: true })
    layOut()
    measured.em = 0.5
    const { container, getByText, getByRole } = render(
      <RingGauge value={37} caption="Risk Score" format={String} animate={false} />,
    )
    expect(box(container).width).toBe('240px')
    expect(box(container).height).toBe('240px')
    expect(px(getByText('37'))).toBe('40px')
    expect(px(getByText('Risk Score'))).toBe('20px')
    // Still the same named meter.
    expect(getByRole('meter', { name: 'Risk Score' })).toHaveAttribute('aria-valuetext', '37')
  })

  it('presentation: a wide value is fitted at the size it is DRAWN, inside the larger hole', () => {
    usePresentationStore.setState({ enabled: true })
    layOut()
    // "100.0%" at 40 px is 6 x 0.8 x 40 = 192 px: wider than the 240 px ring's
    // 152.8 px hole. (At 20 px it would be 96 px and "fit" — the wrong measure.)
    measured.em = 0.8
    const hole = ringTextWidth(240)
    expect(hole).toBeCloseTo(152.8, 1)
    const { getByText } = render(<RingGauge value={100} animate={false} />)
    const drawn = Number.parseFloat(px(getByText('100.0%')))
    expect(drawn * 0.8 * 6).toBeLessThanOrEqual(hole)
    // Shrunk only as far as it must: still far above the desk size.
    expect(drawn).toBeGreaterThan(30)
    expect(drawn).toBeLessThan(40)
  })

  it('PassRateGauge (legacy, pinned bytes) does not take part', () => {
    usePresentationStore.setState({ enabled: true })
    layOut()
    measured.em = 0.5
    const { container, getByText } = render(<PassRateGauge value={96.4} />)
    expect(box(container).width).toBe('120px')
    expect(px(getByText('96.4%'))).toBe('')
  })
})
