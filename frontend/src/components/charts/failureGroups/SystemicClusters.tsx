/**
 * The "Systemic flake clusters" tab (VIZ-504 / VIZ-207): tests that fail
 * TOGETHER across runs, as the nightly sweep found them — the first UI for
 * `/analytics/systemic-clusters` (plan F5).
 *
 *   title     "Tests that fail together (last 60 days, whole project)": the
 *             clusters are a project-wide, 60-day computation, NOT the page's
 *             window; the release / suite chips only choose which members are
 *             listed (the server's `scope.note`, shown in the footer). Never
 *             called "AI": they come from co-failure counts.
 *   empty     a normal, frequent answer: the server's own sentence, as a
 *             result, never as an error or "no data yet"
 *   identity  keyed by `membership_key`, and the footer says what that key
 *             promises (the exact member set)
 *
 * Asked only while the tab is open (the tab panel mounts it), through
 * `chartGet` (abortable, toast-free, inside the chart request cap), checked by
 * `validateClustersResponse` before anything is drawn. All Projects asks
 * nothing: clusters exist per project.
 */
import { useCallback, useMemo } from 'react'
import type { SeriesChart } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import ChartFrame, { type ChartHeadingLevel } from '../ChartFrame'
import type { ChartState } from '../chartStateCore'
import type { CatalogParams } from '../chartCatalogSources'
import { packLayout } from './packLayout'
import GroupPlot from './GroupPlot'
import { EMPTY_ANSWER_HEIGHT, WRAP_ANYWHERE, type PlotBox, type PlotItem, type PlotLayout } from './plot.model'
import {
  causeLabel,
  clustersTitle,
  clusterTipContent,
  EMPTY_CLUSTERS_FALLBACK,
  formatCohesion,
  IDENTITY_NOTE,
  type SystemicCluster,
  type SystemicClustersResponse,
} from './systemicClusters.model'
import { useSystemicClusters } from './useSystemicClusters'

export const ALL_PROJECTS_REASON = 'systemic clusters are computed per project; choose a project to see them'

/** The cluster sizes as a category series: the table view and the CSV. */
function clustersSeries(clusters: readonly SystemicCluster[]): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['test'],
    x_type: 'category',
    series: [{ key: 'tests', label: 'Tests', points: clusters.map((c) => ({ x: c.key, y: c.size, n: c.size })) }],
    x_labels: Object.fromEntries(clusters.map((c) => [c.key, c.label])),
  }
}

/**
 * The members toggle: "Members (2 of 14)" when the server listed fewer members
 * than the cluster has tests (R2-B F-09: "14 tests" beside "Members (2)" read as
 * a contradiction), else "Members (2)".
 */
function membersLabel(cluster: SystemicCluster): string {
  const listed = formatNumber(cluster.members.length)
  return cluster.members.length < cluster.size ? `Members (${listed} of ${formatNumber(cluster.size)})` : `Members (${listed})`
}

export interface SystemicClustersProps {
  /** The page's scope without `days` (clusters have no window). `null` while unresolved. */
  params: CatalogParams | null
  /** All Projects: nothing is asked. */
  allProjects: boolean
  headingLevel: ChartHeadingLevel
  height?: number
  /** The plot's width when known (tests, gallery). */
  plotWidth?: number
  /** A settled state to draw instead of fetching (the chart gallery). Absent: the list is fetched. */
  state?: ChartState<SystemicClustersResponse>
  /** The frame's title; default the window's sentence (`clustersTitle`). A page with several frames names each. */
  title?: string
}

export default function SystemicClusters({
  params,
  allProjects,
  headingLevel,
  height = 300,
  plotWidth,
  state: given,
  title: titleProp,
}: SystemicClustersProps) {
  // With a given state nothing is asked (the gallery has no network).
  const fetched = useSystemicClusters(allProjects || given ? null : params)
  const state = given ?? fetched
  const frameState: ChartState<SystemicClustersResponse> = allProjects
    ? { status: 'not-measured', reason: ALL_PROJECTS_REASON, meta: null }
    : state
  const body = state.status === 'ready' || state.status === 'truncated' ? state.data : null
  const clusters = useMemo(() => body?.clusters ?? [], [body])
  const series = useMemo(() => (clusters.length > 0 ? clustersSeries(clusters) : null), [clusters])
  const items = useMemo<PlotItem[]>(
    () =>
      clusters.map((cluster, index) => ({
        id: cluster.key,
        rank: index + 1,
        label: cluster.label,
        category: null,
        tip: clusterTipContent(cluster, index + 1),
        mark: null,
      })),
    [clusters],
  )
  const layout = useCallback(
    (box: PlotBox): PlotLayout => ({
      nodes: packLayout(
        clusters.map((c) => ({ id: c.key, value: c.size })),
        Math.min(box.width, box.height),
      ).circles,
    }),
    [clusters],
  )
  const title = titleProp ?? clustersTitle(clusters)
  const takeaway =
    clusters.length > 0
      ? `${formatNumber(clusters.length)} cluster${clusters.length === 1 ? '' : 's'}; the largest has ${formatNumber(clusters[0].size)} tests`
      : undefined

  return (
    <ChartFrame
      title={title}
      takeaway={takeaway}
      headingLevel={headingLevel}
      state={frameState}
      height={body && clusters.length === 0 ? EMPTY_ANSWER_HEIGHT : height}
      series={series}
      chartType="Packed circles"
      axes={{ x: 'Cluster', y: 'Tests' }}
      footer={
        body ? (
          <>
            <span data-clusters-identity="">{IDENTITY_NOTE}</span>
            {body.scopeNote && <span data-clusters-scope="">{body.scopeNote}</span>}
          </>
        ) : undefined
      }
    >
      {body && clusters.length === 0 ? (
        <p data-clusters-empty="" className="m-0 py-8 text-center text-sm text-[var(--color-text-secondary)]">
          {body.emptyIsNormal ?? EMPTY_CLUSTERS_FALLBACK}
        </p>
      ) : body ? (
        <div className="flex min-w-0 flex-col gap-4">
          <GroupPlot
            kind="clusters"
            items={items}
            layout={layout}
            shape="square"
            height={height}
            description={`Systemic flake clusters as circles sized by tests: ${formatNumber(clusters.length)} clusters. Arrow keys move through them largest first.`}
            width={plotWidth}
            idleText="Point at a circle, or focus the chart and use the arrow keys, to read a cluster here."
          />
          <ol data-clusters-list="" className="m-0 flex list-none flex-col gap-2 p-0 text-xs text-[var(--color-text)]">
            {clusters.map((cluster, index) => (
              <li
                key={cluster.key}
                data-cluster-key={cluster.key}
                className="rounded border border-[var(--color-border)] px-3 py-2"
              >
                <p className="m-0 font-semibold" style={WRAP_ANYWHERE}>
                  #{index + 1} {cluster.label}
                </p>
                <p className="m-0 text-[var(--color-text-secondary)]">
                  {causeLabel(cluster.causeFamily)} · {formatNumber(cluster.size)} tests · cohesion{' '}
                  {formatCohesion(cluster.cohesion)} · {formatNumber(cluster.coFailureRuns)} runs failing together
                </p>
                {cluster.members.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer">{membersLabel(cluster)}</summary>
                    <ul className="m-0 mt-1 list-none p-0">
                      {cluster.members.map((member) => (
                        <li key={member.fingerprint} data-cluster-member="" style={WRAP_ANYWHERE}>
                          {member.name}
                          {member.failureRuns !== null && (
                            <span className="text-[var(--color-text-secondary)]">
                              {' '}
                              ({formatNumber(member.failureRuns)} failing runs)
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </ChartFrame>
  )
}
