/**
 * A dataset's evaluation runs (W16 AGT-09 b), opened under its row: legacy label-agreement runs (never recomputed,
 * labelled so) and candidate replays. A queued / running replay shows its progress and the list polls until it is
 * terminal (the hook's `refreshInterval`); a terminal replay shows its status, accuracy and F1 (`—` + `Not measured`
 * when nothing was scored — never 0%), its counts and its error sentence.
 *
 * ADMIN, on a replay: `Items` (the per-item diff, inline), and on a COMPLETED replay `Gate` (the pre-release gate for
 * this run, the result card inline) and `Set as baseline` (an InlineConfirm). A QA lead sees them disabled.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { useEvalRuns, useSetBaseline } from '@/hooks/useAiEval'
import { type AIEvalDataset, type AIEvalRun, type EvalGateResult, runPreReleaseGate } from '@/services/aiEvalService'
import { formatDay } from '@/utils/formatters'
import EvalRunItems from './EvalRunItems'
import GateResultCard from './GateResultCard'
import {
  CHIP_CLASS,
  COPY,
  TONE_CLASS,
  baselineConfirmSentence,
  gateFor,
  isActiveRun,
  isMeasured,
  isReplay,
  metricChip,
  metricText,
  refusalText,
  runCounts,
  runMetric,
  runProgress,
  runStatusChip,
} from './aiEvalModel'

export const NOT_COMPLETE_TITLE = 'Only a completed replay run can be gated or set as a baseline.'

type Panel = 'items' | 'gate' | 'confirm' | null

function Chip({ label, tone, title }: { label: string; tone: keyof typeof TONE_CLASS; title?: string }) {
  return <span title={title} className={clsx(CHIP_CLASS, TONE_CLASS[tone])}>{label}</span>
}

function RunRow({ run, taskType, isAdmin, onBaselineSet }: { run: AIEvalRun; taskType: string; isAdmin: boolean; onBaselineSet: () => void }) {
  const [panel, setPanel] = useState<Panel>(null)
  const [gate, setGate] = useState<EvalGateResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const setBaseline = useSetBaseline()
  const replay = isReplay(run)
  const active = isActiveRun(run)
  const completed = replay && run.status === 'completed'
  const agent = gateFor(taskType)?.agent_name ?? 'AnalysisAgent'
  const kind = metricChip(run)
  const status = runStatusChip(run.status)
  const accuracy = runMetric(run, 'accuracy')
  const f1 = runMetric(run, 'f1_score')
  const toggle = (next: Panel) => { setNote(null); setPanel(panel === next ? null : next) }

  const runGate = async () => {
    setBusy(true)
    setNote(null)
    try {
      setGate(await runPreReleaseGate(taskType, agent, undefined, run.id))
      setPanel('gate')
    } catch (err) {
      setNote({ ok: false, text: refusalText(err, 'The gate did not run.') })
    } finally {
      setBusy(false)
    }
  }

  const confirmBaseline = async () => {
    setBusy(true)
    try {
      await setBaseline({ task_type: taskType, agent_name: agent, eval_run_id: run.id })
      setNote({ ok: true, text: `Baseline set for ${agent}.` })
      setPanel(null)
      onBaselineSet()
    } catch (err) {
      setNote({ ok: false, text: refusalText(err, 'The baseline was not set.') })
      setPanel(null)
    } finally {
      setBusy(false)
    }
  }

  const adminTitle = (enabled: boolean) => (!isAdmin ? COPY.needsAdmin : enabled ? undefined : NOT_COMPLETE_TITLE)

  return (
    <li data-run-row={run.id} data-run-status={run.status ?? 'completed'} className="space-y-2 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-muted)]">
        {replay ? <Chip {...kind} /> : <Chip label={COPY.legacyMetric} tone="muted" title={COPY.legacyMetric} />}
        <Chip {...status} />
        {active ? (
          <span data-run-progress="" className="inline-flex items-center gap-1.5">
            <LoadingSpinner size="sm" />
            {runProgress(run)}
          </span>
        ) : (
          <>
            <span>Acc {metricText(accuracy)}</span>
            <span>F1 {metricText(f1)}</span>
            {(!isMeasured(accuracy) || !isMeasured(f1)) && <span data-not-measured="">{COPY.notMeasured}</span>}
            {replay ? <span>{runCounts(run)}</span> : <span>{run.total_items} items</span>}
            {run.items_capped && <span>first {run.total_items} items</span>}
          </>
        )}
        <span>{formatDay(run.completed_at ?? run.evaluated_at)}</span>
      </div>
      {run.error_text && <p className="m-0 text-xs text-[var(--ui-warning-ink)]">{run.error_text}</p>}
      {replay && !active && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-secondary btn-sm" aria-expanded={panel === 'items'} disabled={!isAdmin} title={adminTitle(true)} onClick={() => toggle('items')}>
            Items
          </button>
          <button type="button" className="btn-secondary btn-sm inline-flex items-center gap-1.5" disabled={!isAdmin || !completed || busy} title={adminTitle(completed)} onClick={() => { void runGate() }}>
            {busy && panel !== 'confirm' && <LoadingSpinner size="sm" />}
            Gate
          </button>
          <button type="button" className="btn-secondary btn-sm" disabled={!isAdmin || !completed || busy} title={adminTitle(completed)} onClick={() => toggle('confirm')}>
            Set as baseline
          </button>
          {note && (
            <span role={note.ok ? 'status' : 'alert'} className={clsx('text-xs', note.ok ? 'text-[var(--ui-success-ink)]' : 'text-[var(--ui-danger-ink)]')}>
              {note.text}
            </span>
          )}
        </div>
      )}
      {panel === 'confirm' && (
        <InlineConfirm
          message={baselineConfirmSentence(taskType, agent)}
          confirmLabel="Set as baseline"
          busy={busy}
          onConfirm={() => { void confirmBaseline() }}
          onCancel={() => setPanel(null)}
          testId="run-baseline-confirm"
        />
      )}
      {panel === 'gate' && gate && <GateResultCard result={gate} isAdmin={isAdmin} onBaselineSet={onBaselineSet} />}
      {panel === 'items' && isAdmin && <EvalRunItems runId={run.id} taskType={taskType} />}
    </li>
  )
}

export default function EvalRunsList({ dataset, isAdmin, onBaselineSet }: { dataset: AIEvalDataset; isAdmin: boolean; onBaselineSet: () => void }) {
  const { data, error, isLoading, mutate } = useEvalRuns({ dataset_id: dataset.id, limit: 20 }, true)
  const runs = Array.isArray(data?.items) ? data.items : []
  const total = typeof data?.total === 'number' ? data.total : runs.length
  if (isLoading) return <div className="py-2"><LoadingSpinner size="sm" /></div>
  if (error) {
    return (
      <p className="m-0 py-2 text-xs text-[var(--color-text-muted)]">
        {COPY.loadError.runs}{' '}
        <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
      </p>
    )
  }
  if (runs.length === 0) return <p className="m-0 py-2 text-xs text-[var(--color-text-muted)]">{COPY.runsEmpty}</p>
  return (
    <div data-runs-list={dataset.id}>
      <p className="eyebrow m-0 text-[var(--color-text-secondary)]">
        {typeof data?.total === 'number' ? `${total} runs` : `At least ${total} runs`}
      </p>
      <ul className="m-0 list-none divide-y divide-[var(--color-border)] p-0">
        {runs.map((run) => (
          <RunRow key={run.id} run={run} taskType={dataset.task_type} isAdmin={isAdmin} onBaselineSet={onBaselineSet} />
        ))}
      </ul>
    </div>
  )
}
