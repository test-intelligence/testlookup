/**
 * The four release count cards with their twelve-week trend (W11 REL-03).
 *
 * Mounted only while the "Release health · this week" disclosure is open
 * (`Disclosure` mounts its children only then), so the one trend read
 * (`GET /releases/portfolio-trend`) happens when a reader opens it, never on
 * the page's load. While it loads the cards show without sparklines (no
 * skeleton, nothing shifts); a failed read keeps the cards and says so in one
 * line under them.
 */
import { useReleasePortfolioTrend } from '@/hooks/useReleasePortfolioTrend'
import KpiStrip from './KpiStrip'
import type { DerivedRelease } from './types'

export default function PortfolioKpis({ releases }: { releases: DerivedRelease[] }) {
  const { data, error } = useReleasePortfolioTrend()
  return <KpiStrip releases={releases} trend={data ?? null} trendError={Boolean(error) && !data} />
}
