/**
 * The test scatter section (VIZ-506): p95 duration x failure rate x volume,
 * one point per test, from `GET /api/v1/analytics/test-scatter`. OWNER: FK4.
 *
 * Behind `viz_chart_data_api` AND `viz_advanced_charts` (`useAdvancedRollout`,
 * the one seam): with either off it renders nothing and requests nothing. On,
 * it mounts behind its own `LazySection` (as `HeatmapSection` does), so no
 * request is made and no scatter engine is fetched before the reader is near.
 *
 * What it shows, and why each piece is there:
 *   - the frame (`ChartFrame`): title, the salient-quadrant takeaway, the
 *     summary, "View as table" (every test, from the C3 `points` series) and
 *     the export; the footer ALWAYS says which tests are not on the plot and
 *     why (the endpoint counts them by reason; a test with no timing cannot be
 *     placed on a duration axis, one with only skips has no rate — never 0%);
 *   - a scatter with nothing to place (every test excluded) states why in its
 *     body instead of drawing an empty plot;
 *   - the selection list in the frame, under the plot (the brush's accessible twin);
 *   - the rows panel: a point (click, Enter, the readout's button, a list
 *     row's "View rows") opens that test's executions, through the page URL
 *     (`rows=test~<fingerprint>`, `useDrillPath`), so Back closes it and a
 *     shared link reopens it.
 *
 * All Projects: the endpoint needs one project (a fingerprint is per
 * project), so the frame says so and nothing is requested.
 *
 * The data hook: `useChartData` with the catalogue's own key, fetcher and
 * validator (`catalogKey` / `catalogFetcher` / `catalogValidator`, exactly
 * what `useCatalogChartData('test-scatter')` uses) and ONE different
 * accessor: a scatter whose every test was EXCLUDED is not "empty" here. The
 * kit's emptiness rule turns no points into `filtered-empty` and drops the
 * series, and with it the exclusion counts that are the whole answer.
 */
import { useCallback, useMemo, useState, type ReactElement } from 'react'
import type { PointsChart } from '@/lib/viz/contracts'
import { useChartData, type ChartKey } from '@/hooks/useChartData'
import { ownedRows, useDrillPath } from '@/hooks/useDrillPath'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import ChartFrame from '@/components/charts/ChartFrame'
import TestScatter, { type ScatterSelectionOrigin } from '@/components/charts/TestScatter'
import ScatterSelectionList from '@/components/charts/ScatterSelectionList'
import { hasChartData, type ChartState } from '@/components/charts/chartStateCore'
import { catalogFetcher, catalogKey, catalogValidator, type CatalogResponse } from '@/components/charts/chartCatalogSources'
import { SCATTER_DENSE_POINTS } from '@/components/charts/engines/echarts/scatterOption'
import {
  X_FLOOR_MS,
  exclusionSentence,
  nothingPlacedSentence,
  scatterDescription,
  scatterTakeaway,
} from '@/components/charts/testScatter.model'
import type { MarkActivateHandler } from '@/components/charts/marks'
import { formatNumber } from '@/utils/formatters'
import LazySection from './LazySection'
import RowsPanel from './RowsPanel'
import { clampCatalogueDays, useCatalogueParams } from './catalogueScope'
import { useAdvancedRollout } from './useCatalogueRollout'
import { useEverHadRun } from './useEverHadRun'
import type { ScatterSectionProps } from './sectionContracts'
import {
  ALL_PROJECTS_REASON,
  NO_TESTS_MESSAGE,
  SCATTER_ACCESSORS,
  SCATTER_CHART_TYPE,
  SCATTER_HEIGHT,
  SCATTER_MIN_EXECUTIONS,
  SCATTER_PARAMS,
  SCATTER_TITLE,
  scatterFrameHeight,
  scatterRowsChart,
  scatterRowsSelectors,
} from './ScatterSection.model'

/** The frame's footer: what is not on the plot, the 1 ms floor, and "Dense" past the threshold (the gallery draws it too). */
export function ScatterFooter({ chart }: { chart: PointsChart }) {
  const floored = chart.x.scale === 'log' && chart.points.some((p) => p.x <= X_FLOOR_MS)
  return (
    <>
      {/* With nothing placed, the body's sentence already says what was left out: once (F-14). */}
      {chart.points.length > 0 ? <span data-scatter-excluded="">{exclusionSentence(chart.excluded, SCATTER_MIN_EXECUTIONS)}</span> : null}
      {floored ? <span data-scatter-floor="">{` A p95 under ${X_FLOOR_MS} ms is drawn at ${X_FLOOR_MS} ms.`}</span> : null}
      {chart.points.length > SCATTER_DENSE_POINTS ? (
        <span data-scatter-dense="">{` Dense: ${formatNumber(chart.points.length)} tests; drag to select a region to read them.`}</span>
      ) : null}
    </>
  )
}

function ScatterBody({ days, suiteFilter, placement }: ScatterSectionProps) {
  const allProjects = useProjectStore((s) => s.activeProjectId) === ALL_PROJECTS_ID
  const windowDays = clampCatalogueDays(days)
  const everHadData = useEverHadRun(!allProjects)
  const params = useCatalogueParams(days, suiteFilter, SCATTER_PARAMS)
  const rowsScope = useCatalogueParams(days, suiteFilter)
  const wanted = allProjects ? null : params
  const key = wanted === null ? null : catalogKey('test-scatter', wanted)
  const fetcher = useMemo(() => catalogFetcher('test-scatter', wanted ?? {}), [wanted])
  const fetched = useChartData<CatalogResponse<'test-scatter'>, ChartKey>(key, fetcher, {
    validate: catalogValidator('test-scatter'),
    everHadData,
    accessors: SCATTER_ACCESSORS,
  })
  const state: ChartState<CatalogResponse<'test-scatter'>> = allProjects
    ? { status: 'not-measured', reason: ALL_PROJECTS_REASON, meta: null }
    : fetched
  const chart = hasChartData(state) ? state.data.series : null
  const asOf = hasChartData(state) ? (state.meta?.as_of ?? null) : null

  // The selection belongs to the chart it was made on: a new response clears it.
  const [selection, setSelection] = useState<{ chart: PointsChart; indices: number[] } | null>(null)
  const selected = selection && selection.chart === chart ? selection.indices : null
  const onSelectionChange = useCallback(
    (indices: number[] | null, _origin: ScatterSelectionOrigin) => {
      setSelection(chart && indices ? { chart, indices } : null)
    },
    [chart],
  )

  // The page may hold another rows host (Suite detail: the test x run
  // heatmap also opens a lone `test`): the URL's owner entry says whose panel it is.
  const owner = `scatter-${placement}`
  const drill = useDrillPath()
  const openRows = useCallback((id: string) => drill.openRows(owner, [{ dimension: 'test', value: id }]), [drill, owner])
  const onMarkActivate = useCallback<MarkActivateHandler>((mark) => openRows(mark.value), [openRows])
  const markIntents = useCallback(() => ['rows'] as const, [])

  const rowsSelectors = scatterRowsSelectors(ownedRows(drill, owner))
  const rowsPoint = rowsSelectors.length && chart ? chart.points.find((p) => p.id === rowsSelectors[0].value) : undefined
  const rowsTitle = rowsPoint?.label ?? rowsSelectors[0]?.value ?? ''

  return (
    <div data-catalogue-section={`scatter-${placement}`} className="min-w-0">
      <ChartFrame
        title={SCATTER_TITLE}
        takeaway={chart ? scatterTakeaway(chart) : undefined}
        headingLevel={3}
        height={scatterFrameHeight(chart)}
        state={state}
        series={chart && chart.points.length > 0 ? chart : null}
        chartType={SCATTER_CHART_TYPE}
        axes={chart ? { x: chart.x.label, y: chart.y.label } : undefined}
        scopeLabel={`last ${windowDays} days`}
        emptyMessage={NO_TESTS_MESSAGE}
        footer={chart ? <ScatterFooter chart={chart} /> : undefined}
      >
        {chart === null ? null : chart.points.length === 0 ? (
          <p data-scatter-nothing="" className="py-8 text-center text-sm text-[var(--color-text-secondary)]">
            {nothingPlacedSentence(chart.excluded, SCATTER_MIN_EXECUTIONS)}
          </p>
        ) : (
          <TestScatter
            data={chart}
            description={scatterDescription(SCATTER_TITLE, chart)}
            height={SCATTER_HEIGHT}
            onSelectionChange={onSelectionChange}
            onMarkActivate={onMarkActivate}
            markIntents={markIntents}
          />
        )}
        {/* In the card, on its padding (F-08): the selection is read where it was made. */}
        {chart && selected !== null ? (
          <ScatterSelectionList chart={chart} indices={selected} onViewRows={(index) => openRows(chart.points[index].id)} />
        ) : null}
      </ChartFrame>
      <RowsPanel
        selectors={rowsSelectors}
        chart={scatterRowsChart(placement)}
        scope={rowsScope}
        title={rowsTitle}
        expected={rowsPoint ? { y: rowsPoint.y, n: rowsPoint.n, asOf } : null}
        onClose={drill.closeRows}
      />
    </div>
  )
}

/**
 * The drawn section's height, px, for the lazy placeholder: the plot plus the
 * frame's header and footer, the selection toolbar, the keyboard hint and the
 * quadrant key, MEASURED at 1280 with both flags on, on Failures and on Suite
 * detail alike (R2-B F-15: 543 px; the old estimate, the plot + 260 = 580,
 * moved the page up 37 px as the scatter mounted).
 */
export const SCATTER_SECTION_MIN_HEIGHT = 543

export function ScatterSection(props: ScatterSectionProps): ReactElement | null {
  const on = useAdvancedRollout()
  if (!on) return null
  return (
    <LazySection label={`scatter-${props.placement}`} minHeight={SCATTER_SECTION_MIN_HEIGHT}>
      <ScatterBody {...props} />
    </LazySection>
  )
}

export default ScatterSection
