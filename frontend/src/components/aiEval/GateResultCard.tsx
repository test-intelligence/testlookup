/**
 * One pre-release gate answer (W16 AGT-09): the server's verdict in its own words (`pass` / `fail` /
 * `insufficient_samples` → PASS / FAIL / Not enough evidence — the old card compared `PASS` and was never coloured),
 * the metric kind, the candidate and baseline runs, the four metrics and every rule with its detail.
 *
 * ADMIN: `Set as baseline` asks inline (InlineConfirm) and sets a baseline of the result's OWN metric kind (§7.2
 * D-17): a replay result names its run (`eval_run_id`), a legacy result sends today's `{task_type, agent_name}` body.
 * A refusal shows the server's sentence here. Every value is React text.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import MetricCard from '@/components/ui/MetricCard'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { useSetBaseline } from '@/hooks/useAiEval'
import type { EvalGateResult } from '@/services/aiEvalService'
import {
  CHIP_CLASS,
  COPY,
  TONE_CLASS,
  baselineBody,
  baselineConfirmSentence,
  metricChip,
  percent,
  refusalText,
  shortChecksum,
  verdictLabel,
  verdictTone,
} from './aiEvalModel'

export interface GateResultCardProps {
  result: EvalGateResult
  isAdmin: boolean
  /** After a baseline was set from this result (the page re-reads the baselines). */
  onBaselineSet?: () => void
}

function RunRef({ label, id }: { label: string; id: string | null | undefined }) {
  if (!id) return null
  return (
    <span>
      {label}{' '}
      <code title={id} className="font-mono text-[var(--color-text-secondary)]">{shortChecksum(id)}</code>
    </span>
  )
}

export default function GateResultCard({ result, isAdmin, onBaselineSet }: GateResultCardProps) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const setBaseline = useSetBaseline()
  const kind = metricChip({ metric_kind: result.metric_kind ?? 'label_agreement' })
  const metrics = result.current_metrics
  const rules = Array.isArray(result.rule_results) ? result.rule_results : []

  const saveBaseline = async () => {
    setBusy(true)
    try {
      await setBaseline(baselineBody(result))
      setNote({ ok: true, text: `Baseline set for ${result.agent_name}.` })
      setConfirming(false)
      onBaselineSet?.()
    } catch (err) {
      setNote({ ok: false, text: refusalText(err, 'The baseline was not set.') })
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card space-y-3" data-gate-result={result.status} data-testid="gate-result">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="m-0 text-base font-semibold text-[var(--color-text)]">Gate result</h3>
          <span data-verdict={result.status} className={clsx(CHIP_CLASS, 'text-xs', TONE_CLASS[verdictTone(result.status)])}>
            {verdictLabel(result.status)}
          </span>
          <span title={kind.title} className={clsx(CHIP_CLASS, TONE_CLASS[kind.tone])}>{kind.label}</span>
        </div>
        <span className="text-xs text-[var(--color-text-muted)]">
          {result.agent_name} / {result.task_type}
        </span>
      </div>

      {(result.candidate_run_id || result.baseline_run_id) && (
        <p className="m-0 flex flex-wrap gap-x-3 text-xs text-[var(--color-text-muted)]">
          <RunRef label="Candidate run" id={result.candidate_run_id} />
          <RunRef label="Baseline run" id={result.baseline_run_id} />
        </p>
      )}

      {metrics && (
        <div className="grid grid-cols-4 gap-2">
          <MetricCard size="sm" title="Accuracy" metric={{ value: percent(metrics.accuracy) }} />
          <MetricCard size="sm" title="Precision" metric={{ value: percent(metrics.precision) }} />
          <MetricCard size="sm" title="Recall" metric={{ value: percent(metrics.recall) }} />
          <MetricCard size="sm" title="F1" metric={{ value: percent(metrics.f1_score) }} />
        </div>
      )}

      <div>
        <h4 className="eyebrow m-0 mb-1 text-[var(--color-text-secondary)]">Rules</h4>
        <ul className="m-0 list-none space-y-1 p-0">
          {rules.map((rule, i) => (
            <li key={`${rule.rule}-${i}`} data-rule={rule.rule} data-passed={rule.passed ? 'true' : 'false'} className="flex items-start gap-2 text-xs">
              <span className={clsx(CHIP_CLASS, TONE_CLASS[rule.passed ? 'success' : 'danger'])}>{rule.passed ? 'PASS' : 'FAIL'}</span>
              <span className="shrink-0 font-mono text-[var(--color-text-secondary)]">{rule.rule}</span>
              <span className="min-w-0 text-[var(--color-text-muted)]">{rule.detail}</span>
            </li>
          ))}
        </ul>
      </div>

      {confirming ? (
        <InlineConfirm
          message={baselineConfirmSentence(result.task_type, result.agent_name)}
          confirmLabel="Set as baseline"
          busy={busy}
          onConfirm={() => { void saveBaseline() }}
          onCancel={() => setConfirming(false)}
          testId="gate-baseline-confirm"
        />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={!isAdmin}
            title={isAdmin ? undefined : COPY.needsAdmin}
            onClick={() => { setNote(null); setConfirming(true) }}
          >
            Set as baseline
          </button>
          {note && (
            <span role={note.ok ? 'status' : 'alert'} className={clsx('text-xs', note.ok ? 'text-[var(--ui-success-ink)]' : 'text-[var(--ui-danger-ink)]')}>
              {note.text}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
