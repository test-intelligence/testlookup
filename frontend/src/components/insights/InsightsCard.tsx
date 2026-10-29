/**
 * The Overview "Insights" card (W23a FAIL-11, contract §4.1): below the home
 * sections, outside the home tabs, ONE project only — in All Projects it
 * renders nothing and asks nothing (`useInsights(null)` has no key).
 *
 * One read on load (`range=7d`, no dismissed rows); the `24 h` · `7 d` toggle
 * is component state and changes the key. At most {@link CARD_ROWS} rows,
 * then `Showing 5 of 12 · Show all in the Inbox` from the server's `total`
 * (never the page length). No polling: insights change hourly and SWR
 * revalidates on focus. A failed or malformed answer reads "Could not load
 * the insights." here and nothing else on the page changes.
 */
import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import PanelHeader from '@/components/ui/PanelHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useInsights } from '@/hooks/useInsights'
import type { InsightRange } from '@/types/insights'
import InsightsList, { INSIGHT_LINK_CLASS, InsightsRangeToggle } from './InsightsList'
import {
  CARD_ROWS,
  CARD_SUBTITLE,
  INBOX_INSIGHTS_PATH,
  INSIGHTS_TITLE,
  LOAD_ERROR,
  OPEN_IN_INBOX,
  SHOW_ALL_IN_INBOX,
  showingLine,
} from './insightsModel'

export interface InsightsCardProps {
  /** The active project, or null in All Projects (the card renders nothing). */
  projectId: string | null
  projectName: string
}

export default function InsightsCard({ projectId, projectName }: InsightsCardProps) {
  const headingId = useId()
  const [range, setRange] = useState<InsightRange>('7d')
  const { data, error, isLoading } = useInsights(projectId, range)
  if (!projectId) return null

  const visible = data && Array.isArray(data.items) ? data.items.filter((item) => !item.dismissed_at).length : 0
  const shown = Math.min(CARD_ROWS, visible)
  const total = typeof data?.total === 'number' ? data.total : visible

  let body: ReactNode
  if (data) {
    body = (
      <>
        <InsightsList projectId={projectId} projectName={projectName} data={data} range={range} maxRows={CARD_ROWS} />
        {data.enabled !== false && total > shown && (
          <p data-insights-showing="" className="m-0 border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
            {showingLine(shown, total)} ·{' '}
            <Link to={INBOX_INSIGHTS_PATH} className={INSIGHT_LINK_CLASS}>{SHOW_ALL_IN_INBOX}</Link>
          </p>
        )}
      </>
    )
  } else if (isLoading && !error) {
    body = (
      <div className="flex items-center justify-center py-6" aria-busy="true">
        <LoadingSpinner size="sm" />
      </div>
    )
  } else {
    body = <p role="alert" data-insights-error="" className="m-0 px-4 py-3 text-sm text-[var(--color-text-secondary)]">{LOAD_ERROR}</p>
  }

  return (
    <div role="region" aria-labelledby={headingId} data-insights-card="" className="card !p-0 overflow-hidden">
      <PanelHeader
        id={headingId}
        title={INSIGHTS_TITLE}
        subtitle={CARD_SUBTITLE}
        actions={(
          <>
            <InsightsRangeToggle value={range} onChange={setRange} />
            <Link to={INBOX_INSIGHTS_PATH} data-insights-open-inbox="" className={INSIGHT_LINK_CLASS}>{OPEN_IN_INBOX}</Link>
          </>
        )}
      />
      {body}
    </div>
  )
}
