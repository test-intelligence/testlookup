/**
 * The heatmap section (VIZ-501, Wave 3): one `/analytics/heatmap` request,
 * drawn by `HeatmapChartFrame`, with the three controls the plan gives it —
 *
 *   - **kind** (a segmented radio group, only when the host offers more than
 *     one): Suite x day, Test x run, Suite x environment, Suite x release;
 *   - **row order**: worst first (the default), by name, most executions
 *     first — a pure permutation of the server's rows, keys included;
 *   - **fit the colour scale to the data** (a pressed toggle, rate kinds
 *     only): off by default, so the ramp means 0..100% as on every other
 *     heatmap.
 *
 * Hosts: Trends (`suite_day`, through `TrendsCatalogue`), Suite detail
 * (`test_run`, `SuiteDetailAdvanced`), Coverage (`suite_environment`,
 * `suite_release`, `CoverageAdvanced`). It asks no flag (Phase D, S4: the
 * flags are on everywhere) and mounts behind its own `LazySection`, so
 * nothing is asked, and no engine downloaded, until it is near the reader.
 *
 * Never a request the server must refuse: a one-day `suite_day` window is one
 * column (not a trend), and `test_run` / `suite_release` need one project
 * (runs of several projects are not one sequence; a release belongs to one
 * project). Those say why in the frame (`not-measured`) and ask nothing.
 * The window goes through `catalogueParams` (at most 90 days on the wire;
 * the 2026-10-02 decision keeps the suite x day heatmap at 90).
 *
 * A cell's actions: its rows, and "Filter page by this" (VIZ-603, P2) when
 * the cell names a suite or release the page can be filtered by
 * (`useCrossFilter`): a suite x release cell sets the page's "Test suite"
 * select AND the top bar's release; a suite x day or suite x environment cell
 * sets the suite. A test x run cell (Suite detail) never offers it.
 */
import { useCallback, useId, useState, type ReactElement } from 'react'
import type { DrillLevel } from '@/lib/viz/contracts'
import { useCatalogChartData, type CatalogResponse } from '@/components/charts/chartCatalogSources'
import { hasChartData, type ChartState } from '@/components/charts/chartState'
import { useChartAnnouncer } from '@/components/charts/ChartAnnouncer'
import HeatmapChartFrame from '@/components/charts/HeatmapChartFrame'
import { heatmapOrderNote, type HeatmapRowSort } from '@/components/charts/heatmapFromMatrix'
import { markSelectors, type ChartMark, type MarkIntent } from '@/components/charts/marks'
import { ownedRows, useDrillPath } from '@/hooks/useDrillPath'
import { useCrossFilter } from '@/hooks/useCrossFilter'
import LazySection from './LazySection'
import RowsPanel from './RowsPanel'
import { selectorsKey, type RowsExpectation } from './RowsPanel.model'
import { clampCatalogueDays, ROW_GRAIN_MAX_WINDOW_DAYS, useCatalogueParams } from './catalogueScope'
import { useEverHadRun } from './useEverHadRun'
import type { HeatmapKind, HeatmapSectionProps } from './sectionContracts'
import {
  HEATMAP_FIT_LABEL,
  HEATMAP_GRAIN_NOTE,
  HEATMAP_KIND_LABEL,
  HEATMAP_KIND_SPECS,
  HEATMAP_SECTION_HEIGHT,
  HEATMAP_SORT_LABEL,
  HEATMAP_MARK_INTENTS,
  HEATMAP_SORT_OPTIONS,
  heatmapOpenedRows,
  heatmapOwnRows,
  heatmapRowsChart,
  heatmapRowsTitle,
  ONE_DAY_HEATMAP_REASON,
} from './HeatmapSection.model'

const FRAME_CHROME = 150

/** A closed rows panel (one identity: a new `[]` per render would be a new key). */
const NO_SELECTORS: readonly DrillLevel[] = []

/** Wave 3 additions to the pinned props (optional only, `sectionContracts.ts`). */
export interface HeatmapSectionOwnProps extends HeatmapSectionProps {
  /** The frame's title; default: the kind's. */
  title?: string
  /** `data-catalogue-section` and the lazy placeholder's label; default `heatmap-<first kind>`. */
  sectionId?: string
}

const BUTTON =
  'rounded border border-[var(--color-border-light)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
/** The fit toggle while on: the button, filled and bold (whole strings, so every class is plain to see and to check). */
const BUTTON_PRESSED =
  'rounded border border-[var(--color-border-light)] bg-[var(--color-bg-hover)] px-2 py-1 text-xs font-semibold text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const SELECT =
  'rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
/**
 * One kind: a native radio and its words, 24 px tall (the target size). Only
 * classes the app already uses: a new Tailwind class grows the EAGER
 * stylesheet even from this lazy chunk (FK0 finding 2).
 */
const SEGMENT = 'inline-flex min-h-6 cursor-pointer items-center gap-1 px-2 text-xs text-[var(--color-text)]'

const notMeasured = <T,>(reason: string): ChartState<T> => ({ status: 'not-measured', reason, meta: null })

/**
 * Whether `data` answers `kind` (R1B-3). The chart keeps the previous
 * response on screen while the next loads (`keepPreviousData`), and a heatmap
 * response does not say which kind it is: after a kind switch the previous
 * kind's matrix would stay drawn and clickable, its cells read with the NEW
 * kind's dimensions (an environment key sent as `bucket_release`). So the
 * section remembers which kind each response it saw arrived under: a response
 * object seen for the first time is the current key's (a superseded request
 * is aborted, never delivered), and the one still held from before a switch
 * answers the kind it arrived under.
 */
function useAnswersKind(kind: HeatmapKind, data: object | null): boolean {
  const [seen, setSeen] = useState<{ kind: HeatmapKind; data: object } | null>(null)
  if (data === null) return false
  if (seen === null || seen.data !== data) {
    setSeen({ kind, data })
    return true
  }
  return seen.kind === kind
}

function KindSelector({
  kinds,
  value,
  onChange,
}: {
  kinds: readonly HeatmapKind[]
  value: HeatmapKind
  onChange: (kind: HeatmapKind) => void
}) {
  const name = useId()
  return (
    <fieldset data-heatmap-kinds="" className="m-0 flex flex-wrap items-center border-0 p-0">
      <legend className="sr-only">{HEATMAP_KIND_LABEL}</legend>
      {kinds.map((kind) => (
        <label key={kind} className={SEGMENT}>
          <input
            type="radio"
            name={name}
            value={kind}
            checked={kind === value}
            onChange={() => onChange(kind)}
          />
          {HEATMAP_KIND_SPECS[kind].option}
        </label>
      ))}
    </fieldset>
  )
}

function HeatmapSectionBody({ days, suiteFilter, kinds, title, sectionId }: HeatmapSectionOwnProps & { sectionId: string }) {
  const [chosen, setChosen] = useState<HeatmapKind>(kinds[0])
  // A host that changes its kinds never leaves the selector on one it no longer offers.
  const kind = kinds.includes(chosen) ? chosen : kinds[0]
  const [sort, setSort] = useState<HeatmapRowSort>('worst')
  const [fit, setFit] = useState(false)
  const announcer = useChartAnnouncer()
  const spec = HEATMAP_KIND_SPECS[kind]
  const sortId = useId()

  const windowDays = clampCatalogueDays(days, ROW_GRAIN_MAX_WINDOW_DAYS)
  const params = useCatalogueParams(days, suiteFilter, { kind }, { maxDays: ROW_GRAIN_MAX_WINDOW_DAYS })
  const allProjects = params !== null && params.project_id === undefined
  const reason =
    windowDays < spec.minDays ? ONE_DAY_HEATMAP_REASON : allProjects && spec.allProjectsReason ? spec.allProjectsReason : null
  const everHadData = useEverHadRun(reason === null)
  const fetched = useCatalogChartData('heatmap', { params: reason === null ? params : null, everHadData })
  const answers = useAnswersKind(kind, hasChartData(fetched) ? fetched.data : null)
  // Another kind's matrix while this kind loads: loading (no cells to click), never drawn under this kind's words.
  const current: ChartState<CatalogResponse<'heatmap'>> = hasChartData(fetched) && !answers ? { status: 'loading' } : fetched
  const state = reason === null ? current : notMeasured<CatalogResponse<'heatmap'>>(reason)

  const onSort = useCallback(
    (next: HeatmapRowSort) => {
      setSort(next)
      // The reader's own action; the canvas changed with no words of its own.
      announcer?.assertive(heatmapOrderNote(spec.status ? 'status' : 'rate', next))
    },
    [announcer, spec.status],
  )

  // "View rows" on a cell (VIZ-602 seam): the rows panel, its selectors in
  // the URL (`rows=by~<section id>&rows=...`, a history push). The panel
  // opens only for a selection THIS section owns: another section on the page
  // (the treemap, the scatter, which also opens a lone `test`) shares the
  // `rows` key, and the owner entry says whose selection it is — for a click,
  // a pasted link and Back/Forward alike.
  const drill = useDrillPath()
  const rowsScope = useCatalogueParams(days, suiteFilter, undefined, { maxDays: ROW_GRAIN_MAX_WINDOW_DAYS })
  const asOf = hasChartData(state) ? (state.meta?.as_of ?? null) : null
  // The clicked cell's title and figures, while this page view still has them.
  const [opened, setOpened] = useState<{ key: string; title: string; expected: RowsExpectation | null } | null>(null)
  const matrix = hasChartData(state) ? state.data.series : null
  // "Filter page by this" on a cell that names a suite or release the page can be filtered by.
  const cross = useCrossFilter()
  const offersFilter = cross.offers
  const applyFilter = cross.apply
  const markIntents = useCallback(
    (mark: ChartMark): MarkIntent[] => [...HEATMAP_MARK_INTENTS, ...(offersFilter(mark) ? (['filter'] as const) : [])],
    [offersFilter],
  )
  const onMarkActivate = useCallback(
    (mark: ChartMark, intent: MarkIntent) => {
      if (intent === 'filter') {
        applyFilter(mark)
        return
      }
      if (intent !== 'rows') return
      const selectors = markSelectors(mark)
      setOpened({ key: selectorsKey(selectors), ...heatmapOpenedRows(spec, mark, selectors, matrix, asOf) })
      drill.openRows(sectionId, selectors)
    },
    [applyFilter, asOf, drill, matrix, sectionId, spec],
  )
  const rowsSelectors = heatmapOwnRows(spec, ownedRows(drill, sectionId))
  const clicked = opened !== null && rowsSelectors.length > 0 && selectorsKey(rowsSelectors) === opened.key ? opened : null
  const rowsTitle = clicked ? clicked.title : heatmapRowsTitle(matrix, rowsSelectors)
  const closeRows = useCallback(() => {
    drill.closeRows()
    setOpened(null)
  }, [drill])

  const toolbar: ReactElement = (
    <>
      {kinds.length > 1 ? <KindSelector kinds={kinds} value={kind} onChange={setChosen} /> : null}
      <label htmlFor={sortId} className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
        {HEATMAP_SORT_LABEL}
        <select
          id={sortId}
          data-heatmap-sort=""
          value={sort}
          onChange={(event) => onSort(event.target.value as HeatmapRowSort)}
          className={SELECT}
        >
          {HEATMAP_SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {spec.status ? null : (
        <button
          type="button"
          data-heatmap-fit=""
          aria-pressed={fit}
          onClick={() => setFit((on) => !on)}
          className={fit ? BUTTON_PRESSED : BUTTON}
        >
          {HEATMAP_FIT_LABEL}
        </button>
      )}
    </>
  )

  return (
    <div data-catalogue-section={sectionId} data-heatmap-kind={kind} className="min-w-0">
      <HeatmapChartFrame
        title={title ?? spec.title}
        headingLevel={3}
        height={HEATMAP_SECTION_HEIGHT}
        state={state}
        nouns={spec.nouns}
        rowAxis={spec.rowAxis}
        columnAxis={spec.columnAxis}
        sort={sort}
        fit={fit}
        toolbar={toolbar}
        scopeLabel={`last ${windowDays} days`}
        footer={<span data-catalogue-grain="">{HEATMAP_GRAIN_NOTE}</span>}
        markDimensions={spec.marks}
        onMarkActivate={onMarkActivate}
        markIntents={markIntents}
      />
      <RowsPanel
        selectors={rowsSelectors.length > 0 ? rowsSelectors : NO_SELECTORS}
        chart={heatmapRowsChart(spec)}
        scope={rowsScope}
        title={rowsTitle}
        expected={clicked?.expected ?? null}
        onClose={closeRows}
      />
    </div>
  )
}

export function HeatmapSection(props: HeatmapSectionOwnProps): ReactElement | null {
  if (props.kinds.length === 0) return null
  const sectionId = props.sectionId ?? `heatmap-${props.kinds[0]}`
  return (
    <LazySection label={sectionId} minHeight={HEATMAP_SECTION_HEIGHT + FRAME_CHROME}>
      <HeatmapSectionBody {...props} sectionId={sectionId} />
    </LazySection>
  )
}

export default HeatmapSection
