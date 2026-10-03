/**
 * VIZ-504: failure groups inside `ChartFrame` — the bubbles (default), the
 * "Related groups" view when the server linked any two of them, and the ranked
 * table, always.
 *
 * It reads `useCatalogChartData('failure-groups', ...)`'s state: a C3 graph
 * plus BE3's additive keys, turned into the model by `failureGroupsModel`.
 * The frame owns every non-data state (loading, empty, error, 429, forbidden);
 * the views are mounted only once there is something to draw.
 *
 *   takeaway   the Pareto sentence ("Top 3 groups = 61% of failures"), from
 *              shares of EVERY failure in scope
 *   footer     the roll-ups that are not circles ("No error message: …",
 *              "Seen once: …", "Smaller groups not shown: …") and, when the
 *              smallest circles had to be enlarged to be pointed at, which
 *   table      "View as table" and the export read a category series of the
 *              groups' failures (bubbles) or the links (related groups), so the
 *              CSV is the view on screen
 *
 * Group activation (click, Enter, the readout's buttons, the table's names) is
 * handed to the host through the kit's mark vocabulary (`marks.ts`): the host
 * decides what "drill" and "rows" open. No "AI" wording anywhere: groups are
 * computed from the error text by a fixed rule.
 *
 * Holds no engine and no d3: the views under `failureGroups/` do, and this
 * module is only reached from a lazy section chunk.
 */
import { forwardRef, useMemo, useState, type ReactNode } from 'react'
import type { GraphChart } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import { hasChartData, type ChartResponse, type ChartState } from './chartStateCore'
import FailureGroupBubbles from './failureGroups/FailureGroupBubbles'
import FailureGroupRelations from './failureGroups/FailureGroupRelations'
import FailureGroupTable from './failureGroups/FailureGroupTable'
import {
  failureGroupsModel,
  groupsSeries,
  paretoTakeaway,
  relationsSeries,
  rollupLines,
  VIEW_LABELS,
  type FailureGroup,
  type FailureGroupsView,
} from './failureGroups/failureGroups.model'
import { EMPTY_ANSWER_HEIGHT } from './failureGroups/plot.model'
import type { MarkActivationProps } from './marks'

type FrameShell = Omit<
  ChartFrameProps,
  'children' | 'series' | 'chartType' | 'axes' | 'format' | 'state' | 'takeaway' | 'toolbar'
>

export interface FailureGroupsFrameProps extends FrameShell, MarkActivationProps {
  state: ChartState<ChartResponse<GraphChart>>
  /** Open a group's details (the table's names). */
  onOpenGroup: (group: FailureGroup) => void
  /** The group whose details are open. */
  selectedId?: string | null
  /** The plot's width when known (tests, gallery). */
  plotWidth?: number
  /** The first view (default bubbles; `relations` falls back to bubbles when there are no links). */
  initialView?: FailureGroupsView
  /**
   * The ranked table's caption, which also names its scrolling region
   * (default 'Failure groups, largest first'). A page with several frames
   * names each, so no two regions share a name (axe `landmark-unique`).
   */
  tableCaption?: string
}

const SEGMENT =
  'px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-pressed:bg-[var(--color-bg-hover)]'

/**
 * What the frame says when every failure is a roll-up (each message seen once,
 * or none): there are failures, but no two share a first line, so there is no
 * group to draw. Not "no data": the roll-ups below say how many.
 */
export const NO_GROUPS_MESSAGE = 'No two failures in this scope share a first line, so there is no group to draw.'

const FailureGroupsFrame = forwardRef<HTMLDivElement, FailureGroupsFrameProps>(function FailureGroupsFrame(
  {
    state,
    onOpenGroup,
    selectedId = null,
    plotWidth,
    initialView = 'bubbles',
    tableCaption,
    onMarkActivate,
    markIntents,
    height = 360,
    footer,
    ...frameProps
  },
  ref,
) {
  const source = hasChartData(state) ? state.data.series : null
  const model = useMemo(() => (source ? failureGroupsModel(source) : null), [source])
  const [chosen, setChosen] = useState<FailureGroupsView>(initialView)
  const canRelate = (model?.edges.length ?? 0) > 0
  const view: FailureGroupsView = chosen === 'relations' && canRelate ? 'relations' : 'bubbles'

  const series = useMemo(
    () => (model ? (view === 'relations' ? relationsSeries(model) : groupsSeries(model)) : null),
    [model, view],
  )
  const takeaway = model ? paretoTakeaway(model) : undefined
  const description = model
    ? view === 'relations'
      ? `Related failure groups: the ${formatNumber(Math.min(model.groups.length, 60))} largest, linked when they fail in the same tests. Arrow keys move through them largest first; Enter opens one.`
      : `Failure groups as circles sized by failures: ${formatNumber(model.groups.length)} groups. Arrow keys move through them largest first; Enter opens one.`
    : ''

  const toolbar: ReactNode = canRelate ? (
    <div
      role="group"
      aria-label="View"
      className="inline-flex overflow-hidden rounded border border-[var(--color-border-light)]"
      data-group-view-switch=""
    >
      {(['bubbles', 'relations'] as const).map((option) => (
        <button
          key={option}
          type="button"
          className={view === option ? `${SEGMENT} font-semibold` : SEGMENT}
          aria-pressed={view === option}
          onClick={() => setChosen(option)}
        >
          {VIEW_LABELS[option]}
        </button>
      ))}
    </div>
  ) : undefined

  const lines = model ? rollupLines(model) : []
  const frameFooter: ReactNode =
    lines.length > 0 || footer ? (
      <>
        {lines.map((line) => (
          <span key={line.key} data-group-rollup={line.key} className="text-[var(--color-text-secondary)]">
            {line.text}
          </span>
        ))}
        {footer}
      </>
    ) : undefined

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      state={state}
      // A drawn "no group" answer is one sentence: it does not hold the plot's height (R2-B F-14).
      height={model && model.groups.length === 0 ? EMPTY_ANSWER_HEIGHT : height}
      takeaway={takeaway}
      toolbar={toolbar}
      series={series}
      chartType={view === 'relations' ? 'Network of related groups' : 'Bubble chart'}
      axes={view === 'relations' ? undefined : { x: 'Failure group', y: 'Failures' }}
      footer={frameFooter}
    >
      {model && model.groups.length === 0 ? (
        <p data-group-none="" className="m-0 py-8 text-center text-sm text-[var(--color-text-secondary)]">
          {NO_GROUPS_MESSAGE}
        </p>
      ) : model ? (
        <div className="flex min-w-0 flex-col gap-4" data-group-view={view}>
          {view === 'relations' ? (
            <FailureGroupRelations
              groups={model.groups}
              edges={model.edges}
              height={height}
              description={description}
              selectedId={selectedId}
              width={plotWidth}
              onMarkActivate={onMarkActivate}
              markIntents={markIntents}
            />
          ) : (
            <FailureGroupBubbles
              groups={model.groups}
              height={height}
              description={description}
              selectedId={selectedId}
              width={plotWidth}
              onMarkActivate={onMarkActivate}
              markIntents={markIntents}
            />
          )}
          <FailureGroupTable
            groups={model.groups}
            trendGrain={model.trendGrain}
            onOpen={onOpenGroup}
            selectedId={selectedId}
            caption={tableCaption}
            width={plotWidth}
          />
        </div>
      ) : null}
    </ChartFrame>
  )
})

export default FailureGroupsFrame
