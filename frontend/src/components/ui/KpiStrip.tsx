import type { ReactNode } from 'react'
import { clsx } from 'clsx'

export const KPI_STRIP_MAX = 5

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
  return (
    <div data-kpi-strip="" className={clsx('grid gap-3', className)} style={{ gridTemplateColumns: `repeat(${Math.min(tiles.length, KPI_STRIP_MAX) || 1}, minmax(0, 1fr))` }}>
      {tiles.slice(0, KPI_STRIP_MAX)}
    </div>
  )
}
