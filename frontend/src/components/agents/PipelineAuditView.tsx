/**
 * W14 AGT-05: the Audit tab of a pipeline — the replay document
 * (`GET /agents/pipelines/{id}/replay`), read only while this tab is shown.
 *
 * Gaps are DATA: an old pipeline whose every gap list is full still renders
 * its whole document, with the gaps panel in the warning tint (never the error
 * colour, never an alert). Everything the agents wrote — stage names, route
 * decisions, event details, checksums — is React text; a JSON blob is
 * `JSON.stringify` inside `<pre>`.
 */
import { useState } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { usePipelineReplay } from '@/hooks/useAgentOps'
import {
  AUDIT_EVENTS_SHOWN,
  AUDIT_LOAD_ERROR,
  analysisModeLine,
  auditHeader,
  decisionDetails,
  gapLines,
  scalarText,
  shortChecksum,
} from './pipelineViewsModel'

function Checksum({ value }: { value: string | null | undefined }) {
  return <span className="font-mono" title={value ?? undefined}>{shortChecksum(value)}</span>
}

function Flag({ on }: { on: boolean }) {
  return <span aria-label={on ? 'yes' : 'no'}>{on ? '✓' : '—'}</span>
}

export default function PipelineAuditView({ pipelineId, live }: { pipelineId: string; live: boolean }) {
  const { data: replay, error, mutate } = usePipelineReplay(pipelineId, live)
  const [showAllEvents, setShowAllEvents] = useState(false)

  if (!replay) {
    if (error) {
      return (
        <div data-audit-error="" className="space-y-2 px-4 py-5">
          <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{AUDIT_LOAD_ERROR}</p>
          <button type="button" className="btn-secondary text-xs" onClick={() => void mutate()}>Retry</button>
        </div>
      )
    }
    return <div className="flex justify-center py-8"><LoadingSpinner /></div>
  }

  const gaps = replay.replayable ? [] : gapLines(replay.audit_gaps)
  const events = [...(replay.events ?? [])].sort((a, b) => String(b.timestamp ?? '').localeCompare(String(a.timestamp ?? '')))
  const shownEvents = showAllEvents ? events : events.slice(0, AUDIT_EVENTS_SHOWN)
  const counts = Object.entries(replay.event_counts ?? {}).sort(([a], [b]) => a.localeCompare(b))

  return (
    <div data-audit-view="" className="max-h-[600px] space-y-4 overflow-auto p-4">
      <div data-audit-header="" className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <strong className="font-semibold text-[var(--color-text)]">{auditHeader(replay)}</strong>
        <span className="text-xs text-[var(--color-text-muted)]">
          {analysisModeLine(replay.analysis_mode_requested, replay.analysis_mode_resolved)}
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          final state checksum <Checksum value={replay.final_state_checksum_sha256} />
        </span>
      </div>

      {gaps.length > 0 && (
        <section
          aria-label="Audit gaps"
          data-audit-gaps=""
          className="rounded-md border border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] px-3 py-2 text-xs text-[var(--ui-warning-ink)]"
        >
          <h4 className="eyebrow m-0 mb-1">Audit gaps</h4>
          <ul className="m-0 list-none space-y-0.5 p-0">
            {gaps.map(line => <li key={line} className="break-words">{line}</li>)}
          </ul>
        </section>
      )}

      <section aria-label="Stage replay">
        <h4 className="eyebrow m-0 mb-1">Stages</h4>
        <Table density="dense" data-audit-stages="">
          <thead>
            <tr>
              <Th>Stage</Th>
              <Th>Status</Th>
              <Th>Input checksum</Th>
              <Th>Output checksum</Th>
              <Th>Checkpoint</Th>
              <Th>Restored</Th>
              <Th numeric>Decisions</Th>
            </tr>
          </thead>
          <tbody>
            {(replay.stage_replay ?? []).map((stage, index) => (
              <Tr key={`${stage.stage_name}-${index}`} interactive={false}>
                <Td className="min-w-0 break-words font-mono text-xs">{stage.stage_name}</Td>
                <Td className="text-xs">{stage.status}</Td>
                <Td className="text-xs"><Checksum value={stage.input_checksum_sha256} /></Td>
                <Td className="text-xs"><Checksum value={stage.output_checksum_sha256} /></Td>
                <Td className="text-xs"><Flag on={Boolean(stage.checkpoint_available)} /></Td>
                <Td className="text-xs"><Flag on={Boolean(stage.restored_from_checkpoint)} /></Td>
                <Td numeric className="text-xs">{stage.decision_count ?? 0}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </section>

      <section aria-label="Route decisions">
        <h4 className="eyebrow m-0 mb-1">Route decisions</h4>
        {(replay.route_decisions ?? []).length === 0 ? (
          <p className="m-0 text-xs text-[var(--color-text-muted)]">No route decision was recorded.</p>
        ) : (
          <ul className="m-0 list-none space-y-1 p-0 text-xs">
            {(replay.route_decisions ?? []).map((decision, index) => (
              <li key={index} data-route-decision="" className="border-b border-[var(--color-border)] py-1">
                <span className="font-mono text-[var(--color-text-muted)]">{scalarText(decision.at)}</span>
                {' · '}
                <span className="font-semibold text-[var(--color-text)]">{scalarText(decision.decision_point)}</span>
                {' → '}
                <span className="font-mono">{scalarText(decision.chosen)}</span>
                {decisionDetails(decision).map(line => (
                  <span key={line} className="block break-words text-[var(--color-text-muted)]">{line}</span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Memory references">
        <h4 className="eyebrow m-0 mb-1">Memory references</h4>
        {(replay.memory_references ?? []).length === 0 ? (
          <p className="m-0 text-xs text-[var(--color-text-muted)]">No memory was referenced.</p>
        ) : (
          <Table density="dense">
            <thead>
              <tr><Th>Entity</Th><Th>Entity id</Th><Th>Payload</Th><Th>Reference</Th></tr>
            </thead>
            <tbody>
              {(replay.memory_references ?? []).map(ref => (
                <Tr key={ref.memory_entry_id} interactive={false}>
                  <Td className="text-xs">{ref.entity_type}</Td>
                  <Td className="break-all font-mono text-xs">{ref.entity_id}</Td>
                  <Td className="text-xs"><Checksum value={ref.payload_sha256} /></Td>
                  <Td className="break-all font-mono text-xs">{ref.memory_reference_id ?? '—'}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>

      <section aria-label="Events">
        <h4 className="eyebrow m-0 mb-1">Events</h4>
        {counts.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {counts.map(([type, n]) => (
              <span key={type} data-event-count="" className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 py-0.5 font-mono text-2xs text-[var(--color-text-secondary)]">
                {type} × {n}
              </span>
            ))}
          </div>
        )}
        {events.length === 0 ? (
          <p className="m-0 text-xs text-[var(--color-text-muted)]">No event was recorded.</p>
        ) : (
          <ul className="m-0 list-none space-y-1 p-0 text-xs">
            {shownEvents.map((event, index) => (
              <li key={`${event.event_type}-${String(event.timestamp)}-${index}`} data-audit-event="" className="border-b border-[var(--color-border)] py-1">
                <span className="font-mono">{event.event_type}</span>
                {' · '}
                <span>{event.stage_name ?? '—'}</span>
                {' · '}
                <span className="text-[var(--color-text-muted)]">{scalarText(event.timestamp)}</span>
                <details className="mt-0.5">
                  <summary className="cursor-pointer text-[var(--color-text-muted)]">detail</summary>
                  <pre className="m-0 mt-1 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-2 font-mono text-2xs">
                    {JSON.stringify(event.detail ?? {}, null, 2)}
                  </pre>
                </details>
              </li>
            ))}
          </ul>
        )}
        {!showAllEvents && events.length > AUDIT_EVENTS_SHOWN && (
          <button type="button" className="btn-secondary mt-2 text-xs" onClick={() => setShowAllEvents(true)}>
            Show all {events.length}
          </button>
        )}
      </section>
    </div>
  )
}
