/**
 * Inbox › Insights (W23a FAIL-11, contract §2.1 D-2 / §4.1, `?tab=insights`):
 * the same project-wide rows as the Overview card, every one of them, read
 * only while this tab is open (the page renders it only on its own tab — the
 * Activity precedent: no request, and no count, on an Inbox load).
 *
 * One project only: All Projects shows the project prompt and asks nothing.
 * A toolbar row — the `24 h` · `7 d` toggle, `Show dismissed (n)` (the
 * server's `dismissed_total`) and `Showing n of total` (the server's `total`,
 * never the page length) — then the list in a card. Dismissed rows stay
 * readable with who dismissed them; there is no un-dismiss.
 */
import { useState } from 'react'
import { Lightbulb } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import InsightsList, { INSIGHT_LINK_CLASS, InsightsRangeToggle } from '@/components/insights/InsightsList'
import {
  HIDE_DISMISSED,
  INSIGHTS_PROJECT_PROMPT,
  LOAD_ERROR,
  showDismissedLabel,
  showingLine,
} from '@/components/insights/insightsModel'
import { useInsights } from '@/hooks/useInsights'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { INSIGHT_LIST_MAX, type InsightRange } from '@/types/insights'

export default function InsightsTab() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const project = useProjectStore((s) => s.activeProject)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const [range, setRange] = useState<InsightRange>('7d')
  const [showDismissed, setShowDismissed] = useState(false)
  // Every row it may read (`limit=100`, the server's bound); the card takes the default.
  const { data, error, isLoading } = useInsights(projectId, range, showDismissed, INSIGHT_LIST_MAX)

  if (!projectId) {
    return <ProjectRequiredEmptyState description={INSIGHTS_PROJECT_PROMPT} icon={<Lightbulb className="h-6 w-6" />} />
  }

  const dismissedTotal = typeof data?.dismissed_total === 'number' ? data.dismissed_total : 0
  const shown = data && Array.isArray(data.items)
    ? data.items.filter((item) => showDismissed || !item.dismissed_at).length
    : 0

  return (
    <div className="space-y-4" data-inbox-insights="">
      <div className="flex flex-wrap items-center gap-3">
        <InsightsRangeToggle value={range} onChange={setRange} />
        {(dismissedTotal > 0 || showDismissed) && (
          <button
            type="button"
            aria-pressed={showDismissed}
            data-insights-show-dismissed=""
            onClick={() => setShowDismissed((on) => !on)}
            className={INSIGHT_LINK_CLASS}
          >
            {showDismissed ? HIDE_DISMISSED : showDismissedLabel(dismissedTotal)}
          </button>
        )}
        {data && data.enabled !== false && typeof data.total === 'number' && (
          <span data-insights-showing="" className="ml-auto text-xs tabular-nums text-[var(--color-text-muted)]">
            {showingLine(shown, data.total)}
          </span>
        )}
      </div>

      <section aria-label="Insights" data-primary="">
        {data ? (
          <div className="card !p-0 overflow-hidden">
            <InsightsList
              projectId={projectId}
              projectName={project?.name ?? 'this project'}
              data={data}
              range={range}
              includeDismissed={showDismissed}
            />
          </div>
        ) : isLoading && !error ? (
          <div className="flex items-center justify-center py-16" aria-busy="true">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <EmptyState tone="warning" role="alert" icon={<Lightbulb className="h-6 w-6" />} title={LOAD_ERROR} />
        )}
      </section>
    </div>
  )
}
