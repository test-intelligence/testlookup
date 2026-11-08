/**
 * W39 STRAT-07 (first slice) — "Observability evidence" at the bottom of the Run page's Timing tab: the Splunk /
 * OpenShift / Prometheus evidence the AI analysis ALREADY fetched for this run, on one time axis
 * (`GET /api/v1/runs/{id}/observability-timeline`). Opening it asks none of those systems anything: the server reads
 * what the analysis (and the ingest) stored, and writes nothing. Mounted only inside the Timing tab, so a tab that is
 * not open asks for nothing.
 *
 * Honest states, in words, never an empty chart:
 *   loading / error   a spinner / one sentence (the rest of the tab keeps working);
 *   the header        what this is; the analysis it shows ("From the analysis of …", "N earlier analyses not shown");
 *                     two facts labelled "now" (offline mode, which integrations are configured) — never presented as
 *                     the state at analysis time;
 *   one row per lane  `ObservabilityEvidenceLane`: label, who fetched it, the server's sentence; with evidence, the
 *                     timed items on the shared axis (the run window shaded; Prometheus series as small lines with
 *                     their sentence; Splunk items as dots; OpenShift first → last as bars; the item on hover / focus)
 *                     and a "Without a time" list;
 *   nothing timed     no axis (`data-axis-state` = `untimed` / `none`), the lanes' sentences only.
 */
import { useId, useMemo, useState } from 'react'
import { Radar } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useContainerWidth } from '@/components/charts/chartLayout'
import { EvidenceAxisRow } from '@/components/charts/EvidenceLanes'
import { buildEvidenceView, type EvidenceView } from '@/components/charts/evidenceLanes.model'
import { useObservabilityTimeline } from '@/hooks/useObservabilityTimeline'
import {
  CARD_TITLE,
  HEADER_SENTENCE,
  OFFLINE_NOW_SENTENCE,
  type ObservabilityAnalysis,
  type RunObservabilityTimeline,
} from '@/types/observabilityTimeline'
import ObservabilityEvidenceLane from './ObservabilityEvidenceLane'
import {
  analysedLine,
  configuredNowLine,
  LOAD_ERROR,
  NO_ANALYSIS_SENTENCE,
  NO_RUN_WINDOW_CAPTION,
  othersLine,
  RUN_WINDOW_CAPTION,
} from './observabilityEvidenceText'

/** The plots' width before they are measured (and in jsdom, which has no layout). */
const FALLBACK_WIDTH = 720

function AnalysisLine({ analysis }: { analysis: ObservabilityAnalysis | null }) {
  if (!analysis) {
    return (
      <p data-observability-analysis="none" className="m-0 text-xs text-[var(--color-text-secondary)]">
        {NO_ANALYSIS_SENTENCE}
      </p>
    )
  }
  const others = typeof analysis.other_analyses === 'number' && analysis.other_analyses > 0 ? analysis.other_analyses : 0
  return (
    <p data-observability-analysis={analysis.pipeline_run_id} className="m-0 text-xs text-[var(--color-text-secondary)]">
      <span data-observability-analysed="">{analysedLine(analysis.analysed_at)}</span>
      {others > 0 && (
        <span data-observability-others={others} className="text-[var(--color-text-muted)]">
          {` ${othersLine(others)}`}
        </span>
      )}
    </p>
  )
}

function NowNote({ id, children }: { id: string; children: string }) {
  return (
    <li data-now={id} className="text-xs text-[var(--color-text-secondary)]">
      <span data-now-label="" className="mr-1.5 rounded border border-[var(--color-border)] px-1 text-2xs font-semibold text-[var(--color-text-muted)]">
        Now
      </span>{' '}
      {children}
    </li>
  )
}

function Body({ data, view }: { data: RunObservabilityTimeline; view: EvidenceView }) {
  const [measureRef, measured] = useContainerWidth<HTMLDivElement>()
  const width = measured > 0 ? measured : FALLBACK_WIDTH
  const [active, setActive] = useState<string | null>(null)
  return (
    <div data-observability-state="ready" className="mt-2 space-y-3">
      <div className="space-y-1">
        <p data-observability-header="" className="m-0 text-xs text-[var(--color-text-secondary)]">
          {HEADER_SENTENCE}
        </p>
        <AnalysisLine analysis={data.analysis ?? null} />
        <ul data-observability-now="" className="m-0 list-none space-y-1 p-0">
          {data.offline_now === true && <NowNote id="offline">{OFFLINE_NOW_SENTENCE}</NowNote>}
          <NowNote id="configured">{configuredNowLine(data.configured_now)}</NowNote>
        </ul>
      </div>
      <div ref={measureRef} className="min-w-0">
        {view.axis && <EvidenceAxisRow axis={view.axis} width={width} />}
        <ol data-lanes="" className="m-0 list-none space-y-3 p-0">
          {view.lanes.map((lane) => (
            <ObservabilityEvidenceLane
              key={lane.lane.source}
              view={lane}
              axis={view.axis}
              width={width}
              runWindow={view.runWindow}
              onActive={setActive}
            />
          ))}
        </ol>
        {view.axis && (
          <>
            {/* A no-break space while idle keeps the line's height (no layout shift on hover). */}
            <p data-evidence-tip="" aria-live="polite" className="m-0 mt-2 text-xs text-[var(--color-text)]">
              {active ?? ' '}
            </p>
            <p data-evidence-caption="" className="m-0 mt-1 text-2xs text-[var(--color-text-muted)]">
              {view.runWindow ? RUN_WINDOW_CAPTION : NO_RUN_WINDOW_CAPTION}
            </p>
          </>
        )}
      </div>
    </div>
  )
}

export default function ObservabilityEvidenceCard({ runId }: { runId: string }) {
  const titleId = useId()
  const { data, isLoading, isError } = useObservabilityTimeline(runId)
  const view = useMemo(() => (data ? buildEvidenceView(data) : null), [data])
  return (
    <section
      aria-labelledby={titleId}
      data-observability-evidence=""
      data-axis-state={view?.axisState}
      className="card"
    >
      <h2 id={titleId} className="m-0 flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        <Radar className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        {CARD_TITLE}
      </h2>
      {data && view ? (
        <Body data={data} view={view} />
      ) : isError ? (
        <p data-observability-state="error" className="m-0 mt-3 text-sm text-[var(--ui-danger)]">
          {LOAD_ERROR}
        </p>
      ) : (
        <div data-observability-state="loading" className="flex justify-center py-6" aria-busy={isLoading || undefined}>
          <LoadingSpinner />
        </div>
      )}
    </section>
  )
}
