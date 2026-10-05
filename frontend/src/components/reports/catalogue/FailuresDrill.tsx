/**
 * The drill ladder (VIZ-602 / 603) on the Failure analysis page: "Results by
 * suite", then a suite's statuses or a status's suites, then the failing tests
 * of one suite, then the executions behind one test. OWNER: FK5.
 *
 * It reads no flag (Phase D, S5). It mounts behind its own `LazySection`, so
 * nothing is asked before the reader is near.
 *
 * WHERE THE READER IS lives in the address bar (C5, `useDrillPath`): the path
 * `drill=suite~payments&drill=status~failed` names the level, so Back walks up
 * a level, Forward down again, and a shared link opens the same level. Each
 * level is ONE chart-data request (cached like every catalogue request). The
 * path is validated before use: what the C5 rules or this ladder cannot apply
 * is cut, and the page says so (the report filter bar's notice when the page
 * has one, under the breadcrumb otherwise). The path is never written into a
 * saved view: nothing here touches `useAnalyticsView`.
 *
 * WHAT A MARK DOES (OD-2): a click or Enter drills (a stacked segment drills
 * into its suite AND status in one step: the AC's "payments failed segment");
 * Shift-, Ctrl- or Cmd-click, Shift+Enter or the readout's button "Filter page
 * by this" sets the page's "Test suite" select to that suite (`useCrossFilter`,
 * through the page's `PageSuiteTargetContext`; absent without one); "View
 * rows" opens the executions behind the mark (`RowsPanel`), at any level and
 * the only action at the leaf.
 *
 * FOCUS: a drill, and a breadcrumb link, move focus to the chart once the new
 * level is drawn (EPIC "focus moves to the chart"); Back and Forward do not
 * (the reader's focus is on the browser's own control).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import Breadcrumbs, { type BreadcrumbItem } from '@/components/ui/Breadcrumbs'
import BarChart from '@/components/charts/BarChart'
import { MARK_KIT } from '@/components/charts/markKit'
import { useCatalogChartData } from '@/components/charts/chartCatalogSources'
import { hasChartData, type ChartResponse, type ChartState } from '@/components/charts/chartStateCore'
import type { ChartMark, MarkIntent } from '@/components/charts/marks'
import { readDrillLevels, encodeDrillLevel, ownedRows, useDrillPath, writeDrillParams } from '@/hooks/useDrillPath'
import { useCrossFilter } from '@/hooks/useCrossFilter'
import { useMultiFiltersEnabled } from '@/store/multiFiltersFlag'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'
import type { DrillLevel } from '@/lib/viz/contracts'
import {
  DRILL_NOTICE_REASON,
  ROOT_CRUMB,
  answersLevel,
  drillLevels,
  labelKey,
  ladderFromPath,
  ladderIntents,
  learnSuiteLabels,
  levelChart,
  levelRequest,
  levelSuite,
  ownRows,
  rowsChart,
  rowsSelectors,
  rowsTitle,
  stepLabel,
} from './FailuresDrill.model'
import LazySection from './LazySection'
import RowsPanel from './RowsPanel'
import { selectorsKey, type RowsExpectation } from './RowsPanel.model'
import { clampCatalogueDays, useCatalogueParams } from './catalogueScope'
import { useEverHadRun } from './useEverHadRun'
import type { FailuresDrillProps } from './sectionContracts'

/** The ladder's first level: the one a suite's label is learned from. */
const ROOT_LEVEL = { kind: 'suites' } as const

/** The plot's height, px. */
export const DRILL_HEIGHT = 280
/**
 * The lazy placeholder's height: what the ladder DRAWS (frame, breadcrumb, the
 * first level's plot and footer), measured at 1280 (R2-B
 * F-15, `docs/viz-work/w3/r2b/diag-failures.jsonl` and X3's `x3/groups-win.jsonl`:
 * 372 px). The old estimate (the plot + 200 = 480) shrank by 108 px inside the
 * viewport as the section mounted, moving everything below it.
 */
export const DRILL_SECTION_MIN_HEIGHT = 372

/** A search string that sets the drill path (and closes the rows panel); `'?'` for none, so a link to the root navigates. */
/** This section's rows owner (`rows=by~failures-drill`): the groups and the scatter on this page read the same key. */
const ROWS_OWNER = 'failures-drill'

function drillSearch(search: string, path: readonly DrillLevel[]): string {
  const text = writeDrillParams(search, { path, rows: [] }).toString()
  return text === '' ? '?' : `?${text}`
}

function FailuresDrillBody({ days, suiteFilter }: FailuresDrillProps) {
  const location = useLocation()
  const [, setSearchParams] = useSearchParams()
  const drill = useDrillPath()
  const cross = useCrossFilter()
  const multi = useMultiFiltersEnabled()
  const windowDays = clampCatalogueDays(days)

  const suiteKey = JSON.stringify(suiteFilter ?? null)
  const ladder = useMemo(
    () => ladderFromPath(drill.path, JSON.parse(suiteKey) as string | string[] | null),
    [drill.path, suiteKey],
  )
  const { level, used } = ladder
  const usedPath = useMemo(() => drill.path.slice(0, used), [drill.path, used])
  const levelKey = JSON.stringify(usedPath)

  // The level's question: its own suite (when it has one) REPLACES the page's suite filter.
  const suite = levelSuite(level)
  const params = useCatalogueParams(days, suite ?? suiteFilter, levelRequest(level))
  const rowsScope = useCatalogueParams(days, suite ?? suiteFilter)
  const everHadData = useEverHadRun(true)

  // What each level was called when the reader saw it, for the title, the breadcrumb and the rows title.
  const [labels, setLabels] = useState<Record<string, string>>({})
  // F-10: a path names its suite by KEY ("payments"); a reader knows it by its label ("Payments"). A
  // shared link skipped the level that taught it, so the root level's own read (the request Back to
  // it makes anyway, cached) supplies it. Asked before this level's, and only while a suite on the
  // path is unnamed; a suite the root does not list keeps its key.
  const pathSuite = usedPath.find((step) => step.dimension === 'suite') ?? null
  const unnamed = pathSuite !== null && !Object.prototype.hasOwnProperty.call(labels, labelKey(pathSuite))
  const rootParams = useCatalogueParams(days, suiteFilter, levelRequest(ROOT_LEVEL))
  const root = useCatalogChartData('chart-data', { params: unnamed ? rootParams : null, everHadData })
  const state = useCatalogChartData('chart-data', { params, everHadData })

  // The previous level's response stays on screen while this one loads; it is
  // not this level's, and read as this level its keys would drill elsewhere.
  const drawn = hasChartData(state) ? state : null
  const current = drawn && answersLevel(drawn.data.series.kind === 'series' ? drawn.data.series : null, drawn.meta, level) ? drawn : null
  const frameState: ChartState<ChartResponse> = drawn && !current ? { status: 'loading' } : state
  const asOf = current?.meta?.as_of ?? null

  const rootSeries = hasChartData(root) && root.data.series.kind === 'series' ? root.data.series : null
  const learned = useMemo(
    () => ({
      ...learnSuiteLabels(rootSeries),
      ...learnSuiteLabels(current && current.data.series.kind === 'series' ? current.data.series : null),
    }),
    [current, rootSeries],
  )
  if (Object.entries(learned).some(([key, label]) => labels[key] !== label)) setLabels({ ...labels, ...learned })
  const learn = useCallback((mark: ChartMark) => {
    setLabels((known) => {
      const key = labelKey({ dimension: mark.dimension, value: mark.value })
      return known[key] === mark.label ? known : { ...known, [key]: mark.label }
    })
  }, [])

  // Drill from the APPLIED path (a cut level in the URL is replaced, not appended to): one history push.
  const drillTo = useCallback(
    (levels: readonly DrillLevel[]) => {
      const { levels: path } = readDrillLevels([...usedPath, ...levels].map(encodeDrillLevel))
      setSearchParams((current) => writeDrillParams(current, { path, rows: [] }))
    },
    [setSearchParams, usedPath],
  )

  // Focus follows a drill or a breadcrumb link to the chart, once its level is drawn.
  const sectionRef = useRef<HTMLDivElement | null>(null)
  const pendingFocus = useRef(false)
  useEffect(() => {
    if (!pendingFocus.current || !current) return
    const root = sectionRef.current
    const target = root?.querySelector<HTMLElement>('[data-chart-cursor]') ?? root?.querySelector<HTMLElement>('[data-chart-body]')
    if (target) {
      target.focus()
      pendingFocus.current = false
    }
  }, [current, levelKey])

  // The rows panel: this level's selection only (another section on the page owns any other).
  const [rowsMark, setRowsMark] = useState<ChartMark | null>(null)
  const selectors = ownRows(level, ownedRows(drill, ROWS_OWNER))
  const expected: RowsExpectation | null =
    rowsMark && selectors.length > 0 && selectorsKey(rowsSelectors(level, rowsMark)) === selectorsKey(selectors)
      ? { y: rowsMark.y, n: rowsMark.n, asOf }
      : null

  const offersFilter = cross.offers
  const markIntents = useCallback((mark: ChartMark) => ladderIntents(level, mark, offersFilter(mark)), [level, offersFilter])
  const { openRows } = drill
  const applyFilter = cross.apply
  const onMarkActivate = useCallback(
    (mark: ChartMark, intent: MarkIntent) => {
      learn(mark)
      if (intent === 'drill') {
        const levels = drillLevels(level, mark)
        if (levels.length === 0) return
        pendingFocus.current = true
        drillTo(levels)
      } else if (intent === 'rows') {
        setRowsMark(mark)
        openRows(ROWS_OWNER, rowsSelectors(level, mark))
      } else {
        applyFilter(mark)
      }
    },
    [applyFilter, drillTo, learn, level, openRows],
  )

  // What the link named that was not applied: the page's notice when the page has one, else here.
  const dropped = useMemo(() => [...drill.dropped, ...ladder.dropped], [drill.dropped, ladder.dropped])
  const droppedKey = JSON.stringify(dropped)
  useEffect(() => {
    const values = JSON.parse(droppedKey) as string[]
    if (multi && values.length > 0) useScopeNoticeStore.getState().pushNotice({ dimension: 'drill', values, reason: DRILL_NOTICE_REASON })
  }, [droppedKey, multi])

  const crumbs: BreadcrumbItem[] = useMemo(() => {
    const items: BreadcrumbItem[] = [{ label: ROOT_CRUMB, to: drillSearch(location.search, []) }]
    usedPath.forEach((step, i) => items.push({ label: stepLabel(labels, step), to: drillSearch(location.search, usedPath.slice(0, i + 1)) }))
    // The last item is where the reader is: not a link.
    items[items.length - 1] = { label: items[items.length - 1].label }
    return items
  }, [labels, location.search, usedPath])

  const chart = levelChart(level, suite === null ? '' : stepLabel(labels, { dimension: 'suite', value: suite }))

  return (
    <div ref={sectionRef} data-catalogue-section="failures-drill" data-drill-level={level.kind} className="min-w-0">
      <BarChart
        key={levelKey}
        title={chart.title}
        state={frameState}
        variant={chart.variant}
        topN={chart.topN}
        headingLevel={3}
        height={DRILL_HEIGHT}
        dimension={chart.dimension}
        valueAxisLabel={chart.valueAxisLabel}
        scopeLabel={`last ${windowDays} days`}
        onMarkActivate={onMarkActivate}
        markIntents={markIntents}
        // The activation code rides in THIS lazy chunk, not in BarChart's default path.
        markKit={MARK_KIT}
        // The last value tick ("1,000") whole at 375 px, on DejaVu too (F-20).
        fitValueAxisEnd
        scope={
          <div
            data-drill-breadcrumb=""
            // A breadcrumb link is a level change the reader asked for: focus follows to the chart.
            onClickCapture={(event) => {
              if ((event.target as Element).closest('a')) pendingFocus.current = true
            }}
          >
            <Breadcrumbs items={crumbs} />
            {!multi && dropped.length > 0 ? (
              <p data-drill-notice="" className="text-xs text-[var(--color-text-secondary)]">
                {dropped.join(' ')}
              </p>
            ) : null}
          </div>
        }
      />
      <RowsPanel
        selectors={selectors}
        chart={rowsChart(level)}
        scope={rowsScope}
        title={rowsTitle(labels, selectors)}
        expected={expected}
        onClose={drill.closeRows}
      />
    </div>
  )
}

export function FailuresDrill(props: FailuresDrillProps): ReactElement {
  return (
    <LazySection label="failures-drill" minHeight={DRILL_SECTION_MIN_HEIGHT}>
      <FailuresDrillBody {...props} />
    </LazySection>
  )
}

export default FailuresDrill
