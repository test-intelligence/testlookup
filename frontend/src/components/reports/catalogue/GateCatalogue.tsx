/**
 * The Release gate's "Context" group (Wave 2.6, VIZ-408; plan 2.2 and 2.5).
 *
 * Two charts that EXPLAIN a stored verdict and must never look like they
 * decide one:
 *
 *   - **Pass rate by release**: the run's release beside up to four of the
 *     project's most recent other releases, aligned on each release's start
 *     (VIZ-404), from ONE `chart-data` request. Live data.
 *   - **Failure cluster share**: the STORED decision's own `cluster_insights`,
 *     drawn as whatever the registry picks — a donut at five clusters or
 *     fewer, a ranked bar past it (`BreakdownChart`).
 *
 * The rules this file keeps (plan 2.5), each pinned by a test:
 *
 *   1. Placement: the page mounts the group after the recommendation card and
 *      the evidence it rests on, never inside the card or beside the ring.
 *   2. No decision colour: no target line, no `rateTarget`, no verdict tint;
 *      series colours are the categorical palette, never GO / NO-GO tokens.
 *   3. Every chart carries a caption saying the verdict is stored and not
 *      computed from it (and, for the live chart, as of when).
 *   4. One-way data flow: the props are the run id, the build label and the
 *      stored clusters — never the recommendation, the risk score or an
 *      override — so nothing here CAN restyle itself by verdict.
 *   5. The populations are named: executions per release here, one run's
 *      stored snapshot in the verdict.
 *   6. Never blank: a failing request is the frame's own error state, and a
 *      comparison with fewer than two releases is one sentence, never an
 *      empty frame.
 *
 * The run's release comes from `GET /runs/{id}` read ONCE (`useGateRun`), not
 * from `useRun`, which polls every 5 s: on a page of stored verdicts that is
 * 720 requests an hour per open tab for a value that does not change.
 */
import { useId, useMemo } from 'react'
import useSWR from 'swr'
import { BreakdownChart } from '@/components/charts/BarChart'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import { hasChartData, readyState, type ChartState } from '@/components/charts/chartState'
import { buildMultiSeriesModel, multiSeriesInputFromChartData } from '@/components/charts/multiSeriesModel'
import { ownLabel } from '@/components/charts/chartText'
import { useCatalogChartData } from '@/components/charts/chartCatalogSources'
import { chartGet } from '@/services/chartApi'
import { useReleases } from '@/hooks/useReleases'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import LazySection from './LazySection'
import { catalogueParams } from './catalogueScope'
import { chartResponseFromClusters, type ClusterRow } from './catalogueAdapters'
import { GateContextHeader } from './GateContextHeader'
import { hasClusterShare } from './gateContextWords'
import {
  CLUSTERS_TITLE,
  liveCaption,
  measuredSeriesCount,
  NOT_ATTRIBUTED_NOTE,
  planReleaseComparison,
  readGateRun,
  RELEASE_WINDOW_DAYS,
  RELEASES_PLACEHOLDER_HEIGHT,
  RELEASES_POPULATION,
  RELEASES_TITLE,
  sideRequestState,
  storedCaption,
  tooFewNote,
  type GateRun,
} from './GateCatalogue.model'

const PLOT_HEIGHT = 280

// ── The run, read once ───────────────────────────────────────────────────────

/**
 * The run's release and project, fetched ONCE: no refresh interval, no
 * revalidation on focus or reconnect. Through `chartGet`, so a failure is
 * this chart's error state and never a global toast (VIZ-107), and it counts
 * against the page's chart-request cap.
 */
function useGateRun(runId: string) {
  return useSWR<GateRun>(
    ['release-gate-run', runId],
    async ([, id]: readonly [string, string]) =>
      readGateRun((await chartGet(`/api/v1/runs/${encodeURIComponent(id)}`)).data),
    {
      refreshInterval: 0,
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
      revalidateIfStale: false,
      shouldRetryOnError: false,
    },
  )
}

// ── Pass rate by release ─────────────────────────────────────────────────────

function Note({ children, kind }: { children: string; kind: string }) {
  return (
    <p data-gate-note={kind} className="text-xs text-[var(--color-text-secondary)]">
      {children}
    </p>
  )
}

export interface ReleaseComparisonProps {
  runId: string
  build: string
}

export function ReleaseComparison({ runId, build }: ReleaseComparisonProps) {
  const run = useGateRun(runId)
  const releaseList = useReleases(undefined, { cached: true })
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectName = useProjectStore((s) => s.activeProject?.name ?? null)

  const projectId =
    run.data?.projectId ?? (activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null)
  const listItems = releaseList.data?.items
  const plan = useMemo(
    () => (run.data && listItems ? planReleaseComparison(run.data, listItems, projectId) : null),
    [run.data, listItems, projectId],
  )
  const params = useMemo(
    () =>
      plan && plan.releaseIds.length >= 2
        ? catalogueParams({
            projectId,
            allProjects: false,
            days: RELEASE_WINDOW_DAYS,
            releaseIds: plan.releaseIds,
            extra: { metric: 'pass_rate', group_by: ['day', 'release'] },
          })
        : null,
    [plan, projectId],
  )
  // A decision exists, so this project has had runs: never "never had data".
  const chart = useCatalogChartData('chart-data', { params, everHadData: true })

  const model = useMemo(() => {
    if (!plan || !hasChartData(chart) || chart.data.series.kind !== 'series') return null
    if (measuredSeriesCount(chart.data) < 2) return null
    return buildMultiSeriesModel({
      series: multiSeriesInputFromChartData(chart.data.series),
      metric: { kind: 'rate', title: 'Pass rate %' },
      meta: chart.data.meta,
      alignment: 'release-start',
      starts: plan.starts,
      seriesNoun: 'releases',
    })
  }, [plan, chart])

  const sideError = run.error ?? releaseList.error
  // Too few releases, known from the list (no request made) or from the answer.
  const tooFew: number | null = (() => {
    if (sideError || !plan) return null
    if (plan.releaseIds.length < 2) return plan.releaseIds.length
    if (chart.status === 'filtered-empty' || chart.status === 'not-measured') return 0
    if (hasChartData(chart) && measuredSeriesCount(chart.data) < 2) return measuredSeriesCount(chart.data)
    return null
  })()

  if (tooFew !== null && plan) {
    return (
      <div data-catalogue-section="gate-releases" data-gate-releases="too-few" className="card min-w-0">
        <Note kind="too-few">{tooFewNote(tooFew, plan.attributed)}</Note>
      </div>
    )
  }

  const retrySide = () => {
    if (run.error) void run.mutate()
    if (releaseList.error) void releaseList.mutate()
  }
  const state: ChartState<unknown> = sideError ? sideRequestState(sideError, retrySide) : plan ? chart : { status: 'loading' }
  const asOf = hasChartData(chart) ? (chart.data.meta?.as_of ?? null) : null
  const scopeLabel = [
    projectName ? `project ${projectName}` : null,
    plan ? `releases ${plan.releaseIds.map((id) => ownLabel(plan.names, id)).join(', ')}` : null,
    `last ${RELEASE_WINDOW_DAYS} days`,
    'live data',
  ]
    .filter(Boolean)
    .join('; ')

  return (
    <div data-catalogue-section="gate-releases" className="min-w-0 space-y-2">
      {/* The dated caption is in the frame's own footer (rule 3, "in every
          frame"), not under its border (R2-21). */}
      <MultiSeriesChartFrame
        title={RELEASES_TITLE}
        takeaway={RELEASES_POPULATION}
        headingLevel={3}
        state={state}
        model={model}
        height={PLOT_HEIGHT}
        scopeLabel={scopeLabel}
        footer={<span data-gate-caption="live">{liveCaption(asOf, build)}</span>}
      />
      {plan && !plan.attributed && <Note kind="not-attributed">{NOT_ATTRIBUTED_NOTE}</Note>}
    </div>
  )
}

// ── Failure cluster share ────────────────────────────────────────────────────

export interface ClusterShareProps {
  clusters: readonly ClusterRow[]
  build: string
}

export function ClusterShare({ clusters, build }: ClusterShareProps) {
  const state = useMemo(() => readyState(chartResponseFromClusters(clusters)), [clusters])
  return (
    <div data-catalogue-section="gate-clusters" className="min-w-0 space-y-2">
      {/* The dated caption is in the frame's own footer, donut or bar (R2-21). */}
      <BreakdownChart
        title={CLUSTERS_TITLE}
        state={state}
        headingLevel={3}
        height={PLOT_HEIGHT}
        dimension="Failure cluster"
        valueAxisLabel="Failed tests"
        scopeLabel={`build ${build}; stored decision`}
        footer={<span data-gate-caption="stored">{storedCaption(build)}</span>}
      />
    </div>
  )
}

// ── The group ────────────────────────────────────────────────────────────────

/**
 * Rule 4: the ONLY inputs. The recommendation, the risk score and the
 * override are deliberately not here, and the page test pins these three keys.
 */
export interface GateCatalogueProps {
  runId: string
  /** The build label the captions name (the decision's build number, else the run id). */
  build: string
  /** The stored decision's `cluster_insights`, as stored. */
  clusters: readonly ClusterRow[]
}

export default function GateCatalogue({ runId, build, clusters }: GateCatalogueProps) {
  const headingId = useId()
  // A cluster share needs something to share: with no stored cluster of a
  // positive size there is no chart (and no empty frame blaming filters).
  const both = hasClusterShare(clusters)
  return (
    <section data-catalogue-section="gate-context" aria-labelledby={headingId} className="space-y-3">
      {/* The same heading and note the page's Suspense fallback draws. */}
      <GateContextHeader headingId={headingId} build={build} />
      {/* One column at every width (R2-9): the release comparison is the chart
          that needs the width, and beside the donut at xl it got half the
          column and left a 260 px hole under the donut. The cluster share is
          its own row below. Every cell is `min-w-0`, so a chart never pushes
          the page wider than its column. */}
      <div data-gate-context-grid="" className="grid grid-cols-1 gap-4">
        <LazySection minHeight={RELEASES_PLACEHOLDER_HEIGHT} label="gate-releases">
          <ReleaseComparison runId={runId} build={build} />
        </LazySection>
        {both && <ClusterShare clusters={clusters} build={build} />}
      </div>
    </section>
  )
}
