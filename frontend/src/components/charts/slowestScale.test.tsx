/**
 * TRND-09 (VIZ follow-up 19): the slowest-tests bar scale stops at the p90 of
 * the shown p95s when one outlier would flatten the rest, the bars past it
 * carry an off-scale marker, and every real p95 is still printed.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SlowestTests from './SlowestTests'
import { rankSlowestTests } from './durationBuckets'
import { isOffScale, offScaleNote, OFF_SCALE_TEXT, slowestBarWidth, slowestScale, SLOWEST_CLAMP_FACTOR } from './slowestScale'

/** Nineteen tests of 1.0-1.9 s and one of 40 s: the acceptance case. */
const ONE_OUTLIER = [
  ...Array.from({ length: 19 }, (_, i) => ({ name: `checkout step ${i + 1}`, p95: 1_000 + i * 50, runs: 12 })),
  { name: 'nightly export', p95: 40_000, runs: 3 },
]

describe('slowestScale', () => {
  it('one 40 s outlier among 1-2 s tests: the scale stops at the p90, and only the outlier runs past it', () => {
    const scale = slowestScale(ONE_OUTLIER.map((row) => row.p95))
    // Nearest rank over 20 values: the 18th smallest, 1.0 s + 17 x 50 ms.
    expect(scale).toEqual({ max: 1_850, clamped: true, offScale: 2 })
    expect(isOffScale(40_000, scale)).toBe(true)
    expect(slowestBarWidth(40_000, scale)).toBe(100)
    // The other bars are no longer slivers: the fastest is over half the track.
    expect(slowestBarWidth(1_000, scale)).toBeGreaterThan(50)
  })

  it('an even spread keeps the plain linear scale: nothing is flattening anything', () => {
    const even = Array.from({ length: 20 }, (_, i) => 1_000 + i * 50)
    expect(slowestScale(even)).toEqual({ max: 1_950, clamped: false, offScale: 0 })
  })

  it(`clamps only past ${SLOWEST_CLAMP_FACTOR}x the p90 (the boundary itself is not clamped)`, () => {
    const at = [...Array.from({ length: 19 }, () => 1_000), 2_000]
    expect(slowestScale(at).clamped).toBe(false)
    const past = [...Array.from({ length: 19 }, () => 1_000), 2_001]
    expect(slowestScale(past)).toEqual({ max: 1_000, clamped: true, offScale: 1 })
  })

  it('a few rows are never clamped (a p90 of three values is their largest), and unmeasured rows take no part', () => {
    expect(slowestScale([100, 200, 90_000])).toEqual({ max: 90_000, clamped: false, offScale: 0 })
    expect(slowestScale([null, undefined, 0])).toEqual({ max: 0, clamped: false, offScale: 0 })
    const scale = slowestScale([...ONE_OUTLIER.map((row) => row.p95), null])
    expect(scale.max).toBe(1_850)
    expect(slowestBarWidth(null, scale)).toBe(0)
    expect(isOffScale(null, scale)).toBe(false)
  })
})

describe('SlowestTests with an outlier', () => {
  it('draws the outlier full width with the off-scale marker, prints its real p95, and says where the scale stops', () => {
    render(<SlowestTests model={rankSlowestTests(ONE_OUTLIER)} />)
    const items = screen.getAllByRole('listitem')
    // Ranked: the outlier first, its real figure printed.
    expect(within(items[0]).getByText('nightly export')).toBeInTheDocument()
    expect(within(items[0]).getByText('40.0s')).toBeInTheDocument()
    expect(within(items[0]).getByText(OFF_SCALE_TEXT)).toBeInTheDocument()
    const bars = screen.getAllByTestId('ranked-bar')
    expect(bars[0]).toHaveAttribute('data-off-scale', '')
    expect(bars[0].style.width).toBe('100%')
    expect(bars[0].querySelector('[data-off-scale-marker]')).not.toBeNull()
    // The second-slowest (1.9 s) also runs past the 1.85 s stop; the rest do not.
    expect(bars.filter((bar) => bar.hasAttribute('data-off-scale'))).toHaveLength(2)
    expect(Number.parseFloat(bars[19].style.width)).toBeGreaterThan(50)
    expect(document.querySelector('[data-chart-off-scale]')).toHaveTextContent(offScaleNote(1_850, 2))
    expect(offScaleNote(1_850, 2)).toBe(
      'Bars stop at 1.9s, the p90 of these tests; 2 longer tests run off the scale (hatched end), with their real p95 beside the bar.',
    )
  })

  it('an even spread draws no marker and no note', () => {
    render(<SlowestTests model={rankSlowestTests(ONE_OUTLIER.slice(0, 19))} />)
    expect(document.querySelector('[data-off-scale]')).toBeNull()
    expect(document.querySelector('[data-chart-off-scale]')).toBeNull()
  })

  it('two tests with one display name are two rows (the position is part of the key)', () => {
    render(<SlowestTests model={rankSlowestTests([{ name: 'same', p95: 10, runs: 1 }, { name: 'same', p95: 5, runs: 1 }])} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })
})
