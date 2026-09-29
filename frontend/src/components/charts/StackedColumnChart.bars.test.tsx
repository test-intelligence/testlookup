/**
 * VIZ-104 K1 — the HORIZONTAL-BAR form of `StackedColumnChart` (a category
 * chart of more than `MAX_CATEGORY_COLUMNS` buckets) and its edge cases, drawn
 * by real Recharts at a fixed size: a measured-zero row is marked ACROSS its
 * row at the value axis's zero (not along a column's baseline), a gap row is
 * never marked, the name axis is sized before any layout exists, and the
 * row's tooltip states a gap row's total as not measured.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import StackedColumnChart, {
  AXIS_LABEL_MAX_CHARS,
  MAX_CATEGORY_COLUMNS,
  StackedBarTooltip,
  barAxisLayout,
  estimateTextWidth,
  stackRowMark,
} from './StackedColumnChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { NOT_MEASURED_REASON, TOTAL_LABEL, buildStackedColumnModel } from './stackedColumnModel'
import { STATUS_SERIES } from './__fixtures__/stackedColumn'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 480 })
        : null,
    // The bar-form tooltip reads the value axis's scale from Recharts' context,
    // which a tooltip rendered on its own does not have: hand it a plain one.
    useXAxisScale: () => (value: number) => 100 + value * 2,
  }
})

/** Fourteen suites (past the dozen, so bars): one ran and executed nothing, one was not measured at all. */
const ROWS = buildStackedColumnModel({
  buckets: Array.from({ length: MAX_CATEGORY_COLUMNS + 2 }, (_, i) => ({
    key: `suite-${i}`,
    label: `suite ${i}`,
    values:
      i === 3
        ? { passed: 0, failed: 0 } // measured zero
        : i === 7
          ? {} // not measured
          : { passed: 10 + i, failed: i % 3 },
  })),
  series: STATUS_SERIES.slice(0, 2),
  valueTitle: 'Executions',
  bucketTitle: 'Suite',
})

const draw = (node: ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)

describe('StackedColumnChart · the bar form', () => {
  it('marks a measured-zero row ACROSS its row at the value axis’s zero, and never a gap row', () => {
    const { container } = draw(<StackedColumnChart model={ROWS} animate={false} />)
    expect(container.querySelector('[data-stacked-column-plot]')?.getAttribute('data-stacked-orientation')).toBe('bars')
    const ticks = container.querySelectorAll('[data-stacked-zero]')
    expect(ticks).toHaveLength(1)
    const tick = ticks[0]
    expect(tick.getAttribute('data-stacked-zero')).toBe('suite-3')
    const [x1, x2, y1, y2] = ['x1', 'x2', 'y1', 'y2'].map((name) => Number(tick.getAttribute(name)))
    // Across the row (vertical), 12 px long — a column's tick lies along the baseline instead.
    expect(x1).toBe(x2)
    expect(Math.abs(y2 - y1)).toBe(12)
    // At the value axis's zero: where every bar of the chart starts.
    const barLefts = Array.from(container.querySelectorAll('.recharts-bar-rectangle path'), (path) => Number(path.getAttribute('x')))
    expect(barLefts.length).toBeGreaterThan(0)
    expect(x1).toBeCloseTo(Math.min(...barLefts), 0)
    expect(container.querySelector('[data-stacked-zero="suite-7"]')).toBeNull()
  })

  it('sizes the name axis to the widest name cut, before the chart has any width', () => {
    const names = ['checkout', 'integration-tests/payments/checkout-flow-with-saved-card']
    const layout = barAxisLayout(names, 0, estimateTextWidth)
    // No width known: nothing forces a cut below the axis's own cap.
    expect(layout.maxChars).toBe(AXIS_LABEL_MAX_CHARS)
    // The long name, cut in the middle to the cap, plus the gap before its bar.
    expect(layout.width).toBe(Math.ceil(estimateTextWidth('x'.repeat(AXIS_LABEL_MAX_CHARS), 11) + 8))
  })

  it('has nowhere to pin a row whose scale gives no finite position', () => {
    expect(stackRowMark(30, 16, 50, () => Number.NaN)).toBeNull()
    expect(stackRowMark(30, 16, 50, () => undefined)).toBeNull()
  })

  it('states a gap row’s total as not measured in its tooltip, never as 0', () => {
    const open = render(<StackedBarTooltip active label="suite-7" coordinate={{ x: 100, y: 50 }} model={ROWS} />)
    const text = open.container.textContent ?? ''
    expect(text).toContain('suite 7')
    expect(text).toContain(`${TOTAL_LABEL}—(${NOT_MEASURED_REASON})`)
    expect(text).not.toMatch(new RegExp(`${TOTAL_LABEL}0`))
    // The measured-zero row IS 0, and says so.
    const zero = render(<StackedBarTooltip active label="suite-3" coordinate={{ x: 100, y: 80 }} model={ROWS} />)
    expect(zero.container.textContent).toContain(`${TOTAL_LABEL}0`)
  })
})
