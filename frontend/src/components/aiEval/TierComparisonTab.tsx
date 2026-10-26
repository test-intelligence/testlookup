/**
 * The Tiers tab (W16 AGT-09 a / AGT-13): run G2 on the version-controlled samples plus the live pairs people
 * labelled, read the project's recorded comparisons, and label the live shadow pairs.
 *
 * The run posts `pairs: []`: a page holds no outputs for the synthetic corpus, so the live labels ARE its evidence
 * (`include_live_labels`, on by default). `Record the result` (on by default) persists the verdict — the evidence a
 * tier change is checked against, so it can be unticked. All Projects → the shared project prompt and no read.
 * Every server sentence and every value is React text.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Panel } from '@/components/ui/PanelHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { useTierComparisonRuns } from '@/hooks/useAiEval'
import { runTierComparison } from '@/services/aiEvalService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { SHADOW_AGENT_LABELS, TIER_ORDER, type TierComparisonResult } from '@/types/aiEval'
import { formatDay } from '@/utils/formatters'
import ShadowPairLabelling from './ShadowPairLabelling'
import {
  CHIP_CLASS,
  COPY,
  DEFAULT_DELTA,
  METRIC_LABELS,
  SMOKE_FLOOR,
  TIER_AGENTS,
  TONE_CLASS,
  agentFor,
  floorSentence,
  metricCell,
  samplesLine,
  serverMessage,
  verdictText,
  verdictTone,
} from './shadowPairModel'

const FIELD = 'flex min-w-0 flex-col gap-1 text-xs text-[var(--color-text-secondary)]'

function TierSelect({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className={FIELD}>
      <span>{label}</span>
      <select aria-label={label} className="input text-xs" value={value} onChange={(e) => onChange(e.target.value)}>
        {TIER_ORDER.map((tier) => <option key={tier} value={tier}>{tier}</option>)}
      </select>
    </label>
  )
}

interface Outcome {
  result: TierComparisonResult
  agentId: string
}

function ResultCard({ outcome }: { outcome: Outcome }) {
  const { result, agentId } = outcome
  const n = typeof result.sample_count === 'number' ? result.sample_count : 0
  const floor = floorSentence(agentId, n)
  // The smoke rule decides only between the agent's floor and 100 samples (root cause's floor IS 100).
  const smoke = n < SMOKE_FLOOR && agentFor(agentId).floor < SMOKE_FLOOR
  const metrics = Array.isArray(result.per_gate?.G2) ? result.per_gate.G2 : []
  return (
    <div className="space-y-2 border-t border-[var(--color-border)] p-4" data-tier-result={result.verdict}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx(CHIP_CLASS, TONE_CLASS[verdictTone(result.verdict)])} data-verdict={result.verdict}>
          {verdictText(result.verdict)}
        </span>
        <span className="text-xs text-[var(--color-text-secondary)]">{result.reason}</span>
      </div>
      <p className="m-0 text-xs text-[var(--color-text-muted)]" data-tier-samples="">
        {samplesLine(result.sample_count, result.live_labelled_pairs)}
      </p>
      {floor && <p className="m-0 text-xs text-[var(--color-text-secondary)]" data-tier-floor="">{floor}</p>}
      {smoke && <p className="m-0 text-xs text-[var(--color-text-secondary)]" data-tier-smoke="">{COPY.smoke}</p>}
      <div className="overflow-x-auto" data-table-scroller="tier-metrics">
        <Table density="compact" className="table-fixed text-xs" aria-label="G2 metrics">
          <colgroup>
            <col style={{ width: 151 }} />
            <col style={{ width: 70 }} />
            <col style={{ width: 70 }} />
            <col style={{ width: 70 }} />
            <col style={{ width: 40 }} />
          </colgroup>
          <thead>
            <tr>
              <Th>Metric</Th>
              <Th numeric>Value</Th>
              <Th numeric>95 % low</Th>
              <Th numeric>95 % high</Th>
              <Th numeric>n</Th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <Tr key={metric.name} interactive={false} data-tier-metric={metric.name}>
                <Td className="truncate" title={metric.name}>{METRIC_LABELS[metric.name] ?? metric.name}</Td>
                <Td numeric>{metricCell(metric, metric.value)}</Td>
                <Td numeric>{metricCell(metric, metric.ci_low)}</Td>
                <Td numeric>{metricCell(metric, metric.ci_high)}</Td>
                <Td numeric>{typeof metric.n === 'number' ? metric.n : '—'}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  )
}

function RunPanel({ projectId, onRecorded }: { projectId: string; onRecorded: () => void }) {
  const [agentId, setAgentId] = useState<string>(TIER_AGENTS[0].id)
  const [incumbent, setIncumbent] = useState('llm')
  const [candidate, setCandidate] = useState('slm')
  const [delta, setDelta] = useState(String(DEFAULT_DELTA))
  const [includeLive, setIncludeLive] = useState(true)
  const [persist, setPersist] = useState(true)
  const [running, setRunning] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [error, setError] = useState<string | null>(null)
  const deltaValue = Number(delta)
  const deltaValid = delta.trim() !== '' && Number.isFinite(deltaValue) && deltaValue >= 0 && deltaValue <= 1

  const run = async () => {
    const agent = agentFor(agentId)
    setRunning(true)
    setError(null)
    try {
      const result = await runTierComparison({
        project_id: projectId,
        agent_id: agent.id,
        capability: agent.capability,
        incumbent_tier: incumbent,
        candidate_tier: candidate,
        delta: deltaValue,
        pairs: [],
        include_live_labels: includeLive,
        persist,
      })
      setOutcome({ result, agentId: agent.id })
      if (persist) onRecorded()
    } catch (err) {
      setOutcome(null)
      setError(serverMessage(err, COPY.runFailed))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Panel title={COPY.runTitle} subtitle={COPY.runSubtitle} data-testid="tier-run-panel">
      <div className="space-y-3 p-4">
        <div className="grid grid-cols-3 gap-3">
          <label className={FIELD}>
            <span>Agent</span>
            <select aria-label="Agent" className="input text-xs" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              {TIER_AGENTS.map((agent) => <option key={agent.id} value={agent.id}>{agent.label}</option>)}
            </select>
          </label>
          <TierSelect label="Incumbent tier" value={incumbent} onChange={setIncumbent} />
          <TierSelect label="Candidate tier" value={candidate} onChange={setCandidate} />
        </div>
        <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
          <label className={FIELD}>
            <span>Delta</span>
            <input
              aria-label="Delta"
              type="number"
              min={0}
              max={1}
              step={0.01}
              className="input w-24 text-xs"
              value={delta}
              onChange={(e) => setDelta(e.target.value)}
            />
          </label>
          <label className="inline-flex items-center gap-1.5 pb-2 text-xs text-[var(--color-text)]">
            <input type="checkbox" checked={includeLive} onChange={(e) => setIncludeLive(e.target.checked)} />
            Include live labels
          </label>
          <label className="inline-flex items-center gap-1.5 pb-2 text-xs text-[var(--color-text)]" title={COPY.recordHint}>
            <input type="checkbox" checked={persist} onChange={(e) => setPersist(e.target.checked)} />
            Record the result
          </label>
          <button
            type="button"
            className="btn-primary btn-sm mb-1"
            disabled={running || !deltaValid}
            onClick={() => { void run() }}
          >
            {running ? 'Running…' : 'Run comparison'}
          </button>
        </div>
        <p className="m-0 text-2xs text-[var(--color-text-muted)]">{COPY.recordHint}</p>
        {!deltaValid && <p className="m-0 text-xs text-[var(--ui-danger-ink)]">Delta must be a number from 0 to 1.</p>}
        {error && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-tier-error="">{error}</p>}
      </div>
      {outcome && <ResultCard outcome={outcome} />}
    </Panel>
  )
}

function HistoryPanel({ history }: { history: ReturnType<typeof useTierComparisonRuns> }) {
  const { data, error, isLoading, mutate } = history
  const rows = Array.isArray(data?.items) ? data.items : []
  const total = typeof data?.total === 'number' ? data.total : rows.length
  return (
    <Panel title={COPY.historyTitle} actions={rows.length > 0 ? <span>{total} recorded</span> : undefined}>
      {isLoading && !data ? (
        <div className="flex justify-center p-4"><LoadingSpinner size="sm" /></div>
      ) : error && !data ? (
        <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">
          {COPY.loadError.history}{' '}
          <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
        </p>
      ) : rows.length === 0 ? (
        <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">{COPY.historyEmpty}</p>
      ) : (
        <div className="overflow-x-auto" data-table-scroller="tier-history">
          <Table density="compact" className="table-fixed text-xs" aria-label="Recorded tier comparisons">
            <colgroup>
              <col style={{ width: 94 }} />
              <col style={{ width: 137 }} />
              <col style={{ width: 70 }} />
              <col style={{ width: 90 }} />
              <col style={{ width: 60 }} />
            </colgroup>
            <thead>
              <tr>
                <Th>Evaluated</Th>
                <Th>Agent</Th>
                <Th>Tiers</Th>
                <Th>Verdict</Th>
                <Th numeric>Samples</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const agent = row.agent_id ? SHADOW_AGENT_LABELS[row.agent_id] ?? row.agent_id : '—'
                return (
                  <Tr key={row.id} interactive={false} data-tier-run={row.id}>
                    <Td className="truncate">{formatDay(row.evaluated_at)}</Td>
                    <Td className="truncate" title={row.agent_id ?? undefined}>{agent}</Td>
                    <Td className="truncate">{`${row.baseline_tier ?? '—'} → ${row.candidate_tier ?? '—'}`}</Td>
                    <Td>
                      <span className={clsx(CHIP_CLASS, TONE_CLASS[verdictTone(row.status)])} data-verdict={row.status}>
                        {verdictText(row.status)}
                      </span>
                    </Td>
                    <Td numeric>{typeof row.sample_count === 'number' ? row.sample_count : '—'}</Td>
                  </Tr>
                )
              })}
            </tbody>
          </Table>
        </div>
      )}
    </Panel>
  )
}

function TiersBody({ projectId }: { projectId: string }) {
  const history = useTierComparisonRuns(projectId)
  return (
    <div className="space-y-4" data-testid="ai-eval-tiers-tab">
      <div className="space-y-4" data-tier-comparison="">
        <RunPanel projectId={projectId} onRecorded={() => { void history.mutate() }} />
        <HistoryPanel history={history} />
      </div>
      <ShadowPairLabelling projectId={projectId} />
    </div>
  )
}

export default function TierComparisonTab({ projectId }: { projectId: string | null }) {
  if (!projectId || projectId === ALL_PROJECTS_ID) return <ProjectRequiredEmptyState description={COPY.projectRequired} />
  return <TiersBody projectId={projectId} />
}
