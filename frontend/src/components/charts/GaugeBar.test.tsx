import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import GaugeBar from './GaugeBar'
import type { GaugeBands } from './gaugeBar.model'

// The measured-scale case swaps in a font; every other test runs as jsdom
// does in the app's own tests: no layout, so no measure.
const font = vi.hoisted(() => ({ charPx: null as number | null }))
vi.mock('./textMeasure', () => ({
  useTextMeasure: () => [
    () => {},
    font.charPx === null ? null : (text: string) => text.length * (font.charPx as number),
  ],
}))

const RISK: GaugeBands = { direction: 'lower-is-better', thresholds: [40, 70] }
const HEALTH_TICKS = [{ value: 0, label: 'Blocked' }, { value: 33, label: 'At risk' }, { value: 70, label: 'Healthy' }, { value: 100 }]

const q = (c: HTMLElement, sel: string) => c.querySelector(sel) as HTMLElement | null
const widthOf = (el: HTMLElement | null) => el?.style.width

afterEach(() => {
  font.charPx = null
  vi.restoreAllMocks()
})

describe('GaugeBar — not measured', () => {
  it('draws an empty track: no fill, no marker, no segments, no target', () => {
    const { container } = render(
      <GaugeBar
        value={null}
        label="Queue health"
        variant="marker"
        gradient="health"
        target={{ value: 70, label: 'target' }}
        segments={[{ value: 3, label: 'P0' }]}
      />,
    )
    expect(q(container, '[data-gauge-track]')).not.toBeNull()
    expect(q(container, '[data-gauge-fill]')).toBeNull()
    expect(q(container, '[data-gauge-marker]')).toBeNull()
    expect(q(container, '[data-gauge-segment]')).toBeNull()
    expect(q(container, '[data-gauge-target]')).toBeNull()
  })

  it('is announced as not measured, never as a meter reading 0', () => {
    render(<GaugeBar value={null} label="Composite risk score" />)
    expect(screen.queryByRole('meter')).toBeNull()
    const img = screen.getByRole('img', { name: 'Composite risk score: not measured' })
    expect(img).not.toHaveAttribute('aria-valuenow')
    expect(img).toHaveAttribute('data-measured', 'false')
  })

  it('the fill variant draws no fill for null (a 0-width fill would still say "measured")', () => {
    const { container } = render(<GaugeBar value={null} label="x" gradient="risk" />)
    expect(q(container, '[data-gauge-fill]')).toBeNull()
  })

  it('shows the no-value text beside an inline bar, not "0"', () => {
    render(<GaugeBar value={null} label="Pass rate" showValue size="sm" />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText(/\b0\b/)).toBeNull()
  })

  it('a named label keeps "not measured" in the accessible name', () => {
    render(<GaugeBar value={null} label="Spend" showLabel />)
    expect(screen.getByRole('img', { name: 'Spend: not measured' })).toBeInTheDocument()
  })
})

describe('GaugeBar — a reading', () => {
  it('is a named meter with its value, range and value text', () => {
    render(<GaugeBar value={72} label="Queue health" />)
    const meter = screen.getByRole('meter', { name: 'Queue health' })
    expect(meter).toHaveAttribute('aria-valuenow', '72')
    expect(meter).toHaveAttribute('aria-valuemin', '0')
    expect(meter).toHaveAttribute('aria-valuemax', '100')
    expect(meter).toHaveAttribute('aria-valuetext', '72 of 100')
  })

  it('takes the caller\'s value text and domain', () => {
    render(<GaugeBar value={3} domain={[0, 7]} label="Oldest open P0" valueText="3 days of 7" />)
    const meter = screen.getByRole('meter', { name: 'Oldest open P0' })
    expect(meter).toHaveAttribute('aria-valuemax', '7')
    expect(meter).toHaveAttribute('aria-valuetext', '3 days of 7')
  })

  it('a real 0 is a meter at 0, with an empty fill', () => {
    const { container } = render(<GaugeBar value={0} label="Pass rate" />)
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0')
    expect(q(container, '[data-gauge-fill]')).toBeNull()
  })

  it('fills to the value', () => {
    const { container } = render(<GaugeBar value={42} label="x" />)
    expect(widthOf(q(container, '[data-gauge-fill]'))).toBe('42%')
  })

  it('clamps at the top: the fill stops at the end, the text keeps the true value', () => {
    const { container } = render(<GaugeBar value={130} label="x" />)
    expect(widthOf(q(container, '[data-gauge-fill]'))).toBe('100%')
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '100')
    expect(meter).toHaveAttribute('aria-valuetext', '130 of 100')
  })

  it('clamps at the bottom: the marker sits on the start of the track', () => {
    const { container } = render(<GaugeBar value={-20} label="x" variant="marker" gradient="health" />)
    expect(q(container, '[data-gauge-marker]')?.style.left).toBe('0%')
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0')
  })

  it('the marker variant marks the value on a gradient track, with no fill', () => {
    const { container } = render(<GaugeBar value={64} label="x" variant="marker" gradient="health" />)
    expect(q(container, '[data-gauge-marker]')?.style.left).toBe('64%')
    expect(q(container, '[data-gauge-fill]')).toBeNull()
    expect(q(container, '[data-gauge-track]')?.style.background).toBe('var(--gradient-health)')
  })

  it('anchors a gradient fill to the TRACK, so its end colour is the value\'s own', () => {
    const { container } = render(<GaugeBar value={25} label="x" gradient="risk" />)
    const fill = q(container, '[data-gauge-fill]') as HTMLElement
    expect(fill.style.width).toBe('25%')
    expect(fill.style.backgroundImage).toBe('var(--gradient-risk)')
    // The gradient is 4x the fill: the fill shows the first quarter of it.
    expect(fill.style.backgroundSize).toBe('400% 100%')
  })
})

describe('GaugeBar — tone', () => {
  it('lower is better for risk: a low score is good, a high one bad', () => {
    const { container, rerender } = render(<GaugeBar value={20} label="Risk" tone={RISK} />)
    expect(screen.getByRole('meter')).toHaveAttribute('data-tone', 'good')
    expect(q(container, '[data-gauge-fill]')?.style.background).toBe('var(--status-passed)')
    rerender(<GaugeBar value={85} label="Risk" tone={RISK} />)
    expect(screen.getByRole('meter')).toHaveAttribute('data-tone', 'bad')
    expect(q(container, '[data-gauge-fill]')?.style.background).toBe('var(--status-failed)')
  })

  it('higher is better for a pass rate', () => {
    render(<GaugeBar value={85} label="Pass rate" tone={{ direction: 'higher-is-better', thresholds: [60, 80] }} />)
    expect(screen.getByRole('meter')).toHaveAttribute('data-tone', 'good')
  })

  it('a fixed tone colours the marker ring', () => {
    const { container } = render(<GaugeBar value={50} label="x" variant="marker" tone="warn" />)
    expect(q(container, '[data-gauge-marker]')?.style.border).toContain('var(--status-broken)')
  })

  it('not measured is neutral, whatever the bands say', () => {
    render(<GaugeBar value={null} label="Risk" tone={RISK} />)
    expect(screen.getByRole('img')).toHaveAttribute('data-tone', 'neutral')
  })

  it('a tinted track follows the tone', () => {
    const { container } = render(<GaugeBar value={50} label="x" tone="bad" track="tint" size="sm" />)
    expect(q(container, '[data-gauge-track]')?.style.background).toBe(
      'color-mix(in srgb, var(--status-failed) 18%, transparent)',
    )
  })
})

describe('GaugeBar — segments and target', () => {
  it('stacks segments end to end; their widths sum to the total', () => {
    const { container } = render(
      <GaugeBar
        value={10}
        domain={[0, 10]}
        label="Eligible failures"
        segments={[
          { value: 1, label: 'P0', tone: 'bad' },
          { value: 2, label: 'P1', tone: 'warn' },
          { value: 3, label: 'P2' },
          { value: 4, label: 'P3' },
        ]}
      />,
    )
    const segs = [...container.querySelectorAll<HTMLElement>('[data-gauge-segment]')]
    expect(segs.map((s) => [s.style.left, s.style.width])).toEqual([
      ['0%', '10%'],
      ['10%', '20%'],
      ['30%', '30%'],
      ['60%', '40%'],
    ])
    expect(segs.reduce((a, s) => a + Number.parseFloat(s.style.width), 0)).toBe(100)
    expect(segs[0].style.background).toBe('var(--status-failed)')
    expect(segs[2].style.background).toBe('var(--chart-series-3)')
    expect(segs[1]).toHaveAttribute('title', 'P1: 2')
    expect(q(container, '[data-gauge-fill]')).toBeNull()
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '10 of 10; P0 1, P1 2, P2 3, P3 4')
  })

  it('puts the target at its true position and names it', () => {
    const { container } = render(<GaugeBar value={55} label="Avg cluster confidence" target={{ value: 70, label: 'target' }} />)
    expect(q(container, '[data-gauge-target]')?.style.left).toBe('70%')
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '55 of 100; target 70')
  })
})

describe('GaugeBar — scale', () => {
  it('draws the ticks as real text and notches the interior ones at their true values', () => {
    const { container } = render(<GaugeBar value={50} label="Queue health" variant="marker" gradient="health" ticks={HEALTH_TICKS} />)
    for (const text of ['Blocked · 0', 'At risk · 33', 'Healthy · 70', '100']) expect(screen.getByText(text)).toBeInTheDocument()
    const notches = [...container.querySelectorAll<HTMLElement>('[data-gauge-notch]')]
    expect(notches.map((n) => n.style.left)).toEqual(['33%', '70%'])
    expect(q(container, '[data-gauge-ticks]')).toHaveAttribute('data-gauge-ticks', 'full')
  })

  it('drops the band names before two labels collide in a wider font', () => {
    font.charPx = 6
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 200 } as DOMRect)
    render(<GaugeBar value={50} label="Queue health" ticks={HEALTH_TICKS} />)
    expect(document.querySelector('[data-gauge-ticks]')).toHaveAttribute('data-gauge-ticks', 'values')
    expect(screen.getByText('33')).toBeInTheDocument()
    expect(screen.queryByText('At risk · 33')).toBeNull()
  })

  it('keeps the band names where the measured font fits them', () => {
    font.charPx = 3
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 200 } as DOMRect)
    render(<GaugeBar value={50} label="Queue health" ticks={HEALTH_TICKS} />)
    expect(document.querySelector('[data-gauge-ticks]')).toHaveAttribute('data-gauge-ticks', 'full')
    expect((screen.getByText('At risk · 33') as HTMLElement).style.left).toMatch(/px$/)
  })
})

describe('GaugeBar — layout and text', () => {
  it('inline: the root is the table meter — the track then the value, on one line', () => {
    // intelligence-table-geometry.spec.ts measures the cell's FIRST child's
    // scrollWidth as "the meter plus its percentage": that must be this root.
    const { container } = render(
      <GaugeBar value={87} label="Pass rate" size="sm" thickness={5} outlined showValue format={(v) => `${v}%`} />,
    )
    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveAttribute('role', 'meter')
    expect(root.className).toContain('inline-flex')
    expect(root.className).toContain('whitespace-nowrap')
    const [trackEl, valueEl] = [...root.children] as HTMLElement[]
    expect(trackEl).toHaveAttribute('data-gauge-track')
    expect(trackEl.style.width).toBe('56px')
    expect(trackEl.style.height).toBe('5px')
    expect(trackEl.className).toContain('border')
    expect(valueEl).toHaveTextContent('87%')
  })

  it('sizes: md is a 6 px track, sm a 4 px one', () => {
    const { container, rerender } = render(<GaugeBar value={1} label="x" />)
    expect(q(container, '[data-gauge-track]')?.style.height).toBe('6px')
    rerender(<GaugeBar value={1} label="x" size="sm" width={110} />)
    expect(q(container, '[data-gauge-track]')?.style.height).toBe('4px')
    expect(q(container, '[data-gauge-track]')?.style.width).toBe('110px')
  })

  it('showLabel draws the label and names the meter by it', () => {
    render(<GaugeBar value={30} label="Spend MTD" showLabel />)
    expect(screen.getByText('Spend MTD')).toBeVisible()
    expect(screen.getByRole('meter', { name: 'Spend MTD' })).toHaveAttribute('aria-labelledby')
  })

  it('hostile names reach the DOM as text, never as markup', () => {
    const evil = '<img src=x onerror=alert(1)>'
    const { container } = render(
      <GaugeBar
        value={5}
        domain={[0, 10]}
        label={evil}
        showLabel
        segments={[{ value: 5, label: evil }]}
        target={{ value: 5, label: evil }}
        ticks={[{ value: 0, label: evil }]}
      />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(evil)).toBeInTheDocument()
    expect(screen.getByText(`${evil} · 0`)).toBeInTheDocument()
  })
})
