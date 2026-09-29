/**
 * VIZ-104 (K4b) — `PassRateGauge` IS `RingGauge` with its defaults, pixel for
 * pixel.
 *
 * The chart gallery screenshots `PassRateGauge` (`pass-rate-gauge`, and the
 * zero item) on Linux in CI. Wave 2.5 generalises it into `RingGauge` for the
 * release-gate risk arc, and the gallery baselines must not move. So the
 * snapshot below was written from the tree BEFORE `RingGauge` existed (main
 * d55c6ce1, `PassRateGauge.tsx` untouched), through REAL Recharts: only the
 * `ResponsiveContainer` is given a fixed size, because jsdom lays nothing
 * out. A change to it is a change to a visual baseline — do not update it to
 * make this pass unless that is the intent.
 *
 * `PassRateGauge` must keep rendering those bytes exactly (it keeps Recharts'
 * `accessibilityLayer` on: `rechartsA11y.test.tsx` and the gallery's
 * LEGACY_APPLICATION_LAYER ratchet both count it, and neither is this wave's).
 * `RingGauge`'s default differs from it in exactly two NON-visual ways, which
 * this file removes before comparing, so every drawn byte is still pinned:
 *   - `accessibilityLayer={false}` (RULES.md): Recharts' unnamed
 *     `role="application"` tab stop, which does nothing on a gauge, is gone.
 *   - the wrapper is a named `role="meter"` with `aria-value*`, so the value
 *     reaches a screen reader as a value.
 * (`RingGauge` also shrinks its number when the MEASURED text overruns the
 * ring — a real browser at a small size only; at the gallery's 320 px nothing
 * overruns, and jsdom measures nothing. The last block below covers it.)
 *
 * Before this file was committed, a one-off sweep also compared the
 * refactored `PassRateGauge` against the ORIGINAL file (`git show
 * d55c6ce1:…/PassRateGauge.tsx`) over 216 renders — sizes 60/120/200/320 x
 * animate undefined/false/true x 18 values including NaN, ±Infinity, both
 * clamps and every band edge: identical bytes in all 216 (docs/viz-work/w25/B4.md).
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PassRateGauge from './PassRateGauge'
import RingGauge from './RingGauge'
import { bandsTone, ringTextWidth } from './gaugeBar.model'

// The container is the gauge's own square `size` box; each case sets it.
const box = vi.hoisted(() => ({ size: 120 }))

// jsdom has no fonts: the text-fit cases give one (every character the same
// width). `null` = no canvas, which is what every other case sees — and
// jsdom lays nothing out anyway, so the fit never runs there.
const measured = vi.hoisted(() => ({ charPx: null as number | null }))
vi.mock('./textMeasure', () => ({
  measureTextWidth: (text: string) => (measured.charPx === null ? null : text.length * measured.charPx),
}))

afterEach(() => {
  measured.charPx = null
  box.size = 120
  vi.restoreAllMocks()
})

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, {
            width: box.size,
            height: box.size,
          })
        : null,
  }
})

/**
 * Recharts numbers its clip-path ids from a counter shared by every chart the
 * process has drawn, so the same gauge gets a different id in a different
 * test order. The ids carry no pixels; every other byte is compared.
 */
function normaliseIds(html: string): string {
  return html.replace(/recharts\d+-clip/g, 'recharts#-clip')
}

/** The gallery's two items, plus both band edges and both clamp ends. */
const PINNED_CASES: { name: string; value: number; size?: number; animate?: boolean }[] = [
  { name: 'gallery pass-rate-gauge', value: 96.4, size: 320, animate: false },
  { name: 'gallery zero', value: 0, size: 320, animate: false },
  { name: 'default size, good band edge', value: 95, animate: false },
  { name: 'warn band edge', value: 80, animate: false },
  { name: 'just under warn', value: 79.9, animate: false },
  { name: 'over 100 clamps the arc', value: 104, animate: false },
  { name: 'negative clamps the arc', value: -3, animate: false },
]

describe('PassRateGauge — the pinned pre-RingGauge render', () => {
  for (const c of PINNED_CASES) {
    it(`renders the same bytes as before RingGauge existed: ${c.name}`, () => {
      box.size = c.size ?? 120
      const { container } = render(<PassRateGauge value={c.value} size={c.size} animate={c.animate} />)
      expect(normaliseIds(container.innerHTML)).toMatchSnapshot()
    })
  }
})

/** Recharts' accessibility layer: two attributes on the surface, nothing drawn. */
const APPLICATION_LAYER = ' role="application" tabindex="0"'
/** The meter semantics on the wrapper, nothing drawn. */
const METER_ATTRS = /^<div role="meter" aria-label="[^"]*" aria-valuenow="[^"]*" aria-valuemin="0" aria-valuemax="100" aria-valuetext="[^"]*" /

describe('RingGauge — its default IS PassRateGauge, minus two non-visual defects', () => {
  for (const c of PINNED_CASES) {
    it(`draws the same bytes as PassRateGauge: ${c.name}`, () => {
      box.size = c.size ?? 120
      const legacy = normaliseIds(render(<PassRateGauge value={c.value} size={c.size} animate={c.animate} />).container.innerHTML)
      const ring = render(<RingGauge value={c.value} size={c.size} animate={c.animate} />).container
      const html = normaliseIds(ring.innerHTML)

      // What differs, exactly: the meter semantics are there and the
      // application layer is not …
      expect(html).toMatch(METER_ATTRS)
      expect(html).not.toContain('role="application"')
      expect(legacy.split(APPLICATION_LAYER)).toHaveLength(2)
      // … and with those two removed, every other byte is the same.
      expect(html.replace(METER_ATTRS, '<div ')).toBe(legacy.replace(APPLICATION_LAYER, ''))
    })
  }

  it('is a named meter with the true value in words and the clamped one in aria-valuenow', () => {
    const { getByRole } = render(<RingGauge value={104} />)
    const meter = getByRole('meter', { name: 'Pass Rate' })
    expect(meter).toHaveAttribute('aria-valuenow', '100')
    expect(meter).toHaveAttribute('aria-valuetext', '104.0%')
  })
})

describe('RingGauge — the release-gate risk arc', () => {
  const risk = bandsTone({ direction: 'lower-is-better', thresholds: [40, 70] })
  const arcFill = (c: HTMLElement) =>
    c.querySelector('.recharts-radial-bar-sector')?.getAttribute('fill') ?? null

  it('lower is better: a low risk is green, a high one red — the reverse of a pass rate', () => {
    const low = render(<RingGauge animate={false} value={20} tone={risk} caption="Risk Score" format={String} />).container
    expect(arcFill(low)).toBe('var(--status-passed)')
    const high = render(<RingGauge animate={false} value={85} tone={risk} caption="Risk Score" format={String} />).container
    expect(arcFill(high)).toBe('var(--status-failed)')
    const mid = render(<RingGauge animate={false} value={40} tone={risk} caption="Risk Score" format={String} />).container
    expect(arcFill(mid)).toBe('var(--status-broken)')
  })

  it('draws its text and track in theme tokens (the old arc wrote white on the light theme, over a fixed slate track)', () => {
    const { getByText, container } = render(
      <RingGauge animate={false} value={37} tone={risk} caption="Risk Score" format={String} />,
    )
    expect(getByText('37').className).toContain('text-[var(--color-text)]')
    expect(getByText('Risk Score')).toBeInTheDocument()
    expect(container.innerHTML).not.toMatch(/fill="white"/)
    expect(container.querySelector('.recharts-radial-bar-background-sector')).toHaveAttribute('fill', 'var(--chart-grid)')
  })

  it('not measured is an empty ring and a dash, announced as not measured — never a red 0', () => {
    const { getByRole, getByText, queryByRole, container } = render(<RingGauge animate={false} value={null} caption="Risk Score" />)
    expect(queryByRole('meter')).toBeNull()
    expect(getByRole('img', { name: 'Risk Score: not measured' })).toBeInTheDocument()
    expect(getByText('—')).toBeInTheDocument()
    // Value 0 draws no arc: only the grey track.
    expect(container.querySelector('.recharts-radial-bar-sector')).toBeNull()
    expect(container.innerHTML).not.toContain('var(--status-failed)')
  })

  it('NaN is not measured either', () => {
    const { getByRole } = render(<RingGauge value={Number.NaN} label="Risk" />)
    expect(getByRole('img', { name: 'Risk: not measured' })).toBeInTheDocument()
  })

  it('uses the caller\'s accessible name when given', () => {
    const { getByRole } = render(<RingGauge value={30} caption="Risk Score" label="Release risk score" format={String} />)
    expect(getByRole('meter', { name: 'Release risk score' })).toHaveAttribute('aria-valuetext', '30')
  })
})

describe('RingGauge — text that holds in a wider font', () => {
  it('the hole it measures against matches the ring Recharts draws', () => {
    // Recharts' inner arc radius is 40.75 at 120 px and 122.75 at 320 px
    // (the pinned snapshot); the text gets 85 % of that diameter.
    expect(ringTextWidth(120) / 0.85 / 2).toBeCloseTo(40.4, 0)
    expect(ringTextWidth(320) / 0.85 / 2).toBeCloseTo(122.9, 0)
  })

  it('shrinks the number when the measured text is wider than the hole, and only then', () => {
    measured.charPx = 16 // "100.0%" is 96 px: wider than the 120 px ring's 68.7 px
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 50 } as DOMRect)
    const { getByText, unmount } = render(<RingGauge value={100} />)
    const px = Number.parseFloat((getByText('100.0%') as HTMLElement).style.fontSize)
    expect(px).toBeGreaterThan(0)
    expect(px).toBeLessThan(20)
    expect((px / 20) * 96).toBeLessThanOrEqual(ringTextWidth(120))
    unmount()

    measured.charPx = 8 // 48 px: fits, so the class's size stands
    expect((render(<RingGauge value={100} />).getByText('100.0%') as HTMLElement).style.fontSize).toBe('')
  })

  it('PassRateGauge never refits: its bytes are pinned', () => {
    measured.charPx = 16
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 50 } as DOMRect)
    const { getByText } = render(<PassRateGauge value={100} />)
    expect((getByText('100.0%') as HTMLElement).style.fontSize).toBe('')
  })
})
