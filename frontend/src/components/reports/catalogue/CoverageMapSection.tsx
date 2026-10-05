/**
 * The coverage map section (VIZ-502) on the Coverage page: "where is testing
 * concentrated, thin, failing or stale", as a treemap of suites, then the
 * classes (or files) of one suite, then the tests of one class, sized by test
 * count and coloured by the reader's choice of measure.
 *
 * Only with BOTH `viz_chart_data_api` and `viz_advanced_charts` on
 * (`useAdvancedRollout`, the one seam): otherwise it renders nothing and asks
 * nothing. Mounted by `CoverageAdvanced` inside a `LazySection`, so nothing
 * runs before it is near the reader.
 *
 * LEVEL BY LEVEL. Each level is ONE `/analytics/coverage-map` request
 * (`depth` 1, 2, 3 with the parent's KEYS), never a client-side zoom, so no
 * response is more than 500 nodes. Which level is shown is the page's drill
 * path in the address bar (C5 `drill=suite~<key>&drill=class~<key>`, VIZ-602's
 * convention, through `useDrillPath`): a drill is a history push, so Back
 * goes up a level, and a shared URL opens the same level. The breadcrumb
 * ("All suites / payments") is links to the levels above; activating one
 * moves focus back to the map once its level is drawn (EPIC AC). A test (the
 * last level) opens its executions in the shared rows panel.
 *
 * HONEST LABELLING. The subtitle says "Test execution coverage — not code
 * coverage" (EPIC "Honest labelling"). Tests never run in the window are
 * present, hatched, and named ("Not run in this window", "Last run unknown",
 * "Never run"), never drawn as a low value. The map is per project: in All
 * Projects mode it says so and asks nothing.
 *
 * FILTER (VIZ-603, P2): a suite node also offers "Filter page by this"
 * (Shift-click, Shift+Enter, the readout's button) when the page provides its
 * suite select (`useCrossFilter`): it sets the page's "Test suite" select, it
 * does not drill. A class or a test is never a page filter.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import Breadcrumbs, { type BreadcrumbItem } from '@/components/ui/Breadcrumbs'
import ChartFrame from '@/components/charts/ChartFrame'
import CoverageTreemap from '@/components/charts/CoverageTreemap'
import { useCatalogChartData } from '@/components/charts/chartCatalogSources'
import { hasChartData, type ChartState } from '@/components/charts/chartStateCore'
import type { ChartMark, MarkIntent } from '@/components/charts/marks'
import {
  answersLevel,
  COLOR_BY_OPTIONS,
  COVERAGE_DRILL_NOTICE_REASON,
  COVERAGE_MAP_CAPTION,
  COVERAGE_MAP_EMPTY_HEIGHT,
  COVERAGE_MAP_TITLE,
  coverageDescription,
  coverageEmptyText,
  crumbKey,
  crumbLabel,
  DEFAULT_COLOR_BY,
  drillSearch,
  isColorBy,
  learnLabels,
  levelFromPath,
  levelPath,
  levelRequest,
  levelView,
  otherNote,
  ROOT_CRUMB,
  TEST_ROWS_CHART,
  type CoverageColorBy,
} from '@/components/charts/coverageMap.model'
import { encodeDrillLevel, ownedRows, readDrillLevels, useDrillPath, writeDrillParams } from '@/hooks/useDrillPath'
import { useCrossFilter } from '@/hooks/useCrossFilter'
import { useMultiFiltersEnabled } from '@/store/multiFiltersFlag'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { DrillLevel, EnvelopeMeta, TreeChart } from '@/lib/viz/contracts'
import { clampCatalogueDays, useCatalogueParams } from './catalogueScope'
import { RowsPanel } from './RowsPanel'
import type { RowsExpectation } from './RowsPanel.model'
import type { CoverageMapSectionProps } from './sectionContracts'
import { useAdvancedRollout } from './useCatalogueRollout'
import { useEverHadRun } from './useEverHadRun'

export const COVERAGE_MAP_HEIGHT = 360

/** All Projects: canonical tests belong to one project, so there is no map to draw. */
export const ALL_PROJECTS_REASON = 'the coverage map is per project; pick a project to see it'

/** Where a test sits, in one sentence (plan 3.2, the EPIC's manual-move edge). */
export const SUITE_RULE_NOTE = 'A test sits under the suite it ran in; one not run in the window, under its own suite.'


const SELECT =
  'min-h-6 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-1.5 py-0.5 text-xs text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/** The grain the numbers were counted in, from `meta.definitions.grain`, read defensively. */
function grainText(meta: EnvelopeMeta | null): string | null {
  const definitions = (meta as { definitions?: unknown } | null)?.definitions
  const grain = typeof definitions === 'object' && definitions !== null ? (definitions as { grain?: unknown }).grain : undefined
  return grain === 'execution_row' ? 'Counted per test execution.' : null
}

function ColorBySelect({ value, onChange }: { value: CoverageColorBy; onChange: (next: CoverageColorBy) => void }) {
  return (
    <label className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
      Colour by
      <select
        value={value}
        data-coverage-color-by=""
        className={SELECT}
        onChange={(event) => {
          if (isColorBy(event.target.value)) onChange(event.target.value)
        }}
      >
        {COLOR_BY_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/** This section's rows owner (`rows=by~coverage-map`). */
const COVERAGE_ROWS_OWNER = 'coverage-map'

function CoverageMapBody({ days, suiteFilter }: CoverageMapSectionProps) {
  const location = useLocation()
  const [, setSearchParams] = useSearchParams()
  const drillState = useDrillPath()
  const { path, truncate, openRows, closeRows } = drillState
  const multi = useMultiFiltersEnabled()
  const rows = ownedRows(drillState, COVERAGE_ROWS_OWNER)
  // Cut at the first level the map cannot open, a suite outside the page's suite filter included (R1B-6).
  const suiteKey = JSON.stringify(suiteFilter ?? null)
  const read = useMemo(() => levelFromPath(path, JSON.parse(suiteKey) as string | string[] | null), [path, suiteKey])
  const { level, used } = read
  const levelKey = useMemo(() => JSON.stringify(levelPath(level)), [level])
  const allProjects = useProjectStore((s) => s.activeProjectId) === ALL_PROJECTS_ID
  const windowDays = clampCatalogueDays(days)

  const extra = useMemo(() => levelRequest(level), [level])
  const params = useCatalogueParams(days, suiteFilter, extra)
  const everHadData = useEverHadRun(!allProjects)
  const state = useCatalogChartData('coverage-map', { params: allProjects ? null : params, everHadData })

  const drawn = hasChartData(state) ? state : null
  // While the next level loads, the previous response stays on screen: it is
  // NOT this level's, and read as this level it would open the wrong keys.
  const current = drawn && answersLevel(drawn.data.series, level) ? drawn : null
  const tree: TreeChart | null = current ? current.data.series : null
  const meta = current ? current.meta : null
  const view = useMemo(() => levelView(tree, level), [tree, level])

  // The breadcrumb's labels: what the map called each level when it was on screen.
  const [labels, setLabels] = useState<Record<string, string>>({})
  const learned = useMemo(() => learnLabels(view, level), [view, level])
  if (Object.entries(learned).some(([key, label]) => labels[key] !== label)) setLabels({ ...labels, ...learned })

  const [colorBy, setColorBy] = useState<CoverageColorBy>(DEFAULT_COLOR_BY)

  // Focus goes back to the map once the level a reader asked for is drawn.
  const chartRef = useRef<HTMLDivElement | null>(null)
  const pendingFocus = useRef(false)
  useEffect(() => {
    if (!pendingFocus.current || !tree) return
    if (chartRef.current) {
      chartRef.current.focus()
      pendingFocus.current = false
    }
  }, [tree, levelKey])

  const usedPath = useMemo(() => path.slice(0, used), [path, used])

  // Drill from the APPLIED path: a cut level in the URL is replaced, not appended to (one history push).
  const drill = useCallback(
    (levels: readonly DrillLevel[]) => {
      const { levels: next } = readDrillLevels([...usedPath, ...levels].map(encodeDrillLevel))
      setSearchParams((current) => writeDrillParams(current, { path: next, rows: [] }))
    },
    [setSearchParams, usedPath],
  )

  // What the link named that was not applied: the page's notice when the page has one (the
  // report filter bar, `viz_multi_filters`), else the frame's footer. As the Failures ladder does.
  const dropped = useMemo(() => [...drillState.dropped, ...read.dropped], [drillState.dropped, read.dropped])
  const droppedKey = JSON.stringify(dropped)
  useEffect(() => {
    const values = JSON.parse(droppedKey) as string[]
    if (multi && values.length > 0) useScopeNoticeStore.getState().pushNotice({ dimension: 'drill', values, reason: COVERAGE_DRILL_NOTICE_REASON })
  }, [droppedKey, multi])
  const onLevelUp = useCallback(() => {
    pendingFocus.current = true
    truncate(used - 1)
  }, [truncate, used])

  // A suite node can also filter the page (its suite select), when the page has one.
  const cross = useCrossFilter()
  const offersFilter = cross.offers
  const applyFilter = cross.apply
  const markIntents = useCallback(
    (mark: ChartMark): MarkIntent[] =>
      mark.dimension === 'test' ? ['rows'] : mark.dimension === 'suite' && offersFilter(mark) ? ['drill', 'filter'] : ['drill'],
    [offersFilter],
  )

  // The mark behind an open rows panel, while this page view still has it.
  const [rowsMark, setRowsMark] = useState<ChartMark | null>(null)
  const onMarkActivate = useCallback(
    (mark: ChartMark, intent: MarkIntent) => {
      if (intent === 'drill' && (mark.dimension === 'suite' || mark.dimension === 'class')) {
        pendingFocus.current = true
        drill([{ dimension: mark.dimension, value: mark.value }])
      } else if (intent === 'rows' && mark.dimension === 'test') {
        setRowsMark(mark)
        openRows(COVERAGE_ROWS_OWNER, [{ dimension: 'test', value: mark.value }])
      } else if (intent === 'filter' && mark.dimension === 'suite') {
        applyFilter(mark)
      }
    },
    [applyFilter, drill, openRows],
  )

  const crumbs: BreadcrumbItem[] = useMemo(() => {
    const items: BreadcrumbItem[] = [{ label: ROOT_CRUMB, to: drillSearch(location.search, []) }]
    usedPath.forEach((step, i) => items.push({ label: crumbLabel(labels, step), to: drillSearch(location.search, usedPath.slice(0, i + 1)) }))
    // The last item is where the reader is: not a link.
    const last = items[items.length - 1]
    items[items.length - 1] = { label: last.label }
    return items
  }, [usedPath, labels, location.search])

  // This section's rows panel: a test's executions, opened by this map (the URL's owner entry; the heatmaps on the page read the same key).
  // Exactly the shape this map writes, one `test` selector and nothing else (R1B-5): any other
  // selection would send a `bucket_<dimension>` the test-rows chart does not group by.
  const testRows: readonly DrillLevel[] = rows.length === 1 && rows[0].dimension === 'test' ? rows : []
  const rowsScope = useCatalogueParams(days, level.suite)
  const rowsTest = testRows.find((r) => r.dimension === 'test')
  const expected: RowsExpectation | null =
    rowsMark && rowsTest && rowsMark.value === rowsTest.value ? { y: rowsMark.n, n: rowsMark.n, asOf: meta?.as_of ?? null } : null
  const rowsTitle = rowsTest
    ? rowsMark && rowsMark.value === rowsTest.value
      ? rowsMark.label
      : (labels[crumbKey(rowsTest)] ?? rowsTest.value)
    : ''

  const frameState: ChartState<unknown> = useMemo(() => {
    if (allProjects) return { status: 'not-measured', reason: ALL_PROJECTS_REASON, meta: null }
    if (drawn && !current) return { status: 'loading' }
    // "Showing top N of M" would count the root and the Other node: the footer says it instead.
    if (state.status === 'truncated') return { status: 'ready', data: state.data, meta: state.meta, revalidating: state.revalidating }
    return state
  }, [allProjects, drawn, current, state])

  const note = otherNote(view, meta?.truncated_total ?? null, level.depth)
  const grain = grainText(meta)

  return (
    <div data-catalogue-section="coverage-map" className="min-w-0">
      <ChartFrame
        title={COVERAGE_MAP_TITLE}
        takeaway={COVERAGE_MAP_CAPTION}
        headingLevel={3}
        state={frameState}
        height={frameState.status === 'filtered-empty' ? COVERAGE_MAP_EMPTY_HEIGHT : COVERAGE_MAP_HEIGHT}
        emptyMessage={coverageEmptyText(level)}
        series={tree}
        chartType="Treemap"
        scopeLabel={`last ${windowDays} days`}
        scope={
          <div
            data-coverage-breadcrumb=""
            // A breadcrumb link is a level change the reader asked for: focus follows to the map.
            onClickCapture={(event) => {
              if ((event.target as Element).closest('a')) pendingFocus.current = true
            }}
          >
            <Breadcrumbs items={crumbs} />
          </div>
        }
        toolbar={<ColorBySelect value={colorBy} onChange={setColorBy} />}
        footer={
          <>
            {/* A shared link that named a level this page could not open (C5: the path stops there, and says so). */}
            {(multi ? [] : dropped).map((sentence) => (
              <span key={sentence} data-drill-dropped="">
                {sentence}
              </span>
            ))}
            {note ? <span data-coverage-other-note="">{note}</span> : null}
            <span data-coverage-suite-rule="">{SUITE_RULE_NOTE}</span>
            {grain ? <span data-catalogue-grain="">{grain}</span> : null}
          </>
        }
      >
        {view.children.length > 0 ? (
          <CoverageTreemap
            key={levelKey}
            ref={chartRef}
            items={view.children}
            level={level}
            colorBy={colorBy}
            description={coverageDescription(view, level, colorBy)}
            height={COVERAGE_MAP_HEIGHT}
            onLevelUp={used > 0 ? onLevelUp : undefined}
            onMarkActivate={onMarkActivate}
            markIntents={markIntents}
          />
        ) : null}
      </ChartFrame>
      <RowsPanel
        selectors={testRows}
        chart={TEST_ROWS_CHART}
        scope={rowsScope}
        title={rowsTitle}
        expected={expected}
        onClose={closeRows}
      />
    </div>
  )
}

export function CoverageMapSection(props: CoverageMapSectionProps): ReactElement | null {
  const on = useAdvancedRollout()
  if (!on) return null
  return <CoverageMapBody {...props} />
}

export default CoverageMapSection
