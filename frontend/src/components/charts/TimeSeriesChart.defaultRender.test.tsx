/**
 * VIZ-405 — DEFAULT OFF, PIXEL-IDENTICAL.
 *
 * CI compares every existing chart-gallery item against committed Linux
 * screenshots. The trend overlays (moving average, trend line, anomalies) sit
 * behind an explicit `overlays` prop that no existing caller passes, and with
 * it absent `TimeSeriesChart` must render EXACTLY what it rendered before the
 * overlays existed: no toolbar, no caption, no extra element to shift layout.
 *
 * The snapshot below was written from the tree BEFORE any VIZ-405 code landed
 * (main 54ae9628), rendering the three gallery time-series items with the
 * gallery's own fixed clock, zone and locale, through REAL Recharts (only the
 * `ResponsiveContainer` is given a fixed size, because jsdom lays nothing out).
 * A change to it is a change to a visual baseline — do not update it to make
 * this pass unless that is the intent.
 *
 * ONE deliberate change since: VIZ-405 fix round B moved the rotated
 * "Pass rate %" axis title in by Recharts' `offset: 14` (it was the default
 * 5, and the title's line box overhung the svg's left edge by 3.1 px on every
 * time-series item). The snapshot diff is that and only that — the label's
 * `offset` 5 → 14, its `x` 5 → 14 and its rotation centre (5, 123) → (14, 123)
 * — and the `timeseries-*` visual baselines change with it, on purpose.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChartFrame from './TimeSeriesChartFrame'
import {
  inProgressRunsFixture,
  trendSinglePointFixture,
  trendWithReleasesFixture,
  trendZoomedAxisFixture,
} from './__fixtures__/wave2Fixtures'
import type { TimeSeriesModel } from './timeSeriesModel'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 260 })
        : null,
  }
})

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

// The gallery's fixed clock, zone and locale (chartGalleryFixtures.ts).
const NOW = new Date('2026-03-10T09:00:00Z')

const ITEMS: { id: string; title: string; model: TimeSeriesModel; inProgress?: boolean }[] = [
  { id: 'timeseries-trend-releases', title: 'TimeSeriesChart · releases + partial day', model: trendWithReleasesFixture, inProgress: true },
  { id: 'timeseries-single-point', title: 'TimeSeriesChart · one day', model: trendSinglePointFixture },
  { id: 'timeseries-zoomed-axis', title: 'TimeSeriesChart · zoomed rate axis', model: trendZoomedAxisFixture },
]

describe('TimeSeriesChartFrame — the default render is unchanged by VIZ-405', () => {
  for (const item of ITEMS) {
    it(`${item.id} renders the pre-VIZ-405 DOM`, () => {
      const { container } = render(
        <TimeSeriesChartFrame
          title={item.title}
          state={{ status: 'ready', data: null, meta: null, revalidating: false }}
          model={item.model}
          inProgressRuns={item.inProgress ? inProgressRunsFixture : undefined}
          headingLevel={2}
          height={260}
          animate={false}
          now={NOW}
          timeZone="UTC"
          locale="en-US"
        />,
      )
      // A real SVG was drawn — the snapshot is of the chart, not of an empty box.
      expect(container.querySelector('svg.recharts-surface')).not.toBeNull()
      expect(container.innerHTML).toMatchSnapshot()
    })
  }
})
