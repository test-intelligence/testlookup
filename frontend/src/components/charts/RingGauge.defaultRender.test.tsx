/**
 * VIZ-104 (K4b) — `RingGauge`'s own default render, pixel for pixel.
 *
 * The chart gallery screenshots the ring (`ring-gauge-*`) and the release gate
 * draws it (`gate-risk-gauge`) on Linux in CI. The snapshot below pins every
 * byte `RingGauge` draws with its defaults, through REAL Recharts: only the
 * `ResponsiveContainer` is given a fixed size, because jsdom lays nothing
 * out. A change to it is a change to a visual baseline — do not update it to
 * make this pass unless that is the intent.
 *
 * What the default render carries, and the snapshot therefore pins:
 *   - `accessibilityLayer={false}` (RULES.md): no unnamed `role="application"`
 *     tab stop on the surface, which did nothing on a gauge;
 *   - the wrapper is a named `role="meter"` with `aria-value*`, so the value
 *     reaches a screen reader as a value;
 *   - the track's background sector carries a 1 px `NON_TEXT_EDGE` stroke
 *     (R2 F9), so the scale's end can be seen on the card.
 * (`RingGauge` also shrinks its number when the MEASURED text overruns the
 * ring — a real browser at a small size only; at the gallery's 320 px nothing
 * overruns, and jsdom measures nothing. The last block below covers it.)
 *
 * History. The ring began as `PassRateGauge` (main d55c6ce1); Wave 2.5
 * generalised it into `RingGauge` for the release-gate risk arc. Before the
 * refactor was committed, a one-off sweep compared the refactored
 * `PassRateGauge` against the ORIGINAL file (`git show
 * d55c6ce1:…/PassRateGauge.tsx`) over 216 renders — sizes 60/120/200/320 x
 * animate undefined/false/true x 18 values including NaN, ±Infinity, both
 * clamps and every band edge: identical bytes in all 216. This file then
 * pinned `PassRateGauge`'s pre-refactor bytes and proved `RingGauge`'s default
 * drew the same, minus the three differences above; W29b UIX-04b changed the
 * caption to `text-2xs` (11 px) on purpose. W25c (HYG-01) deleted
 * `PassRateGauge`: the snapshot below was recorded while that equality proof
 * still ran, in the same run, so it is exactly the old pins with those three
 * differences, and it is now the ring's own pin.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

/**
 * The two former `PassRateGauge` gallery items' values (96.4 and 0, at the
 * canvas's 320 px), plus both band edges and both clamp ends. The names are
 * the snapshot keys, recorded in W25c: kept as they are.
 */
const PINNED_CASES: { name: string; value: number; size?: number; animate?: boolean }[] = [
  { name: 'gallery pass-rate-gauge', value: 96.4, size: 320, animate: false },
  { name: 'gallery zero', value: 0, size: 320, animate: false },
  { name: 'default size, good band edge', value: 95, animate: false },
  { name: 'warn band edge', value: 80, animate: false },
  { name: 'just under warn', value: 79.9, animate: false },
  { name: 'over 100 clamps the arc', value: 104, animate: false },
  { name: 'negative clamps the arc', value: -3, animate: false },
]

describe('RingGauge — the pinned default render', () => {
  for (const c of PINNED_CASES) {
    it(`draws the same bytes as its pinned default: ${c.name}`, () => {
      box.size = c.size ?? 120
      const { container } = render(<RingGauge value={c.value} size={c.size} animate={c.animate} />)
      expect(normaliseIds(container.innerHTML)).toMatchSnapshot()
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
})
