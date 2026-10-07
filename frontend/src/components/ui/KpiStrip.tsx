import type { ReactNode } from 'react'
import { clsx } from 'clsx'

export const KPI_STRIP_MAX = 5

/** The strip's columns: as many 10rem-or-wider tracks as fit, the tiles sharing the width. */
export const KPI_STRIP_COLUMNS = 'repeat(auto-fit, minmax(10rem, 1fr))'

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
  // One row while every tile gets at least 10rem (a 40 px presentation-mode
  // value such as "8m 32s" needs ~144 px); fewer columns only when the strip
  // is narrower than that. A fixed `repeat(5, …)` overflowed the page
  // sideways at 640 px in presentation mode. `auto-fit` collapses the unused
  // tracks, so three tiles still share the full width.
  return (
    <div data-kpi-strip="" className={clsx('grid gap-3', className)} style={{ gridTemplateColumns: KPI_STRIP_COLUMNS }}>
      {tiles.slice(0, KPI_STRIP_MAX)}
    </div>
  )
}
