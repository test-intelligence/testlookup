/**
 * Investigator cockpit (Agentic plan AI-1, Wave B — shadow mode).
 *
 * Renders as an "Agent Investigation" section on the Deep Investigation page:
 *   - "Investigate this run" CTA (tooltip-disabled when no run in scope, the
 *     project policy disables the agent, or the viewer lacks QA Engineer).
 *     409 on start → attach to the already-running investigation.
 *   - Hypothesis matrix: 5 fixed cards (Infrastructure / Commit-caused /
 *     Environment / Known-flaky / Regression) with live status, confidence +
 *     basis chip (same calibrated/estimated pattern as AIAnalysisPanel), a
 *     summary line, and an expandable evidence list (url_path → internal link).
 *   - Verdict panel when complete — clearly AI-labeled, recommended actions as
 *     text only (no action buttons this slice), and an explicit "shadow mode —
 *     no actions were taken" note.
 *   - Run controls: cancel while active; spend-vs-budget mini bars; a details
 *     footer with trigger, model, and prompt-registry versions.
 *   - Past investigations for the run (falling back to the project) that
 *     re-point the cockpit.
 *
 * Polling: useInvestigation refreshes every 2.5 s while the status is
 * queued/running/synthesizing and stops on terminal statuses.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { isAxiosError } from 'axios'
import {
  Boxes, Check, ChevronDown, ChevronRight, GitCommit, HelpCircle, Loader2,
  Play, Repeat, Server, Sparkles, StopCircle, TrendingDown, X,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { useAgentPolicies } from '@/hooks/useAgentGovernance'
import { useInvestigation, useInvestigations } from '@/hooks/useInvestigation'
import { usePermissions } from '@/hooks/usePermissions'
import { investigatorService } from '@/services/investigatorService'
import {
  isInvestigationActive,
  type ConfidenceBasis,
  type HypothesisId,
  type InvestigationDetail,
  type InvestigationHypothesis,
  type InvestigationSummary,
} from '@/types/investigator'
import { formatRunWhen } from '@/utils/formatters'

// ── Hypothesis metadata (fixed 5-card matrix) ────────────────────────────

const HYPOTHESIS_ORDER: HypothesisId[] = ['infra', 'commit', 'environment', 'known_flaky', 'regression']

const HYPOTHESIS_META: Record<HypothesisId, { title: string; icon: typeof Server }> = {
  infra:       { title: 'Infrastructure', icon: Server },
  commit:      { title: 'Commit-caused',  icon: GitCommit },
  environment: { title: 'Environment',    icon: Boxes },
  known_flaky: { title: 'Known-flaky',    icon: Repeat },
  regression:  { title: 'Regression',     icon: TrendingDown },
}

const TRIGGER_LABEL: Record<string, string> = {
  'manual':             'manual',
  'auto:newly_failing': 'auto · newly failing',
  'auto:gate_no_go':    'auto · gate NO_GO',
}

// ── Small atoms ──────────────────────────────────────────────────────────

/** Calibration-basis chip — same pattern as AIAnalysisPanel (AI-F4). */
function BasisChip({ basis }: { basis: ConfidenceBasis }) {
  const label = basis === 'empirical'
    ? 'calibrated'
    : basis === 'llm_weighted'
      ? 'llm-weighted'
      : basis === 'human_corrected'
        ? 'human-corrected'
        : 'estimated'
  const title =
    basis === 'empirical'
      ? 'Calibrated confidence — equals measured precision on labeled eval samples'
      : basis === 'llm_weighted'
        ? 'LLM-weighted confidence — model self-assessment, not empirically calibrated'
        : basis === 'human_corrected'
          ? 'Confidence pinned by an authoritative human correction of this classification'
          : 'Estimated heuristic confidence — not empirically calibrated'
  return (
    <span
      className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] cursor-help"
      title={title}
    >
      {label}
    </span>
  )
}

function ModeChip({ mode }: { mode: string }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-semibold uppercase"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
        letterSpacing: 'var(--tracking-wide)',
      }}
    >
      {mode}
    </span>
  )
}

function HypothesisStatusIcon({ status }: { status: InvestigationHypothesis['status'] }) {
  if (status === 'pending') {
    return <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: 'var(--color-text-faint)' }} aria-label="pending" />
  }
  if (status === 'running') {
    return (
      <span
        className="inline-block h-2.5 w-2.5 rounded-full animate-pulse"
        style={{ background: 'var(--color-accent)' }}
        aria-label="running"
      />
    )
  }
  if (status === 'validated') {
    return <Check className="h-3.5 w-3.5" strokeWidth={3} style={{ color: 'var(--status-passed)' }} aria-label="validated" />
  }
  if (status === 'invalidated') {
    return <X className="h-3.5 w-3.5" strokeWidth={3} style={{ color: 'var(--color-text-faint)' }} aria-label="invalidated" />
  }
  return <HelpCircle className="h-3.5 w-3.5" style={{ color: 'var(--gate-conditional)' }} aria-label="inconclusive" />
}

/** Spend-vs-budget mini bar (llm calls / tokens / seconds). */
function BudgetBar({ label, used, max, format }: { label: string; used: number; max: number; format?: (n: number) => string }) {
  const fmt = format ?? ((n: number) => n.toLocaleString())
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0
  const over = max > 0 && used >= max
  return (
    <div className="min-w-0">
      <div className="flex justify-between text-[10.5px] text-[var(--color-text-muted)] mb-0.5">
        <span>{label}</span>
        <span className="tabular-nums">{fmt(used)} / {fmt(max)}</span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
        <i
          className="block h-full"
          style={{ width: `${pct}%`, background: over ? 'var(--gate-no-go)' : 'var(--color-accent)' }}
        />
      </div>
    </div>
  )
}

// ── Hypothesis card ──────────────────────────────────────────────────────

function HypothesisCard({ id, hypothesis }: { id: HypothesisId; hypothesis: InvestigationHypothesis | null }) {
  const [expanded, setExpanded] = useState(false)
  const meta = HYPOTHESIS_META[id]
  const Icon = meta.icon
  const status = hypothesis?.status ?? 'pending'
  const muted = status === 'invalidated' || status === 'pending'
  const confColor =
    status === 'validated' ? 'var(--status-passed)'
    : status === 'inconclusive' ? 'var(--gate-conditional)'
    : 'var(--color-text-secondary)'

  return (
    <div
      data-testid={`hypothesis-${id}`}
      className={clsx('rounded-md border flex flex-col gap-1.5', muted && 'opacity-70')}
      style={{
        padding: '10px 12px',
        background: 'var(--color-bg)',
        borderColor: status === 'validated' ? 'var(--gate-go-border, var(--color-border))' : 'var(--color-border)',
      }}
    >
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] flex-1 truncate">
          {hypothesis?.title || meta.title}
        </span>
        <HypothesisStatusIcon status={status} />
      </div>

      <div className="flex items-center gap-1.5">
        {hypothesis ? (
          <>
            <span className="text-[13px] font-bold tabular-nums" style={{ color: confColor }}>
              {hypothesis.confidence}%
            </span>
            <BasisChip basis={hypothesis.confidence_basis} />
          </>
        ) : (
          <span className="text-[11px] text-[var(--color-text-faint)]">awaiting agent</span>
        )}
      </div>

      {hypothesis?.summary && (
        <p className="text-[11.5px] m-0 leading-[1.45]" style={{ color: 'var(--color-text-secondary)' }}>
          {hypothesis.summary}
        </p>
      )}

      {hypothesis && hypothesis.evidence.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1 text-[11px] font-medium hover:underline"
            style={{ color: 'var(--color-accent)' }}
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            {hypothesis.evidence.length} evidence item{hypothesis.evidence.length === 1 ? '' : 's'}
          </button>
          {expanded && (
            <ul className="m-0 mt-1.5 p-0 flex flex-col gap-1.5 list-none">
              {hypothesis.evidence.map((ev, i) => (
                <li
                  key={i}
                  className="rounded border px-2 py-1.5 text-[11px]"
                  style={{ background: 'var(--color-bg-secondary)', borderColor: 'var(--color-border)' }}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-[var(--color-text)]">{ev.label}</span>
                    <span
                      className="text-[9.5px] uppercase px-1 rounded-sm"
                      style={{ background: 'var(--color-bg-hover)', color: 'var(--color-text-muted)', letterSpacing: 'var(--tracking-wide)' }}
                    >
                      {ev.kind}
                    </span>
                    {ev.url_path && (
                      <Link to={ev.url_path} className="ml-auto hover:underline whitespace-nowrap" style={{ color: 'var(--color-accent)' }}>
                        view →
                      </Link>
                    )}
                  </div>
                  <div className="text-[var(--color-text-muted)] mt-0.5">{ev.detail}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

// ── Verdict panel ────────────────────────────────────────────────────────

function VerdictPanel({ detail }: { detail: InvestigationDetail }) {
  const verdict = detail.verdict
  if (!verdict) return null
  const causeTitle =
    verdict.primary_cause === 'unknown' ? 'Unknown' : HYPOTHESIS_META[verdict.primary_cause].title
  return (
    <div
      data-testid="investigation-verdict"
      className="rounded-md border"
      style={{ padding: '12px 14px', background: 'var(--color-bg)', borderColor: 'var(--color-border-light)' }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase"
          style={{
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border-light)',
            color: verdict.primary_cause === 'unknown' ? 'var(--color-text-muted)' : 'var(--status-passed)',
            letterSpacing: 'var(--tracking-wide)',
          }}
        >
          Primary cause · {causeTitle}
        </span>
        <span className="text-[12px] font-semibold tabular-nums text-[var(--color-text-secondary)]">
          {verdict.confidence}% confidence
        </span>
        <span
          className="ml-auto inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full uppercase font-semibold"
          style={{ background: 'var(--color-bg-hover)', color: 'var(--color-text-muted)', letterSpacing: 'var(--tracking-wide)' }}
          title="This narrative was generated by the Investigator AI agent. Verify against the linked evidence."
        >
          <Sparkles className="h-3 w-3" /> AI-generated
        </span>
      </div>
      <p className="text-[12.5px] m-0 mt-2 leading-[1.55]" style={{ color: 'var(--color-text-secondary)' }}>
        {verdict.narrative}
      </p>
      {verdict.recommended_actions.length > 0 && (
        <div className="mt-2">
          <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
            Recommended actions
          </div>
          <ul className="m-0 mt-1 pl-4 flex flex-col gap-0.5">
            {verdict.recommended_actions.map((a, i) => (
              <li key={i} className="text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      {detail.mode === 'shadow' && (
        <div className="text-[11px] mt-2 italic" style={{ color: 'var(--color-text-muted)' }}>
          Shadow mode — no actions were taken. The agent only recorded what it would do.
        </div>
      )}
    </div>
  )
}

// ── Past investigations (compact) ────────────────────────────────────────

function HistoryList({
  items, activeId, onSelect,
}: { items: InvestigationSummary[]; activeId: string | null; onSelect: (id: string) => void }) {
  if (items.length === 0) return null
  return (
    <div className="mt-3">
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Past investigations
      </div>
      <div className="flex flex-col gap-1">
        {items.map((it) => {
          const causeLabel = it.primary_cause
            ? it.primary_cause === 'unknown' ? 'unknown' : HYPOTHESIS_META[it.primary_cause].title
            : '—'
          return (
            <button
              key={it.id}
              type="button"
              onClick={() => onSelect(it.id)}
              className={clsx(
                'flex items-center gap-2.5 rounded-md border px-2.5 py-1.5 text-left text-[11.5px] transition-colors hover:bg-[var(--color-bg-hover)]',
              )}
              style={{
                background: 'var(--color-bg)',
                borderColor: it.id === activeId ? 'var(--color-accent)' : 'var(--color-border)',
              }}
            >
              <span
                className={clsx('inline-block h-1.5 w-1.5 rounded-full flex-none', isInvestigationActive(it.status) && 'animate-pulse')}
                style={{
                  background:
                    it.status === 'completed' ? 'var(--status-passed)'
                    : it.status === 'failed' ? 'var(--gate-no-go)'
                    : it.status === 'cancelled' ? 'var(--color-text-faint)'
                    : 'var(--color-accent)',
                }}
                aria-hidden
              />
              <span className="font-medium text-[var(--color-text)] capitalize">{it.status}</span>
              <ModeChip mode={it.mode} />
              <span className="text-[var(--color-text-muted)] truncate">
                {causeLabel}
                {it.confidence != null && <> · {it.confidence}%</>}
              </span>
              <span className="ml-auto text-[var(--color-text-faint)] tabular-nums whitespace-nowrap">
                {it.run_build_number != null && <>build {it.run_build_number} · </>}
                {it.started_at ? formatRunWhen(it.started_at) : '—'}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Cockpit ──────────────────────────────────────────────────────────────

export default function InvestigatorCockpit({ runId, projectId }: { runId: string | null; projectId: string | null }) {
  const { isQaEngineer } = usePermissions()
  const { investigatorPolicy } = useAgentPolicies(projectId)
  const { data: listData, mutate: mutateList } = useInvestigations(projectId)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  // Newest-first history, scoped to the focused run when one is in scope.
  const history = useMemo<InvestigationSummary[]>(() => {
    const items = listData?.items ?? []
    const sorted = [...items].sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''))
    return runId ? sorted.filter((i) => i.run_id === runId) : sorted
  }, [listData, runId])

  const activeForRun = history.find((i) => isInvestigationActive(i.status))
  const effectiveId = selectedId ?? activeForRun?.id ?? history[0]?.id ?? null

  const { data: detail, mutate: mutateDetail } = useInvestigation(effectiveId)

  const policyDisabled = investigatorPolicy != null && !investigatorPolicy.enabled
  const disabledReason = !runId
    ? 'No run in scope — pick a run from the recent list first'
    : policyDisabled
      ? 'The Investigator agent is disabled for this project — enable it in Settings → AI Agents'
      : !isQaEngineer
        ? 'QA Engineer role required'
        : null

  const onInvestigate = async () => {
    if (!runId || disabledReason || starting) return
    setStarting(true)
    try {
      const { investigation_id } = await investigatorService.startInvestigation(runId)
      setSelectedId(investigation_id)
      toast.success('Investigation queued')
      void mutateList()
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 409) {
        // Shared interceptor already toasted the server's "already running"
        // detail — attach the cockpit to the in-flight investigation.
        toast('Attaching to the investigation already running for this run', { icon: 'ℹ️' })
        setSelectedId(null)
        void mutateList()
      }
      // 403 (policy disabled) and other errors: the shared axios interceptor
      // surfaced the actionable message — nothing further to do here.
    } finally {
      setStarting(false)
    }
  }

  const onCancel = async () => {
    if (!effectiveId) return
    try {
      await investigatorService.cancelInvestigation(effectiveId)
      toast.success('Cancellation requested')
      void mutateDetail()
      void mutateList()
    } catch {
      // interceptor toasts
    }
  }

  const isActive = isInvestigationActive(detail?.status)
  const promptEntries = Object.entries(detail?.prompt_versions ?? {})

  return (
    <section
      aria-label="Agent investigation"
      data-testid="investigator-cockpit"
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', marginBottom: 14 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2.5 px-4 py-3 flex-wrap" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)] inline-flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5" style={{ color: 'var(--color-accent)' }} />
          Agent Investigation
          <span className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
            hypothesis loop
          </span>
          {detail && <ModeChip mode={detail.mode} />}
        </h3>
        <div className="flex items-center gap-2">
          {isActive && (
            <button
              type="button"
              onClick={onCancel}
              data-testid="cancel-investigation"
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] font-medium rounded-md border transition-colors hover:bg-[var(--color-bg-hover)]"
              style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
            >
              <StopCircle className="h-3.5 w-3.5" />
              Cancel
            </button>
          )}
          <span title={disabledReason ?? 'Run the Investigator agent on this run'}>
            <button
              type="button"
              onClick={onInvestigate}
              disabled={!!disabledReason || starting}
              data-testid="investigate-run"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[12.5px] font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
            >
              {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Investigate this run
            </button>
          </span>
        </div>
      </div>

      <div className="px-4 py-3.5">
        {policyDisabled && (
          <div
            className="rounded-md border px-3 py-2 mb-3 text-[12px]"
            style={{ borderColor: 'var(--gate-conditional-border, var(--color-border))', background: 'var(--gate-conditional-bg, var(--color-bg-secondary))', color: 'var(--color-text-secondary)' }}
            data-testid="policy-disabled-note"
          >
            The Investigator agent is disabled for this project. A QA Lead can enable it under{' '}
            <Link to="/settings/ai-agents" className="font-medium hover:underline" style={{ color: 'var(--color-accent)' }}>
              Settings → AI Agents
            </Link>.
          </div>
        )}

        {!detail && (
          <p className="text-[12.5px] m-0 text-[var(--color-text-muted)]">
            No investigations yet for {runId ? 'this run' : 'this project'}. The Investigator agent tests five root-cause
            hypotheses in parallel — infrastructure, commit-caused, environment, known-flaky, regression — and links every
            claim to queryable evidence. It launches in shadow mode: it reports, it never acts.
          </p>
        )}

        {detail && (
          <>
            {/* Status strip while active / terminal notice */}
            {isActive && (
              <div className="flex items-center gap-2 mb-3 text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
                <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: 'var(--color-accent)' }} />
                <span className="capitalize font-medium">{detail.status}</span>
                <span className="text-[var(--color-text-muted)]">— hypotheses update live every 2.5 s</span>
              </div>
            )}
            {detail.status === 'cancelled' && (
              <div className="mb-3 text-[12px]" style={{ color: 'var(--color-text-muted)' }}>
                Investigation cancelled{detail.cancelled_by ? <> by {detail.cancelled_by}</> : null}.
              </div>
            )}
            {detail.status === 'failed' && (
              <div className="mb-3 text-[12px]" style={{ color: 'var(--gate-no-go)' }}>
                Investigation failed before producing a verdict. Partial hypothesis results are shown below.
              </div>
            )}

            {/* Hypothesis matrix */}
            <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
              {HYPOTHESIS_ORDER.map((id) => (
                <HypothesisCard key={id} id={id} hypothesis={detail.hypotheses.find((h) => h.id === id) ?? null} />
              ))}
            </div>

            {/* Verdict */}
            {detail.status === 'completed' && detail.verdict && (
              <div className="mt-3">
                <VerdictPanel detail={detail} />
              </div>
            )}

            {/* Spend vs budget */}
            <div className="grid gap-3 mt-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
              <BudgetBar label="LLM calls" used={detail.spend.llm_calls} max={detail.budget.max_llm_calls} />
              <BudgetBar label="Tokens" used={detail.spend.tokens} max={detail.budget.max_tokens} />
              <BudgetBar
                label="Seconds"
                used={detail.spend.seconds}
                max={detail.budget.max_seconds}
                format={(n) => `${Math.round(n)}s`}
              />
            </div>

            {/* Details footer */}
            <div
              className="flex items-center gap-1.5 flex-wrap mt-3 pt-2.5 text-[10.5px] text-[var(--color-text-muted)]"
              style={{ borderTop: '1px dashed var(--color-border)' }}
            >
              <span>Triggered {TRIGGER_LABEL[detail.triggered_by] ?? detail.triggered_by}</span>
              <span aria-hidden>·</span>
              <span>spend ${detail.spend.cost_usd.toFixed(2)}</span>
              {detail.model && (
                <>
                  <span aria-hidden>·</span>
                  <span>
                    model <code className="font-mono">{detail.model.provider}/{detail.model.model}</code>
                  </span>
                </>
              )}
              {promptEntries.length > 0 && (
                <>
                  <span aria-hidden>·</span>
                  <span>
                    prompts{' '}
                    {promptEntries.map(([name, version], i) => (
                      <code key={name} className="font-mono">
                        {i > 0 && ', '}
                        {name}@{version}
                      </code>
                    ))}
                  </span>
                </>
              )}
              <span aria-hidden>·</span>
              <span>id <code className="font-mono">{detail.id.slice(0, 8)}</code></span>
            </div>
          </>
        )}

        <HistoryList items={history.slice(0, 5)} activeId={effectiveId} onSelect={setSelectedId} />
      </div>
    </section>
  )
}
