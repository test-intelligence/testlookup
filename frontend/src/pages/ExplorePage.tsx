/**
 * VIZ-505 — the Explorer (`/explore`): one metric over time, split into lines
 * by a series dimension and into small multiples (up to 12 panels) by suite
 * or release, all from `chart-data`.
 *
 * The configuration lives in the URL (`exploreUrl`), so a view is a link; the
 * pickers only offer what the API accepts (`exploreModel`). With a facet, one
 * DISCOVERY request (`executions` by the facet, and by the series when the two
 * may be paired) names the panels, largest first, and gives the series their
 * colours once for every panel; each panel then asks for itself when it comes
 * near the screen (`LazySection`), so a 12-panel grid costs one request per
 * panel the reader actually reaches. A y-scale change asks nothing.
 *
 * Release scope is the top bar's release (`useReleaseScope`); All Projects
 * offers no release facet (a release belongs to one project). The report
 * chrome is off, so the page mounts its own Saved Views, inside one project,
 * storing the configuration under `filters.explore`.
 */
import { useCallback, useMemo, useState, type ComponentProps, type ReactElement } from 'react'
import { useSearchParams } from 'react-router-dom'
import { LayoutGrid } from 'lucide-react'
import PageShell from '@/components/layout/PageShell'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { useCatalogChartData, type CatalogParams } from '@/components/charts/chartCatalogSources'
import { hasChartData } from '@/components/charts/chartStateCore'
import LazySection from '@/components/reports/catalogue/LazySection'
import { useCatalogueParams } from '@/components/reports/catalogue/catalogueScope'
import { useAdvancedRolloutStatus } from '@/components/reports/catalogue/useCatalogueRollout'
import { useEverHadRun } from '@/components/reports/catalogue/useEverHadRun'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import type { ReportViewScope } from '@/components/reports/savedViewsModel'
import ExploreControls from '@/components/explore/ExploreControls'
import ExplorePanel, { PANEL_CHROME, PANEL_HEIGHT } from '@/components/explore/ExplorePanel'
import {
  coerceConfig,
  DIMENSION_LABELS,
  discoveryParams,
  EXPLORE_DAYS,
  EXPLORE_METRICS,
  explorePlan,
  facetPanels,
  panelParams,
  seriesStyles,
  type ExploreConfig,
} from '@/components/explore/exploreModel'
import {
  DEFAULT_EXPLORE,
  EXPLORE_URL_KEYS,
  exploreViewFilters,
  readExploreConfig,
  writeExploreConfig,
} from '@/components/explore/exploreUrl'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { normalizeScope } from '@/lib/scopeParams'
import type { SavedView } from '@/services/savedViewsService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'

export const EXPLORER_TITLE = 'Explorer'
export const EXPLORER_SUBTITLE = 'One metric over time, split into lines and into small multiples by suite or release.'
export const EXPLORER_OFF_TITLE = 'The explorer is not enabled for this project'

const NO_STYLES = {}

/** Identity-stable params: the same request keeps one object, so nothing refetches on a re-render. */
function useStableParams(params: CatalogParams | null): CatalogParams | null {
  const key = params === null ? null : JSON.stringify(params)
  return useMemo(() => (key === null ? null : (JSON.parse(key) as CatalogParams)), [key])
}

/** A saved view's `filters.explore`, when it is an object at all. */
function exploreFiltersOf(view: SavedView): Record<string, unknown> | null {
  const raw = view.filters?.explore
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null
}

export default function ExplorePage(): ReactElement {
  const rollout = useAdvancedRolloutStatus()
  if (rollout === true) return <Explorer />
  return (
    <PageShell>
      <PageHeader title={EXPLORER_TITLE} subtitle={EXPLORER_SUBTITLE} />
      {rollout === false ? (
        <EmptyState
          icon={<LayoutGrid className="h-8 w-8" aria-hidden="true" />}
          title={EXPLORER_OFF_TITLE}
          description="It arrives with the advanced charts. An admin can turn them on under Settings, Feature flags."
        />
      ) : (
        <div data-explore-pending="" style={{ minHeight: PANEL_HEIGHT + PANEL_CHROME }} />
      )}
    </PageShell>
  )
}

function Explorer(): ReactElement {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const pinned = activeProjectId !== null && activeProjectId !== ALL_PROJECTS_ID
  const ctx = useMemo(() => ({ pinnedProject: pinned }), [pinned])
  const globalDays = useTimeWindowStore((s) => s.days)
  const defaultDays = snapToAllowed(globalDays, EXPLORE_DAYS)
  const [searchParams, setSearchParams] = useSearchParams()
  const read = useMemo(() => readExploreConfig(searchParams, ctx, { defaultDays }), [searchParams, ctx, defaultDays])
  const config = read.config
  const [changeNotices, setChangeNotices] = useState<string[]>([])

  const setConfig = useCallback(
    (next: ExploreConfig, notices: readonly string[] = []) => {
      const coerced = coerceConfig(next, ctx, DEFAULT_EXPLORE)
      setChangeNotices([...notices, ...coerced.notices])
      setSearchParams(writeExploreConfig(searchParams, { ...next, ...coerced.axes }), { replace: true })
    },
    [ctx, searchParams, setSearchParams],
  )

  const releaseScope = useReleaseScope()
  const base = useCatalogueParams(config.days, null)
  const everHadData = useEverHadRun(true)
  const plan = useMemo(() => explorePlan(config, ctx), [config, ctx])
  const info = EXPLORE_METRICS[config.metric]

  const discoveryRequest = useStableParams(plan.ok && plan.discovery && base ? discoveryParams(base, plan.discovery) : null)
  const discovery = useCatalogChartData('chart-data', { params: discoveryRequest, everHadData })
  const sharedLegend = plan.ok && plan.discovery?.sharedLegend === true
  // Both read the one discovery state, so each keeps its identity until it changes.
  const { facets, styles } = useMemo(() => {
    const chart = hasChartData(discovery) && discovery.data.series.kind === 'series' ? discovery.data.series : null
    return {
      facets: config.facet ? facetPanels(chart, hasChartData(discovery) ? discovery.meta : null, config.facet) : null,
      styles: sharedLegend ? seriesStyles(chart) : NO_STYLES,
    }
  }, [discovery, config.facet, sharedLegend])

  // What every panel reported, for a shared count scale. Any change to what
  // the panels ask for starts over: a value from the last metric is no scale.
  const generation = JSON.stringify([base, config.metric, config.x, config.series, config.facet])
  const [measured, setMeasured] = useState<{ generation: string; values: Record<string, number> }>({ generation: '', values: {} })
  const values = measured.generation === generation ? measured.values : {}
  const onMeasured = useCallback(
    (key: string, ownMax: number) =>
      setMeasured((prev) => {
        if (prev.generation !== generation) return { generation, values: { [key]: ownMax } }
        if (prev.values[key] === ownMax) return prev
        return { generation, values: { ...prev.values, [key]: ownMax } }
      }),
    [generation],
  )
  const reported = Object.values(values)
  const sharedMax = reported.length > 0 ? Math.max(...reported) : null

  const current: ReportViewScope = useMemo(
    () => ({ releaseIds: normalizeScope(releaseScope), suiteNames: [], windowDays: config.days }),
    [releaseScope, config.days],
  )
  const onApplyView = useCallback(
    (scope: ReportViewScope, view: SavedView) => {
      const windowDays = snapToAllowed(scope.windowDays, EXPLORE_DAYS)
      const saved = readExploreConfig(exploreFiltersOf(view), ctx, { defaultDays: windowDays })
      setConfig({ ...saved.config, days: windowDays }, saved.notices)
      useReleaseStore.getState().setActiveRelease(scope.releaseIds[0] ?? null, activeProjectId)
    },
    [ctx, setConfig, activeProjectId],
  )

  const notices = [...read.notices, ...changeNotices, ...(facets?.notices ?? [])]
  const seriesWords = config.series ? `, one line per ${DIMENSION_LABELS[config.series].label.toLowerCase()}` : ''
  const facetWords = config.facet ? `, one panel per ${DIMENSION_LABELS[config.facet].label.toLowerCase()}` : ''
  const summary = `${info.label} per ${config.x}${seriesWords}${facetWords}, last ${config.days} days.`

  const panelProps = {
    config,
    everHadData,
    styles,
    sharedMax,
    measured: reported.length,
    onMeasured,
  }

  let body: ReactElement
  if (!plan.ok) {
    // Unreachable through the pickers and the URL (both are coerced); said, not hidden.
    body = <EmptyState title="This combination cannot be drawn" description={plan.reason} />
  } else if (config.facet === null) {
    const params = base ? panelParams(base, config.metric, plan.panel, null, null) : null
    const title = `${info.label}${config.series ? ` by ${DIMENSION_LABELS[config.series].label.toLowerCase()}` : ''}`
    body = <ExplorePanelStable title={title} facetKey={null} params={params} panels={1} {...panelProps} />
  } else if (discovery.status === 'error' || discovery.status === 'forbidden') {
    const error =
      discovery.status === 'error'
        ? { response: discovery.error.status === null ? undefined : { status: discovery.error.status, data: { detail: discovery.error.message } } }
        : { response: { status: 403 } }
    body = (
      <DataUnavailable error={error} onRetry={discovery.status === 'error' ? discovery.retry : undefined} testId="explore-discovery-unavailable" />
    )
  } else if (discovery.status === 'never-had-data') {
    body = <EmptyState title="No test results yet" description="Ingest a run, and the explorer has something to split." />
  } else if (discovery.status === 'loading' || !facets) {
    body = (
      <p data-explore-loading="" className="py-10 text-center text-sm text-[var(--color-text-secondary)]">
        Finding the {DIMENSION_LABELS[config.facet].plural} with the most executions…
      </p>
    )
  } else if (facets.panels.length === 0) {
    body = (
      <EmptyState
        title={`No ${DIMENSION_LABELS[config.facet].plural} ran in the last ${config.days} days`}
        description="Widen the window, or pick another release in the top bar."
      />
    )
  } else {
    const facet = config.facet
    body = (
      // Keyed by the discovery: a new panel set mounts afresh, near the screen first.
      <div key={JSON.stringify(discoveryRequest)} data-explore-grid="" className="grid grid-cols-3 gap-4">
        {facets.panels.map((panel) => (
          <LazySection key={panel.key} minHeight={PANEL_HEIGHT + PANEL_CHROME} label={panel.key}>
            <ExplorePanelStable
              title={panel.label}
              facetKey={panel.key}
              params={base ? panelParams(base, config.metric, plan.panel, facet, panel.key) : null}
              panels={facets.panels.length}
              {...panelProps}
            />
          </LazySection>
        ))}
      </div>
    )
  }

  return (
    <PageShell className="space-y-4">
      <PageHeader
        title={EXPLORER_TITLE}
        subtitle={EXPLORER_SUBTITLE}
        actions={
          pinned && activeProjectId ? (
            <SavedViewsMenu
              page="explore"
              projectId={activeProjectId}
              current={current}
              extraFilters={{ explore: exploreViewFilters(config) }}
              linked={searchParams.has(EXPLORE_URL_KEYS.metric)}
              onApply={onApplyView}
            />
          ) : undefined
        }
      />
      <ExploreControls config={config} ctx={ctx} onChange={setConfig} />
      <p data-explore-summary="" className="text-sm text-[var(--color-text-secondary)]">
        {summary}
      </p>
      {notices.length > 0 ? (
        <ul data-explore-notices="" className="space-y-1 text-sm text-[var(--color-text-secondary)]">
          {notices.map((notice) => (
            <li key={notice} data-explore-notice="">
              {notice}
            </li>
          ))}
        </ul>
      ) : null}
      {body}
    </PageShell>
  )
}

/** A panel whose request object keeps its identity across re-renders. */
function ExplorePanelStable(props: ComponentProps<typeof ExplorePanel>): ReactElement {
  const params = useStableParams(props.params)
  return <ExplorePanel {...props} params={params} />
}
