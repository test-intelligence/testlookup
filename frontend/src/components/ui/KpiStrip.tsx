import type { ReactNode } from 'react'
import { clsx } from 'clsx'

export const KPI_STRIP_MAX = 5

/**
 * The strip's columns: as many tracks as fit at the tile's narrowest width (`--kpi-tile-min`: 10rem at the desk,
 * 16.5rem in presentation mode, `index.css`), the tiles sharing the width.
 */
export const KPI_STRIP_COLUMNS = 'repeat(auto-fit, minmax(var(--kpi-tile-min, 10rem), 1fr))'

/**
 * At most five compact `MetricCard`s in one row (UX redesign P0), above a
 * page's primary content. More than five is a design error: the dev build
 * warns, and only the first five are drawn.
 */
export default function KpiStrip({ children, className }: { children: ReactNode[]; className?: string }) {
  const tiles = children.filter(Boolean)
  if (import.meta.env.DEV && tiles.length > KPI_STRIP_MAX) {
    console.warn(`KpiStrip: ${tiles.length} tiles given; it shows at most ${KPI_STRIP_MAX}.`)
  }
  // One row while every tile gets at least `--kpi-tile-min`; fewer columns only when the strip is narrower than
  // that. At the desk that is 10rem (five tiles in one row at 1280 px). In presentation mode the tile's label and
  // change line are 16 px (W29b: the `.eyebrow` and `text-xs` tokens grow), so five 186 px tiles cut "TOTAL
  // EXECU…" and "Down …": the floor grows to 16.5rem there, three columns at 1280 px and four at 1440. A fixed
  // `repeat(5, …)` overflowed the page sideways at 640 px in presentation mode. `auto-fit` collapses the unused
  // tracks, so three tiles still share the full width.
  return (
    <div data-kpi-strip="" className={clsx('grid gap-3', className)} style={{ gridTemplateColumns: KPI_STRIP_COLUMNS }}>
      {tiles.slice(0, KPI_STRIP_MAX)}
    </div>
  )
}
