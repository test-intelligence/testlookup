/**
 * The failure groups + systemic clusters section (VIZ-504), on Failure
 * analysis. OWNER: FK3. Mounted by `FailuresAdvanced` (lazy, near the reader);
 * it reads no flag (Phase D, S5: the advanced charts are on everywhere).
 *
 * Two tabs (`@radix-ui/react-tabs`):
 *
 *   Failure groups          `/analytics/failure-groups?include=edges` in the
 *                           page's scope (window clamped to 90 d by
 *                           `useCatalogueParams`), drawn by
 *                           `FailureGroupsFrame`: bubbles, related groups
 *                           (offered only when linked), ranked table; a
 *                           group's details in a side panel
 *   Systemic flake clusters `/analytics/systemic-clusters`, asked only when the
 *                           tab is opened (Radix mounts the open panel only)
 *
 * Activating a group (click, Enter, the readout's buttons, its name in the
 * table): "drill" opens its details here; "rows" opens the shared rows panel on
 * `error_signature=<signature>` with `metric=failures` (BE4: a group drill sends
 * failures), the population the group counted — the rows endpoint selects by
 * the SAME SQL signature, so its total is the group's failures. The rows panel
 * state is in the URL (`rows=error_signature~…`, C5), so Back closes it and a
 * shared link reopens it; the group panel is local (Failures' drill host is
 * the ladder, plan 3.4.2: one drill path per page).
 *
 * A group body with no group but failures (every message seen once) is DRAWN
 * — its roll-ups say how many — rather than reported as "no data".
 */
import { useCallback, useMemo, useState, type ReactElement } from 'react'
import * as Tabs from '@radix-ui/react-tabs'
import type { GraphChart } from '@/lib/viz/contracts'
import { useChartData, type ChartKey } from '@/hooks/useChartData'
import { ownedRows, useDrillPath } from '@/hooks/useDrillPath'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import {
  catalogFetcher,
  catalogKey,
  catalogValidator,
  type CatalogParams,
  type CatalogResponse,
} from '@/components/charts/chartCatalogSources'
import type { ChartAccessors } from '@/components/charts/chartState'
import FailureGroupsFrame from '@/components/charts/FailureGroupsFrame'
import FailureGroupPanel from '@/components/charts/failureGroups/FailureGroupPanel'
import { failureGroupsModel, ownRows, type FailureGroup } from '@/components/charts/failureGroups/failureGroups.model'
import SystemicClusters from '@/components/charts/failureGroups/SystemicClusters'
import type { MarkActivateHandler, MarkIntentsFor } from '@/components/charts/marks'
import { useCatalogueParams } from './catalogueScope'
import RowsPanel from './RowsPanel'
import type { FailureGroupsSectionProps } from './sectionContracts'
import { useEverHadRun } from './useEverHadRun'

export const FAILURE_GROUPS_TITLE = 'Failures grouped by error message'
export const GROUPS_TAB = 'Failure groups'
export const CLUSTERS_TAB = 'Systemic flake clusters'

/** The groups request's own parameter: the links, so "Related groups" needs no second request. */
const FAILURE_GROUPS_PARAMS: CatalogParams = { include: 'edges' }

/** The rows behind a group: its failures, selected by signature (BE4 notes). */
const ROWS_CHART = { metric: 'failures', groupBy: ['error_signature'] } as const
/** This section's rows owner (`rows=by~failures-groups`): the ladder on the same page reads the same key. */
const ROWS_OWNER = 'failures-groups'

const INTENTS: MarkIntentsFor = () => ['drill', 'rows']

type GroupsResponse = CatalogResponse<'failure-groups'>

/** `total_failures`, read defensively off the graph (an additive key), or 0. */
function totalFailures(series: GraphChart): number {
  const total = (series as unknown as { total_failures?: unknown }).total_failures
  return typeof total === 'number' && Number.isFinite(total) ? total : 0
}

/**
 * Empty = nothing failed at all. A body with failures but no group (all
 * singletons or no message) is NOT empty: the frame draws its roll-ups.
 */
const GROUP_ACCESSORS: ChartAccessors<GroupsResponse> = {
  meta: (value) => value.meta,
  isEmpty: (value) => value.series.nodes.length === 0 && totalFailures(value.series) === 0,
  shown: (value) => value.series.nodes.length,
}

const TAB =
  'px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const TAB_ACTIVE = `${TAB} bg-[var(--color-bg-hover)] font-semibold`

function FailureGroupsBody({ days, suiteFilter }: FailureGroupsSectionProps) {
  const allProjects = useProjectStore((s) => s.activeProjectId === ALL_PROJECTS_ID)
  const everHadData = useEverHadRun(true)
  const params = useCatalogueParams(days, suiteFilter, FAILURE_GROUPS_PARAMS)
  // The rows and the clusters take the page's scope WITHOUT the groups' own parameter.
  const scope = useCatalogueParams(days, suiteFilter)
  const clusterParams = useMemo(() => {
    if (scope === null) return null
    const rest: CatalogParams = { ...scope }
    delete rest.days // clusters have no window: the sweep's 60 days, project-wide
    return rest
  }, [scope])

  const key: ChartKey | null = params === null ? null : catalogKey('failure-groups', params)
  const fetcher = useMemo(() => catalogFetcher('failure-groups', params ?? {}), [params])
  const state = useChartData<GroupsResponse, ChartKey>(key, fetcher, {
    validate: catalogValidator('failure-groups'),
    everHadData,
    accessors: GROUP_ACCESSORS,
  })

  const series = state.status === 'ready' || state.status === 'truncated' ? state.data.series : null
  const asOf = state.status === 'ready' || state.status === 'truncated' ? (state.meta?.as_of ?? null) : null
  const model = useMemo(() => (series ? failureGroupsModel(series) : null), [series])
  const groupOf = useCallback((id: string) => model?.groups.find((group) => group.id === id) ?? null, [model])

  const [tab, setTab] = useState('groups')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = selectedId === null ? null : groupOf(selectedId)
  const drill = useDrillPath()
  const rows = ownRows(ownedRows(drill, ROWS_OWNER))
  const rowsGroup = rows.length > 0 ? groupOf(rows[0].value) : null

  const openRows = useCallback(
    (signature: string) => {
      setSelectedId(null)
      drill.openRows(ROWS_OWNER, [{ dimension: 'error_signature', value: signature }])
    },
    [drill],
  )
  const onMarkActivate = useCallback<MarkActivateHandler>(
    (mark, intent) => {
      if (intent === 'rows') openRows(mark.value)
      else setSelectedId(mark.value)
    },
    [openRows],
  )
  const onOpenGroup = useCallback((group: FailureGroup) => setSelectedId(group.id), [])

  return (
    <section data-catalogue-section="failures-groups" aria-label={FAILURE_GROUPS_TITLE} className="min-w-0">
      <Tabs.Root value={tab} onValueChange={setTab} className="flex min-w-0 flex-col gap-2">
        <Tabs.List
          aria-label={FAILURE_GROUPS_TITLE}
          className="inline-flex self-start overflow-hidden rounded border border-[var(--color-border-light)]"
        >
          <Tabs.Trigger value="groups" className={tab === 'groups' ? TAB_ACTIVE : TAB}>
            {GROUPS_TAB}
          </Tabs.Trigger>
          <Tabs.Trigger value="clusters" className={tab === 'clusters' ? TAB_ACTIVE : TAB}>
            {CLUSTERS_TAB}
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content
          value="groups"
          className="min-w-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        >
          <FailureGroupsFrame
            title={FAILURE_GROUPS_TITLE}
            headingLevel={3}
            state={state}
            scopeLabel={`last ${params?.days ?? days} days`}
            onOpenGroup={onOpenGroup}
            selectedId={selectedId}
            onMarkActivate={onMarkActivate}
            markIntents={INTENTS}
          />
        </Tabs.Content>
        <Tabs.Content
          value="clusters"
          className="min-w-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        >
          <SystemicClusters params={clusterParams} allProjects={allProjects} headingLevel={3} />
        </Tabs.Content>
      </Tabs.Root>
      <FailureGroupPanel
        group={selected}
        trendGrain={model?.trendGrain ?? null}
        onClose={() => setSelectedId(null)}
        onViewRows={(group) => openRows(group.id)}
      />
      <RowsPanel
        selectors={rows}
        chart={ROWS_CHART}
        scope={scope}
        title={rowsGroup ? rowsGroup.label : rows.length > 0 ? rows[0].value : ''}
        expected={rowsGroup ? { y: rowsGroup.failureCount, n: null, asOf } : null}
        onClose={drill.closeRows}
      />
    </section>
  )
}

export function FailureGroupsSection(props: FailureGroupsSectionProps): ReactElement {
  return <FailureGroupsBody {...props} />
}

export default FailureGroupsSection
