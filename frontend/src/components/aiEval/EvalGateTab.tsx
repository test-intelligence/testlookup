/**
 * The Gate tab (W16 AGT-09): the four pre-release gates, each with an optional completed replay run as its
 * candidate, the result card, and the active baselines.
 *
 * Two defects of the old tab are fixed here: it compared the verdict with `PASS` / `FAIL` while the server answers
 * `pass` / `fail` / `insufficient_samples` (every run toasted "No baseline configured"), and its Duplicate Detection
 * button asked `ClusterAgent` while the agent stack gates `DefectPromotionAgent` — a baseline set for the stack was
 * never found. The four buttons now come from `DEFAULT_GATES`, the mirror of `DEFAULT_AGENT_STACK_GATES`.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Panel } from '@/components/ui/PanelHeader'
import { useEvalRuns } from '@/hooks/useAiEval'
import { type EvalBaseline, type EvalGateResult, runPreReleaseGate } from '@/services/aiEvalService'
import { DEFAULT_GATES } from '@/types/aiEval'
import { formatMonthDay } from '@/utils/formatters'
import GateResultCard from './GateResultCard'
import {
  CHIP_CLASS,
  COPY,
  TONE_CLASS,
  type GateSpec,
  metricChip,
  percent,
  tierLabel,
  refusalText,
} from './aiEvalModel'

export interface EvalGateTabProps {
  baselines: EvalBaseline[]
  loading: boolean
  isAdmin: boolean
  /** The last gate answer (kept by the page, so the workflow timeline reads it and it survives a tab switch). */
  result: EvalGateResult | null
  onResult: (result: EvalGateResult) => void
  onBaselineSet: () => void
}

interface GateRowProps {
  gate: GateSpec
  isAdmin: boolean
  running: boolean
  disabled: boolean
  onRun: (gate: GateSpec, candidateRunId: string | null) => void
}

/** One gate: its button and its `Candidate run` select (the task's completed replay runs; none = legacy). */
function GateRow({ gate, isAdmin, running, disabled, onRun }: GateRowProps) {
  const [candidate, setCandidate] = useState('')
  const { data } = useEvalRuns(
    { task_type: gate.task_type, metric_kind: 'replay', status: 'completed', limit: 20 },
    true,
  )
  const runs = Array.isArray(data?.items) ? data.items : []
  const selectId = `gate-candidate-${gate.task_type}`
  return (
    <div data-gate={gate.task_type} className="flex flex-col gap-1.5">
      <button
        type="button"
        className="btn-secondary inline-flex w-fit items-center gap-1.5"
        disabled={!isAdmin || disabled}
        title={isAdmin ? undefined : COPY.needsAdmin}
        onClick={() => onRun(gate, candidate || null)}
      >
        {running && <LoadingSpinner size="sm" />}
        Gate: {gate.label}
      </button>
      <label htmlFor={selectId} className="text-xs text-[var(--color-text-secondary)]">Candidate run</label>
      <select
        id={selectId}
        className="input w-72 text-xs"
        value={candidate}
        onChange={(event) => setCandidate(event.target.value)}
      >
        <option value="">Legacy label agreement</option>
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            {tierLabel(run.candidate?.tier)} · {percent(run.accuracy)} · {formatMonthDay(run.completed_at ?? run.evaluated_at)} · {run.id.slice(0, 8)}
          </option>
        ))}
      </select>
    </div>
  )
}

function BaselineRow({ baseline }: { baseline: EvalBaseline }) {
  const chip = metricChip({ metric_kind: baseline.metric_kind ?? 'label_agreement' })
  return (
    <li data-baseline={baseline.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2 text-sm">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-[var(--color-text)]">{baseline.agent_name}</span>
        <span className="text-xs text-[var(--color-text-muted)]">
          {baseline.task_type} / {baseline.prompt_version}
        </span>
        <span title={chip.title} className={clsx(CHIP_CLASS, TONE_CLASS[chip.tone])}>{chip.label}</span>
      </div>
      <div className="flex gap-3 text-xs text-[var(--color-text-muted)]">
        <span>Acc: {percent(baseline.baseline_accuracy)}</span>
        <span>Min: {(baseline.min_accuracy * 100).toFixed(0)}%</span>
        <span>Max drop: {baseline.max_regression_pct.toFixed(1)}%</span>
      </div>
    </li>
  )
}

export default function EvalGateTab({ baselines, loading, isAdmin, result, onResult, onBaselineSet }: EvalGateTabProps) {
  const [running, setRunning] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const gates = DEFAULT_GATES as readonly GateSpec[]
  const rows = Array.isArray(baselines) ? baselines : []

  const run = async (gate: GateSpec, candidateRunId: string | null) => {
    setRunning(gate.task_type)
    setRefusal(null)
    try {
      onResult(await runPreReleaseGate(gate.task_type, gate.agent_name, undefined, candidateRunId ?? undefined))
    } catch (err) {
      setRefusal(refusalText(err, 'The gate did not run.'))
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="space-y-4" data-testid="ai-eval-gate-tab">
      <Panel
        title="Run pre-release gate"
        subtitle="Compare a candidate with the active baseline. Prompt, model or routing changes should not ship on FAIL."
      >
        <div className="grid grid-cols-2 gap-3 p-4">
          {gates.map((gate) => (
            <GateRow
              key={gate.task_type}
              gate={gate}
              isAdmin={isAdmin}
              running={running === gate.task_type}
              disabled={running !== null}
              onRun={(g, id) => { void run(g, id) }}
            />
          ))}
        </div>
        {refusal && (
          <p role="alert" className="m-0 px-4 pb-4 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>
        )}
      </Panel>

      {result && <GateResultCard result={result} isAdmin={isAdmin} onBaselineSet={onBaselineSet} />}

      <Panel title="Active baselines">
        {loading ? (
          <div className="flex justify-center p-4"><LoadingSpinner size="sm" /></div>
        ) : rows.length === 0 ? (
          <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">{COPY.noBaselines}</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-[var(--color-border)] p-0">
            {rows.map((baseline) => <BaselineRow key={baseline.id} baseline={baseline} />)}
          </ul>
        )}
      </Panel>
    </div>
  )
}
