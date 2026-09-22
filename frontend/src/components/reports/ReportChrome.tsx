/**
 * The connected report chrome: `useReportScope()` (the state half, VIZ-303)
 * plus ONE `/metrics/summary` request (`useReportMetrics`), whose `meta` feeds
 * the header, the chips' warnings and the summary, and whose KPI fields feed
 * the strip.
 *
 * Mounted once per report route by `ReportChromeSlot` in the layout, which
 * loads this module lazily — nothing here is in the entry chunk — and only
 * when BOTH `viz_report_context` and `viz_multi_filters` are on.
 *
 * Two clocks, as on the pages: the controls (bar, chips) follow every click
 * through `useReportScope`; the REQUEST is built from the same SETTLED scope
 * the page's data hooks read (`useReleaseScope` / `useSuiteScope`, 250 ms
 * after the last change). The window is the page's own snap of the stored
 * value (`windowOptionsFor(route)`), capped at what `/metrics/summary`
 * accepts (`summaryWindow`); when the cap bites the header says so. So the
 * strip answers for the scope the page's panels answer for, a burst of ticks
 * is one request, and a stored "All time" or "1 year" is never a 422.
 *
 * A failed request never toasts (`suppressToast` in the hook): the header
 * states the reason and every tile is "—" with it.
 */
import { useMemo } from 'react'
import type { MultiSelectOption } from '@/components/ui/MultiSelect'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { useReleases } from '@/hooks/useReleases'
import { summaryWindow, useReportMetrics, type ReportMetricsScope } from '@/hooks/useReportMetrics'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { useSuiteScope } from '@/hooks/useSuiteScope'
import { normalizeScope } from '@/lib/scopeParams'
import { DEFAULT_WINDOW_DAYS, RELEASE_CAP, SUITE_CAP, useReportScope } from '@/store/reportScope'
import { metricsFromSummary, metricsUnavailable } from './metricsModel'
import { releaseOptionsFrom } from './releaseOptions'
import ReportChromeView from './ReportChromeView'
import { windowOptionsFor, type ReportRoute } from './reportRoutes'

export default function ReportChrome({ route }: { route: ReportRoute }) {
  const scope = useReportScope()
  // The page's window (its own snap of the stored value) and what the summary
  // endpoint is asked for (capped at 90).
  const windowOptions = windowOptionsFor(route)
  const win = summaryWindow(scope.windowDays, windowOptions)
  // The DATA clock: the settled selection the page's own hooks request with.
  const settledReleases = useReleaseScope()
  const settledSuites = useSuiteScope()
  const requestScope = useMemo<ReportMetricsScope>(
    () => ({
      projectId: scope.projectId,
      allProjects: scope.allProjects,
      // Both are identity-stable (sorted, memoised on content) or null.
      releaseIds: normalizeScope(settledReleases),
      suiteNames: normalizeScope(settledSuites),
      windowDays: win.days,
    }),
    [scope.projectId, scope.allProjects, win.days, settledReleases, settledSuites],
  )
  const { summary, meta, metaErrors, isLoading, errorReason } = useReportMetrics(requestScope)
  const { data: releaseData } = useReleases()
  const { options: suiteNames } = useSuiteOptions(win.days)

  const releaseOptions = useMemo(() => releaseOptionsFrom(releaseData?.items ?? []), [releaseData])
  const suiteOptions = useMemo<MultiSelectOption[]>(() => suiteNames.map((s) => ({ value: s, label: s })), [suiteNames])

  const releaseLabel = (id: string) =>
    releaseOptions.find((o) => o.value === id)?.label ??
    meta?.scope.releases.find((r) => r.id === id)?.name ??
    id

  const unavailableReason = errorReason
    ? `Report context is unavailable: ${errorReason}.`
    : metaErrors.length > 0
      ? 'Report context is unavailable: the response did not describe its scope.'
      : undefined

  return (
    <ReportChromeView
      data-testid={`report-chrome-${route}`}
      meta={meta}
      allProjects={scope.allProjects}
      loading={isLoading && !summary}
      unavailableReason={unavailableReason}
      windowNote={win.note ? `(${win.note})` : undefined}
      bar={{
        releaseOptions,
        suiteOptions,
        releaseIds: scope.releaseIds,
        suiteNames: scope.suiteNames,
        windowDays: win.pageDays,
        windowOptions,
        defaultWindowDays: DEFAULT_WINDOW_DAYS,
        releaseCap: RELEASE_CAP,
        suiteCap: SUITE_CAP,
        allProjects: scope.allProjects,
        onReleaseChange: scope.setReleaseIds,
        onSuiteChange: scope.setSuiteNames,
        onWindowChange: scope.setWindowDays,
        droppedNotice: scope.droppedNotice,
        onDismissNotice: scope.dismissDroppedNotice,
      }}
      chips={{
        releases: (scope.allProjects ? [] : scope.releaseIds).map((id) => ({ id, label: releaseLabel(id) })),
        suites: scope.suiteNames,
        windowDays: win.pageDays,
        defaultWindowDays: DEFAULT_WINDOW_DAYS,
        onRemoveRelease: (id) => scope.setReleaseIds(scope.releaseIds.filter((r) => r !== id)),
        onRemoveSuite: (name) => scope.setSuiteNames(scope.suiteNames.filter((s) => s !== name)),
        onResetWindow: () => scope.setWindowDays(DEFAULT_WINDOW_DAYS),
        onClearAll: scope.clearAll,
      }}
      metrics={errorReason ? metricsUnavailable(errorReason) : metricsFromSummary(summary, meta)}
    />
  )
}
