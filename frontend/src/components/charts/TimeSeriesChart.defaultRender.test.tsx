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
 *
 * Wave 2.4 (VIZ-606 / VIZ-608) gave EVERY drawn frame two toolbar buttons —
 * Export and Full screen. They belong to `ChartFrame`, not to the time-series
 * chart, and are pinned by `ChartFrame.fullscreen.test.tsx`'s own regression
 * snapshot. So this test removes exactly those two controls
 * (`WAVE_24_TOOLBAR`) before comparing, and the snapshot below stays the
 * pre-VIZ-405 bytes: everything else the chart draws must still be unchanged.
 *
 * The export menu is replaced by a bare placeholder carrying its hook. Not to
 * hide its DOM — that is stripped anyway, and `ChartExportMenu.test.tsx` owns
 * it — but because the real menu calls `useId` twice, and React's client id
 * counter is global: removing the menu's nodes afterwards cannot give those
 * numbers back, so every id the chart renders after it (the pattern prefix,
 * the summary id, Recharts' clip ids) would be renumbered and the comparison
 * would fail on numbering alone. The full-screen button takes no id.
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

vi.mock('./ChartExportMenu', () => ({
  default: () => <div data-chart-export="" />,
}))

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

/**
 * The frame-level controls Wave 2.4 added to every drawn frame (see the header).
 * Only these two, by their own hooks — anything else new in the DOM still fails.
 */
const WAVE_24_TOOLBAR = '[data-chart-export], [data-chart-fullscreen-toggle]'

/** The rendered DOM without the Wave 2.4 frame controls. */
function withoutFrameControls(container: HTMLElement): string {
  const copy = container.cloneNode(true) as HTMLElement
  const controls = copy.querySelectorAll(WAVE_24_TOOLBAR)
  // Both must be there: a test that strips nothing proves nothing about them.
  expect(controls).toHaveLength(2)
  controls.forEach((node) => node.remove())
  return copy.innerHTML
}

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
      expect(withoutFrameControls(container)).toMatchSnapshot()
    })
  }
})
