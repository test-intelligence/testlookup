/**
 * The Agent stack tab (W16 AGT-09 a): the shipped agent-stack release gate (`POST /ai-eval/agent-stack-release-gate`,
 * ADMIN) and its history (`GET …/runs`, QA lead+) — both had no page. A run answers the overall verdict, one row per
 * required gate, the blocking gates and the manifest checksum; a history row opens its manifest INLINE
 * (`GET /ai-eval/gates/{checksum}`).
 *
 * The history read is `{items, total}` and only agent-stack rows (the server filters `gate_type IS NULL`: it used to
 * list every project's tier comparisons to any QA lead). A QA lead reads history and manifests; Run is disabled with
 * `Needs an administrator.`. Every value is React text; version maps are shown as `<code>` text.
 */
import { Fragment, useState } from 'react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Panel } from '@/components/ui/PanelHeader'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { useAgentStackGateRuns, useEvalManifest } from '@/hooks/useAiEval'
import { runAgentStackGate } from '@/services/aiEvalService'
import { formatDay } from '@/utils/formatters'
import {
  CHIP_CLASS,
  COPY,
  TONE_CLASS,
  defaultChangeId,
  percent,
  refusalText,
  shortChecksum,
  taskLabel,
  verdictChip,
} from './aiEvalModel'

type Json = Record<string, unknown>

function asRecord(value: unknown): Json {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {}
}

function asList(value: unknown): Json[] {
  return Array.isArray(value) ? value.map(asRecord) : []
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—'
}

function Verdict({ status }: { status: unknown }) {
  const chip = verdictChip(typeof status === 'string' ? status : null)
  return (
    <span data-verdict={String(status ?? '')} className={clsx(CHIP_CLASS, TONE_CLASS[chip.tone])}>
      {chip.label}
    </span>
  )
}

/** A `{agent: version}` map as `<code>` lines; empty → `—`. */
function VersionMap({ value }: { value: unknown }) {
  const entries = Object.entries(asRecord(value))
  if (entries.length === 0) return <span>—</span>
  return (
    <span className="flex flex-col gap-0.5">
      {entries.map(([key, version]) => (
        <code key={key} className="break-all font-mono">{key}: {text(version)}</code>
      ))}
    </span>
  )
}

/** The manifest of one history row, opened inline under it. */
function ManifestView({ checksum }: { checksum: string }) {
  const { data, error, isLoading, mutate } = useEvalManifest(checksum)
  if (isLoading) return <LoadingSpinner size="sm" />
  if (error || !data) {
    return (
      <p className="m-0 text-xs text-[var(--color-text-muted)]">
        {COPY.loadError.manifest}{' '}
        <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
      </p>
    )
  }
  const manifest = asRecord(data.manifest)
  const gates = asList(manifest.required_gates)
  return (
    <dl data-manifest={checksum} className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
      <dt className="text-[var(--color-text-secondary)]">Checksum</dt>
      <dd className="m-0 break-all font-mono">{text(data.eval_manifest_checksum)}</dd>
      <dt className="text-[var(--color-text-secondary)]">Source</dt>
      <dd className="m-0">{text(data.source)}</dd>
      <dt className="text-[var(--color-text-secondary)]">Status</dt>
      <dd className="m-0"><Verdict status={data.status} /></dd>
      <dt className="text-[var(--color-text-secondary)]">Prompt versions</dt>
      <dd className="m-0"><VersionMap value={manifest.prompt_versions} /></dd>
      <dt className="text-[var(--color-text-secondary)]">Model versions</dt>
      <dd className="m-0"><VersionMap value={manifest.model_versions} /></dd>
      <dt className="text-[var(--color-text-secondary)]">Routing versions</dt>
      <dd className="m-0"><VersionMap value={manifest.routing_versions} /></dd>
      <dt className="text-[var(--color-text-secondary)]">Required gates</dt>
      <dd className="m-0">
        {gates.length === 0 ? '—' : gates.map((gate) => `${taskLabel(text(gate.task_type))} · ${text(gate.agent_name)}`).join(', ')}
      </dd>
    </dl>
  )
}

/** A run's answer: the overall verdict, the checksum, the blocking gates and one row per gate. */
function RunResult({ result }: { result: Json }) {
  const manifest = asRecord(result.manifest)
  const checksum = text(manifest.manifest_checksum_sha256)
  const gates = asList(result.gate_results)
  const blocking = asList(result.blocking_gates)
  return (
    <div data-testid="agent-stack-result" className="space-y-3 border-t border-[var(--color-border)] p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-muted)]">
        <span className="text-sm font-semibold text-[var(--color-text)]">Overall</span>
        <Verdict status={result.status} />
        <span>
          Checksum <code title={checksum} className="font-mono text-[var(--color-text-secondary)]">{shortChecksum(checksum)}</code>
        </span>
        <span>
          {blocking.length === 0
            ? 'No blocking gate'
            : `Blocking: ${blocking.map((gate) => taskLabel(text(gate.task_type))).join(', ')}`}
        </span>
      </div>
      <div className="overflow-x-auto" data-table-scroller="agent-stack-gates">
      <Table density="compact" className="table-fixed text-xs" aria-label="Agent-stack gates">
        <colgroup>
          <col style={{ width: 130 }} />
          <col style={{ width: 160 }} />
          <col style={{ width: 156 }} />
          <col style={{ width: 80 }} />
          <col style={{ width: 56 }} />
          <col style={{ width: 60 }} />
        </colgroup>
        <thead>
          <tr>
            <Th>Task</Th>
            <Th>Agent</Th>
            <Th>Status</Th>
            <Th numeric>Accuracy</Th>
            <Th numeric>F1</Th>
            <Th numeric>Items</Th>
          </tr>
        </thead>
        <tbody>
          {gates.map((gate, i) => {
            const metrics = asRecord(gate.current_metrics)
            const task = text(gate.task_type)
            return (
              <Tr key={`${task}-${i}`} interactive={false} data-gate-row={task}>
                {/* Wraps, never cuts: "Duplicate Detection" lost its end beside DefectPromotionAgent on DejaVu. */}
                <Td className="whitespace-normal" title={task}>{taskLabel(task)}</Td>
                <Td className="truncate" title={text(gate.agent_name)}>{text(gate.agent_name)}</Td>
                <Td><Verdict status={gate.status} /></Td>
                <Td numeric>{percent(typeof metrics.accuracy === 'number' ? metrics.accuracy : null)}</Td>
                <Td numeric>{percent(typeof metrics.f1_score === 'number' ? metrics.f1_score : null)}</Td>
                <Td numeric>{text(metrics.total)}</Td>
              </Tr>
            )
          })}
        </tbody>
      </Table>
      </div>
    </div>
  )
}

/** The persisted agent-stack runs, newest first; a row opens its manifest inline. */
function History() {
  const { data, error, isLoading, mutate } = useAgentStackGateRuns(true)
  const [open, setOpen] = useState<string | null>(null)
  const rows = Array.isArray(data?.items) ? data.items : []
  const total = typeof data?.total === 'number' ? data.total : rows.length
  return (
    <Panel
      title="Gate history"
      subtitle="Every recorded agent-stack gate run; open a row for its manifest."
      actions={rows.length > 0 ? <span>{typeof data?.total === 'number' ? `${total} runs` : `at least ${total} runs`}</span> : undefined}
    >
      {isLoading ? (
        <div className="flex justify-center p-4"><LoadingSpinner size="sm" /></div>
      ) : error ? (
        <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">
          {COPY.loadError.stack}{' '}
          <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
        </p>
      ) : rows.length === 0 ? (
        <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">{COPY.stackHistoryEmpty}</p>
      ) : (
        <div className="overflow-x-auto" data-table-scroller="agent-stack-history">
        <Table density="compact" className="table-fixed text-xs" aria-label="Agent-stack gate history">
          <colgroup>
            <col style={{ width: 110 }} />
            <col style={{ width: 190 }} />
            <col style={{ width: 80 }} />
            <col style={{ width: 80 }} />
            <col style={{ width: 120 }} />
          </colgroup>
          <thead>
            <tr>
              <Th>Evaluated</Th>
              <Th>Change</Th>
              <Th>Status</Th>
              <Th numeric>Blocking</Th>
              <Th>Checksum</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const checksum = row.manifest_checksum_sha256
              const isOpen = open === row.id
              const blocking = Array.isArray(row.blocking_gates) ? row.blocking_gates.length : 0
              return (
                <Fragment key={row.id}>
                  <Tr selected={isOpen} data-history-row={row.id} onClick={() => setOpen(isOpen ? null : row.id)}>
                    <Td className="truncate">{formatDay(row.evaluated_at)}</Td>
                    <Td className="truncate" title={row.change_id}>{row.change_id}</Td>
                    <Td><Verdict status={row.status} /></Td>
                    <Td numeric>{blocking}</Td>
                    <Td>
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        title={checksum}
                        className="font-mono text-[var(--color-accent-ink)] hover:underline"
                        onClick={(event) => { event.stopPropagation(); setOpen(isOpen ? null : row.id) }}
                      >
                        {shortChecksum(checksum)}
                      </button>
                    </Td>
                  </Tr>
                  {isOpen && (
                    <tr data-manifest-row={row.id}>
                      <td colSpan={5} className="border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-3">
                        <ManifestView checksum={checksum} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </Table>
        </div>
      )}
    </Panel>
  )
}

export default function AgentStackTab({ isAdmin }: { isAdmin: boolean }) {
  const [changeId, setChangeId] = useState(() => defaultChangeId(new Date()))
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<Json | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const { mutate } = useAgentStackGateRuns(true)

  const run = async () => {
    setRunning(true)
    setRefusal(null)
    try {
      setResult(asRecord(await runAgentStackGate({ change_id: changeId.trim() })))
      void mutate()
    } catch (err) {
      setRefusal(refusalText(err, 'The agent-stack gate did not run.'))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-4" data-testid="ai-eval-stack-tab">
      <Panel
        title="Agent-stack release gate"
        subtitle="Run every required gate against its baseline before a prompt, model or routing change ships."
      >
        <div className="flex flex-wrap items-end gap-3 p-4">
          <label className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
            Change id
            <input
              className="input w-64"
              value={changeId}
              maxLength={120}
              onChange={(event) => setChangeId(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={!isAdmin || running || !changeId.trim()}
            title={isAdmin ? undefined : COPY.needsAdmin}
            onClick={() => { void run() }}
          >
            {running && <LoadingSpinner size="sm" />}
            Run gate
          </button>
          {refusal && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>}
        </div>
        {result && <RunResult result={result} />}
      </Panel>
      <History />
    </div>
  )
}
