import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  AlertTriangle, CheckCircle, HelpCircle, Shield, XCircle, Zap,
  History, ExternalLink, ChevronDown, ChevronUp, FileDown, Share2,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import SuiteBadge from '@/components/ui/SuiteBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import CriticalityMatrix from '@/components/ai/CriticalityMatrix'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { useReleaseCouncil } from '@/hooks/useReleaseCouncil'
import { useAIConfig, isLLMAvailable } from '@/hooks/useAIConfig'
import { releaseCouncilService } from '@/services/releaseCouncilService'
import type { OverrideAuditEntry } from '@/services/releaseCouncilService'
import { useRuns } from '@/hooks/useRuns'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { buildReleaseGateWorkflow } from '@/components/workflow/workflowPresets'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import { copyTextToClipboard } from '@/utils/clipboard'

type Recommendation = 'GO' | 'NO_GO' | 'CONDITIONAL_GO' | 'PENDING'

const REC_CONFIG: Record<Recommendation, { label: string; colour: string; bg: string; icon: React.ElementType }> = {
  GO:              { label: 'GO',              colour: 'text-[var(--status-passed)]', bg: 'bg-[var(--status-passed-bg)]/20 border-[var(--status-passed-bd)]/30', icon: CheckCircle },
  NO_GO:           { label: 'NO GO',           colour: 'text-[var(--status-failed)]',     bg: 'bg-[var(--status-failed-bg)]/20 border-[var(--status-failed-bd)]/30',         icon: XCircle     },
  CONDITIONAL_GO:  { label: 'CONDITIONAL GO',  colour: 'text-[var(--status-broken)]',   bg: 'bg-[var(--status-broken-bg)]/20 border-[var(--status-broken-bd)]/30',     icon: AlertTriangle },
  // Used when the decision has no test evidence to grade — backend can still
  // return GO/NO_GO from policy defaults, but the UI shouldn't surface a
  // verdict against zero data.
  PENDING:         { label: 'PENDING',         colour: 'text-[var(--color-text-secondary)]', bg: 'bg-[var(--color-bg-secondary)]/40 border-[var(--color-border)]', icon: HelpCircle },
}

function RiskGauge({ score }: { score: number }) {
  const colour = score >= 70 ? 'var(--status-failed)' : score >= 40 ? 'var(--status-broken)' : 'var(--status-passed)'
  const pct = Math.min(100, Math.max(0, score))
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="120" height="70" viewBox="0 0 120 70">
        <path d="M 10 65 A 50 50 0 0 1 110 65" fill="none" stroke="#334155" strokeWidth="10" strokeLinecap="round" />
        <path
          d="M 10 65 A 50 50 0 0 1 110 65"
          fill="none"
          stroke={colour}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * 157} 157`}
        />
        <text x="60" y="60" textAnchor="middle" fill="white" fontSize="22" fontWeight="bold">{score}</text>
      </svg>
      <p className="text-xs text-[var(--color-text-muted)]">Risk Score</p>
    </div>
  )
}

function OverrideAuditTrail({ entries }: { entries: OverrideAuditEntry[] }) {
  const [expanded, setExpanded] = useState(false)
  if (entries.length === 0) return null

  return (
    <div className="card border border-[var(--color-border)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]"
      >
        <History className="w-4 h-4 text-[var(--color-text-muted)]" />
        Override History ({entries.length})
        {expanded ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
      </button>
      {expanded && (
        <div className="mt-3 space-y-2">
          {entries.map((entry, i) => (
            <div key={i} className="bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2 text-sm">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[var(--color-text-muted)] text-xs">
                  {new Date(entry.timestamp).toLocaleString()}
                </span>
                <span className="text-[var(--color-text-muted)] text-xs">by {entry.actor_name ?? 'Unknown'}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={clsx('text-xs font-medium', REC_CONFIG[entry.before_recommendation as Recommendation]?.colour ?? 'text-[var(--color-text-muted)]')}>
                  {entry.before_recommendation}
                </span>
                <span className="text-[var(--color-text-faint)]">→</span>
                <span className={clsx('text-xs font-medium', REC_CONFIG[entry.after_recommendation as Recommendation]?.colour ?? 'text-[var(--color-text-muted)]')}>
                  {entry.after_recommendation}
                </span>
              </div>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">{entry.reason}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ReleaseGatePage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { council: decision, isLoading, isError, refresh } = useReleaseCouncil(runId ?? null)
  const { data: aiConfig } = useAIConfig()
  const llmMode = isLLMAvailable(aiConfig)
  const [overrideMode, setOverrideMode] = useState(false)
  const [overrideRec, setOverrideRec] = useState<Recommendation>('GO')
  const [overrideReason, setOverrideReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const workflow = decision ? buildReleaseGateWorkflow(decision) : null

  useProjectChangeRedirect('/release-gate', Boolean(runId))

  const { data: recentRunsData } = useRuns({ page: 1, size: isAllProjects ? 10 : 1 })

  useEffect(() => {
    if (!runId && !isAllProjects && recentRunsData?.items?.[0]?.id) {
      navigate(`/release-gate/${recentRunsData.items[0].id}`, { replace: true })
    }
  }, [runId, isAllProjects, recentRunsData, navigate])

  // All Projects overview
  if (!runId && isAllProjects) {
    const runs = recentRunsData?.items ?? []
    return (
      <div className="space-y-6">
        <PageHeader
          title="Release Gate"
          subtitle="Recent runs across all projects — select a run to view its gate decision"
        />
        {runs.length === 0 ? (
          <EmptyState
            icon={<Shield className="h-10 w-10" />}
            title="No runs found"
            description="Upload test results to generate release gate assessments."
          />
        ) : (
          <div className="card">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th text-left">Run</th>
                  <th className="th text-left">Build</th>
                  <th className="th text-left">Project</th>
                  <th className="th text-left">Date</th>
                  <th className="th text-left">Status</th>
                  <th className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="table-row">
                    <td className="td font-mono text-[var(--color-text)] text-xs">{run.id.slice(0, 8)}</td>
                    <td className="td text-[var(--color-text-secondary)] text-xs">{run.build_number ?? '—'}</td>
                    <td className="td text-[var(--color-text-muted)] text-xs">{run.project_name ?? run.project_id?.slice(0, 8) ?? '—'}</td>
                    <td className="td text-[var(--color-text-muted)] text-xs">
                      {run.created_at ? new Date(run.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                    </td>
                    <td className="td">
                      <span className={clsx(
                        'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset',
                        run.status === 'passed' ? 'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)] ring-[var(--status-passed)]/20' :
                        run.status === 'failed' ? 'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)] ring-[var(--status-failed)]/20' :
                        'bg-[var(--color-bg-card)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/20',
                      )}>
                        {(run.status ?? 'unknown').toUpperCase()}
                      </span>
                    </td>
                    <td className="td text-right">
                      <button
                        onClick={() => navigate(`/release-gate/${run.id}`)}
                        className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
                      >
                        View Gate →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  if (!runId) {
    return (
      <EmptyState
        icon={<Shield className="h-10 w-10" />}
        title="No run selected"
        description="Navigate to a test run to see the release gate decision."
      />
    )
  }

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  }

  if (isError || !decision) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Release Gate"
          subtitle={`Run ${runId?.slice(0, 8) ?? '—'}`}
        />
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="No release decision found"
          description="Deep investigation has not been triggered for this run yet."
          action={
            <button
              onClick={() => navigate(`/deep-investigate/${runId}`)}
              className="btn-primary flex items-center gap-2"
            >
              <Zap className="h-4 w-4" /> Go to Deep Investigation
            </button>
          }
        />
      </div>
    )
  }

  // A decision computed against an empty dataset (no pass_rate and no
  // dimension scores) typically falls through to GO/NO_GO from policy
  // defaults — both misleading without evidence. Render a neutral Pending
  // banner instead so the user sees "we can't grade this yet" rather than
  // a verdict that came from defaults applied to zero data.
  const hasEvidence =
    decision.pass_rate != null ||
    (decision.dimension_scores?.length ?? 0) > 0 ||
    (decision.rule_evaluations?.length ?? 0) > 0
  const cfg = hasEvidence
    ? (REC_CONFIG[decision.recommendation as Recommendation] ?? REC_CONFIG.CONDITIONAL_GO)
    : REC_CONFIG.PENDING
  const Icon = cfg.icon

  const handleOverride = async () => {
    if (!overrideReason.trim()) {
      toast.error('Please provide a reason for the override')
      return
    }
    setSubmitting(true)
    try {
      await releaseCouncilService.override(runId, overrideRec, overrideReason)
      toast.success('Release decision overridden')
      setOverrideMode(false)
      setOverrideReason('')
      await refresh()
    } catch {
      toast.error('Override failed — QA Lead role required')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Release Gate"
        subtitle={`Build ${decision.build_number ?? runId} — AI-powered go/no-go assessment`}
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <SuiteBadge
              primary={(decision as unknown as { primary_suite_name?: string | null; suite_names?: string[] | null }).primary_suite_name}
              all={(decision as unknown as { suite_names?: string[] | null }).suite_names}
            />
            <button
              onClick={async () => {
                const { downloadPdf } = await import('@/services/reportExportService')
                if (runId) downloadPdf(runId, 'executive').catch(() => toast.error('PDF export failed'))
              }}
              className="btn-secondary text-xs flex items-center gap-1.5"
              title="Export PDF"
            >
              <FileDown className="h-3.5 w-3.5" /> PDF
            </button>
            <button
              onClick={async () => {
                const { createShareLink } = await import('@/services/reportExportService')
                try {
                  if (!runId) return
                  const link = await createShareLink(runId, 'executive')
                  const ok = await copyTextToClipboard(link.share_url)
                  if (ok) toast.success('Share link copied to clipboard')
                  else toast.error('Clipboard access denied — copy manually')
                } catch { toast.error('Share failed') }
              }}
              className="btn-secondary text-xs flex items-center gap-1.5"
              title="Share Report"
            >
              <Share2 className="h-3.5 w-3.5" /> Share
            </button>
          <Link
            to={`/runs/${runId}/intelligence`}
            className="btn-secondary text-sm flex items-center gap-2"
          >
            View Intelligence <ExternalLink className="h-3.5 w-3.5" />
          </Link>
          </div>
        }
      />

      {decision.synthesized && (
        // Quick-look notice — the backend synthesised this view from
        // the run's aggregates because no persisted ReleaseDecision
        // exists yet (deep investigation has not run). The page is
        // still useful at a glance, but cluster insights / defect
        // breakdown / LLM narrative are blank, so we surface a CTA.
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-lg border border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-3"
        >
          <div className="flex items-start gap-2 text-sm text-[var(--status-broken)]">
            <Shield className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-broken)]" />
            <div>
              <strong className="font-semibold">Quick-look decision.</strong>{' '}
              Derived from this run&apos;s aggregates because deep investigation
              has not run yet. Cluster insights, defect breakdown, and AI
              narrative require a deep investigation pass.
            </div>
          </div>
          <button
            onClick={() => navigate(`/deep-investigate/${runId}`)}
            className="btn-secondary flex-shrink-0 text-xs"
          >
            Run deep investigation
          </button>
        </div>
      )}

      <WorkflowTimeline
        title="Release decision flow"
        subtitle="Policy rules, cluster review, evidence synthesis, and the final gate recommendation"
        stages={workflow?.stages ?? []}
        events={workflow?.events ?? []}
        stageOrder={workflow?.stageOrder ?? []}
        compact
        showInspector
        showEventFeed
      />

      {/* Main decision banner */}
      <div className={clsx('card border rounded-xl p-6 flex items-center gap-6', cfg.bg)}>
        <Icon className={clsx('w-16 h-16 shrink-0', cfg.colour)} />
        <div className="flex-1 min-w-0">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Recommendation</p>
          <p className={clsx('text-4xl font-black tracking-tight', cfg.colour)}>{cfg.label}</p>
          {!hasEvidence && (
            <p className="text-sm text-[var(--color-text-muted)] mt-1">
              No test evidence yet — verdict will compute once results land for this run.
            </p>
          )}
          {hasEvidence && decision.pass_rate != null && (
            <p className="text-sm text-[var(--color-text-muted)] mt-1">Pass rate: {decision.pass_rate.toFixed(1)}%</p>
          )}
          {hasEvidence && decision.original_recommendation && decision.original_recommendation !== decision.recommendation && (
            <p className="text-xs text-[var(--status-broken)] mt-1">
              Original AI recommendation: {decision.original_recommendation}
              {decision.original_risk_score != null && ` (score: ${decision.original_risk_score})`}
            </p>
          )}
        </div>
        {hasEvidence && <RiskGauge score={decision.risk_score} />}
      </div>

      {/* Policy badge (ENT-02) */}
      {decision.policy_level && (
        <div className="card border border-[var(--color-border)] py-2 px-4 flex items-center gap-3 text-xs">
          <Shield className="w-3.5 h-3.5 text-[var(--color-text-muted)]" />
          <span className="text-[var(--color-text-muted)]">
            Evaluated with{' '}
            <span className="text-[var(--color-text)] font-medium">
              {decision.policy_level === 'hardcoded' ? 'system defaults' :
               decision.policy_level === 'system' ? 'system policy' :
               `project policy`}
              {decision.policy_version != null && ` v${decision.policy_version}`}
            </span>
          </span>
        </div>
      )}

      {/* Policy rule evaluations (ENT-02) */}
      {decision.rule_evaluations && decision.rule_evaluations.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3 flex items-center gap-2">
            <Shield className="w-4 h-4 text-[var(--color-text-muted)]" />
            Policy Rules Evaluated
          </h3>
          <div className="space-y-1.5">
            {decision.rule_evaluations.map((ev: { rule_id: string; rule_name: string; passed: boolean; action: string; message: string }) => (
              <div key={ev.rule_id} className="flex items-center justify-between bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className={clsx('w-2 h-2 rounded-full', ev.passed ? 'bg-[var(--status-passed-bg)]' : ev.action === 'BLOCK' ? 'bg-[var(--status-failed-bg)]' : 'bg-[var(--status-broken-bg)]')} />
                  <span className="text-[var(--color-text-secondary)]">{ev.rule_name}</span>
                </div>
                <span className={clsx('text-xs', ev.passed ? 'text-[var(--color-text-muted)]' : 'text-[var(--status-failed)]')}>{ev.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Override badge */}
      {decision.human_override && (
        <div className="card border border-[var(--status-broken-bd)]/30 bg-[var(--status-broken-bg)]/10 py-3">
          <p className="text-xs text-[var(--status-broken)] uppercase tracking-wider mb-1">Human Override Applied</p>
          <p className="text-sm text-[var(--color-text-secondary)]">{decision.human_override}</p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Blocking issues with cluster drill-down */}
        {decision.blocking_issues.length > 0 && (
          <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3 flex items-center gap-2">
              <XCircle className="w-4 h-4 text-[var(--status-failed)]" />
              Blocking Issues
            </h3>
            <ul className="space-y-2">
              {decision.blocking_issues.map((issue, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                  <span className="text-[var(--status-failed)] mt-0.5 shrink-0">✕</span>
                  {issue}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Conditions for go */}
        {decision.conditions_for_go.length > 0 && (
          <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3 flex items-center gap-2">
              <Zap className="w-4 h-4 text-[var(--status-broken)]" />
              Conditions for GO
            </h3>
            <ul className="space-y-2">
              {decision.conditions_for_go.map((cond, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                  <span className="text-[var(--status-broken)] mt-0.5 shrink-0">→</span>
                  {cond}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Linked cluster insights */}
      {decision.cluster_insights.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Linked Failure Clusters</h3>
          <div className="space-y-2">
            {decision.cluster_insights.map((c) => (
              <div key={c.cluster_id} className="flex items-center justify-between bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-[var(--color-text-secondary)] truncate">{c.label}</p>
                  <p className="text-xs text-[var(--color-text-muted)]">{c.size} tests · {c.criticality_level ?? 'unclassified'}</p>
                </div>
                <Link
                  to={`/runs/${runId}/intelligence`}
                  className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] shrink-0"
                >
                  Details →
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Open defects by component */}
      {decision.open_defects_by_component.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Open Defects by Component</h3>
          <div className="space-y-1.5">
            {decision.open_defects_by_component.map((d) => (
              <div key={d.component} className="flex items-center justify-between bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
                <span className="text-sm text-[var(--color-text-secondary)]">{d.component}</span>
                <span className="text-sm font-mono text-[var(--status-failed)]">{d.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reasoning */}
      {decision.reasoning && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-2">{llmMode ? 'AI Reasoning' : 'Decision Rationale'}</h3>
          <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">{decision.reasoning}</p>
        </div>
      )}

      {/* Criticality Matrix */}
      {decision.dimension_scores.length > 0 && (
        <CriticalityMatrix
          dimensionScores={decision.dimension_scores}
          title="Risk Dimension Breakdown"
          defaultExpanded={false}
        />
      )}

      {/* Override audit trail */}
      <OverrideAuditTrail entries={decision.override_audit} />

      {/* Override section */}
      <div className="card">
        <div className="flex items-center gap-3 mb-3">
          <Shield className="w-4 h-4 text-[var(--color-text-muted)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">QA Lead Override</h3>
          {!overrideMode && (
            <button
              onClick={() => setOverrideMode(true)}
              className="ml-auto text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
            >
              Override decision
            </button>
          )}
        </div>
        {overrideMode && (
          <div className="space-y-3">
            <div className="flex gap-2">
              {(['GO', 'CONDITIONAL_GO', 'NO_GO'] as Recommendation[]).map(rec => (
                <button
                  key={rec}
                  onClick={() => setOverrideRec(rec)}
                  className={clsx(
                    'px-3 py-1.5 rounded text-xs font-medium transition-colors',
                    overrideRec === rec
                      ? clsx(REC_CONFIG[rec].colour, 'bg-[var(--color-bg-hover)]')
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                >
                  {REC_CONFIG[rec].label}
                </button>
              ))}
            </div>
            <textarea
              value={overrideReason}
              onChange={e => setOverrideReason(e.target.value)}
              placeholder="Reason for override (required)…"
              rows={3}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] resize-none"
            />
            <div className="flex gap-2">
              <button
                onClick={handleOverride}
                disabled={submitting}
                className="px-4 py-1.5 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] text-sm rounded-lg disabled:opacity-50"
              >
                {submitting ? 'Saving…' : 'Apply Override'}
              </button>
              <button
                onClick={() => { setOverrideMode(false); setOverrideReason('') }}
                className="px-4 py-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
