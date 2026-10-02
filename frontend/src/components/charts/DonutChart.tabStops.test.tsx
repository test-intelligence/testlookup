/**
 * Wave 2.6 R2-12: a donut is ONE Tab stop — the named cursor surface the kit
 * gives it (`useChartCursor`) — and nothing inside its drawing.
 *
 * Recharts 3's `<Pie>` puts `tabindex="0"` on its sectors' `g` by default
 * (`rootTabIndex`), independently of `PieChart`'s `accessibilityLayer`. That
 * `g` has no role, so a keyboard user met a second stop right after the chart
 * whose only name was the centre text run together ("4,437executions"), on
 * every donut on Overview, Summary and the Release gate.
 *
 * The real Recharts tree is rendered here (only `ResponsiveContainer` is
 * replaced, by one that hands the chart a fixed size, so Recharts draws its
 * sectors in jsdom, which has no layout).
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import DonutChart from './DonutChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartResponse, ChartState } from './chartStateCore'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 400, height: 260 })
        : null,
  }
})

/** What the Tab key can land on: a non-negative tabindex, or an element focusable by default. */
const TABBABLE = '[tabindex]:not([tabindex="-1"]), a[href], button:not([disabled]), input:not([disabled]), select, textarea'

const ready = (points: [string, number][]): ChartState<ChartResponse> => {
  const series: SeriesChart = {
    kind: 'series',
    dimensions: ['status'],
    x_type: 'category',
    series: [{ key: 'count', label: 'Count', points: points.map(([x, y]) => ({ x, y, n: y })) }],
  }
  return { status: 'ready', data: { meta: null, series }, meta: null, revalidating: false }
}

function renderDonut(points: [string, number][]) {
  return render(
    <ChartAnnouncerProvider>
      <DonutChart title="Execution results" state={ready(points)} animate={false} />
    </ChartAnnouncerProvider>,
  )
}

describe('the donut has one Tab stop: its cursor surface (R2-12)', () => {
  it.each([
    ['several slices', [['passed', 880], ['failed', 120], ['skipped', 40]] as [string, number][]],
    ['a full ring', [['passed', 4437]] as [string, number][]],
  ])('%s: nothing inside the drawing is tabbable', (_, points) => {
    const { container } = renderDonut(points)
    const svg = container.querySelector('[data-donut] svg.recharts-surface')
    // The control: Recharts really drew the ring (sectors and all), so an empty
    // svg cannot pass this.
    expect(svg).not.toBeNull()
    expect(svg?.querySelectorAll('.recharts-pie-sector').length).toBeGreaterThan(0)
    expect([...(svg?.querySelectorAll(TABBABLE) ?? [])].map((el) => el.outerHTML.slice(0, 80))).toEqual([])
  })

  it('the surface the kit intends is still the stop, named and focusable', () => {
    const { container } = renderDonut([['passed', 880], ['failed', 120]])
    const surface = container.querySelector('[data-donut]') as HTMLElement
    expect(surface).toHaveAttribute('tabindex', '0')
    expect(surface).toHaveAttribute('role', 'group')
    expect(surface.getAttribute('aria-label')).toContain('Execution results')
    // Inside the donut's box, the only stops are the surface and the legend's own controls.
    const stops = [...surface.querySelectorAll(TABBABLE)].filter((el) => !el.closest('.recharts-legend-wrapper'))
    expect(stops).toEqual([])
  })
})
