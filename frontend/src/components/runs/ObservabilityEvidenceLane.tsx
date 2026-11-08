/**
 * W39 STRAT-07: one lane of the "Observability evidence" card (`ObservabilityEvidenceCard`): its label, who fetched
 * it, the server's sentence; with evidence, its timed items on the card's shared axis and its "Without a time" list.
 * A lane without evidence shows only its sentence — never an empty plot. Every string is React text (titles and
 * details come from stored tool output).
 */
import { EvidenceMarksPlot, EvidenceSeriesPlot, type RunWindowMs } from '@/components/charts/EvidenceLanes'
import type { EvidenceAxis, EvidenceLaneView } from '@/components/charts/evidenceLanes.model'
import {
  FETCHED_BY_LABELS,
  LANE_LABELS,
  TRUNCATED_SENTENCE,
  UNTIMED_HEADING,
  UNTIMED_REASON_LABELS,
  type ObservabilityUntimed,
} from '@/types/observabilityTimeline'
import { formatNumber } from '@/utils/formatters'

/** The untimed list shows this many items, then says how many more there are. */
export const UNTIMED_SHOWN_MAX = 25

const label = <K extends string>(labels: Record<K, string>, key: string): string =>
  (labels as Record<string, string>)[key] ?? key

function UntimedList({ items }: { items: ObservabilityUntimed[] }) {
  const shown = items.slice(0, UNTIMED_SHOWN_MAX)
  return (
    <div data-untimed="" className="mt-2">
      <h4 className="m-0 text-2xs font-semibold text-[var(--color-text-muted)]">{UNTIMED_HEADING}</h4>
      <ul className="m-0 mt-1 list-none space-y-1 p-0">
        {shown.map((item, index) => (
          <li key={index} data-untimed-item={item.reason} className="min-w-0 text-xs text-[var(--color-text-secondary)]">
            <span className="text-[var(--color-text)]">{item.title}</span>
            {item.test_name && <span data-untimed-test="">{` · ${item.test_name}`}</span>}
            <span className="text-[var(--color-text-muted)]">{` — ${label(UNTIMED_REASON_LABELS, item.reason)}`}</span>
            {item.detail && (
              <span data-untimed-detail="" title={item.detail} className="block truncate text-[var(--color-text-muted)]">
                {item.detail}
              </span>
            )}
          </li>
        ))}
      </ul>
      {items.length > shown.length && (
        <p data-untimed-more="" className="m-0 mt-1 text-2xs text-[var(--color-text-faint)]">
          {`${formatNumber(items.length - shown.length)} more without a time are not listed here.`}
        </p>
      )}
    </div>
  )
}

export default function ObservabilityEvidenceLane({
  view,
  axis,
  width,
  runWindow,
  onActive,
}: {
  view: EvidenceLaneView
  axis: EvidenceAxis | null
  width: number
  runWindow: RunWindowMs
  onActive: (text: string | null) => void
}) {
  const { lane } = view
  const evidence = lane.state === 'evidence'
  return (
    <li data-lane={lane.source} data-lane-state={lane.state} className="min-w-0 border-t border-[var(--color-border-light)] pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="m-0 text-xs font-semibold text-[var(--color-text)]">{lane.label || label(LANE_LABELS, lane.source)}</h3>
        <span
          data-lane-fetched-by={lane.fetched_by}
          className="rounded border border-[var(--color-border)] px-1.5 py-px text-2xs text-[var(--color-text-secondary)]"
        >
          {label(FETCHED_BY_LABELS, lane.fetched_by)}
        </span>
      </div>
      {lane.sentence && (
        <p data-lane-sentence="" className="m-0 mt-1 text-xs text-[var(--color-text-secondary)]">
          {lane.sentence}
        </p>
      )}
      {evidence && axis && view.marks.length > 0 && (
        <div className="mt-2">
          <EvidenceMarksPlot view={view} axis={axis} width={width} runWindow={runWindow} onActive={onActive} />
        </div>
      )}
      {evidence &&
        view.series.map((series, index) => (
          <div
            key={`${series.series.cluster_id ?? ''}:${series.series.label}:${index}`}
            data-evidence-series={series.series.label}
            data-series-rose={series.series.rose ? 'true' : 'false'}
            className="mt-2"
          >
            {axis && series.drawn > 0 && <EvidenceSeriesPlot view={series} axis={axis} width={width} runWindow={runWindow} />}
            <p className="m-0 text-xs text-[var(--color-text-secondary)]">
              <span className="font-medium text-[var(--color-text)]">{series.series.label}</span>
              {series.series.unit ? <span className="text-[var(--color-text-muted)]">{` (${series.series.unit})`}</span> : null}
              {series.series.sentence ? <span data-series-sentence="">{` — ${series.series.sentence}`}</span> : null}
            </p>
          </div>
        ))}
      {evidence && view.untimed.length > 0 && <UntimedList items={view.untimed} />}
      {lane.truncated && (
        <p data-lane-truncated="" className="m-0 mt-1 text-2xs text-[var(--color-text-faint)]">
          {TRUNCATED_SENTENCE}
        </p>
      )}
    </li>
  )
}
