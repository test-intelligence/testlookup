import { Suspense, useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  AlertTriangle, CheckCircle, HelpCircle, Shield, XCircle, Zap,
  History, ExternalLink, FileDown, Share2,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import SuiteBadge from '@/components/ui/SuiteBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Tabs from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import CriticalityMatrix from '@/components/ai/CriticalityMatrix'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import Disclosure from '@/components/ui/Disclosure'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useReleaseCouncil } from '@/hooks/useReleaseCouncil'
import { useAIConfig, isLLMAvailable } from '@/hooks/useAIConfig'
import { releaseCouncilService } from '@/services/releaseCouncilService'
import type { OverrideAuditEntry, ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import { useRuns } from '@/hooks/useRuns'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { buildReleaseGateWorkflow } from '@/components/workflow/workflowPresets'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import { copyTextToClipboard } from '@/utils/clipboard'
import RingGauge from '@/components/charts/RingGauge'
import { bandsTone, formatGaugeNumber, type GaugeBands } from '@/components/charts/gaugeBar.model'
import { GateContextPending } from '@/components/reports/catalogue/GateContextHeader'
import { lazyWithRetry } from '@/utils/lazyWithRetry'

/**
 * The catalogue's "Context" group (VIZ-408), in its own chunk: it arrives
 * after the verdict it sits below. A stale chunk (a tab opened before a
 * deploy) reloads the page once, like a route's.
 */
const GateCatalogue = lazyWithRetry(() => import('@/components/reports/catalogue/GateCatalogue'))

const HELP_TOPIC = helpTopicParam('/release-gate')

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

/** The verdict card lists this many blockers; the rest are one click away in Why. */
export const TOP_BLOCKERS = 3

/** The gate's sections below the verdict (UX redesign P4 item 6), `?tab=`. */
const GATE_TABS = ['why', 'context', 'history'] as const
type GateTab = (typeof GATE_TABS)[number]

/**
 * The risk arc's bands: 70 and up is bad, 40 and up a warning, below is good.
 * LOWER is better — the reverse of a pass rate — and the direction is said
 * here, not buried in a colour ternary.
 */
export const RISK_SCORE_BANDS: GaugeBands = { direction: 'lower-is-better', thresholds: [40, 70] }
const riskTone = bandsTone(RISK_SCORE_BANDS)

/**
 * The kit's ring (OD-6). It replaced a hand-drawn semicircle whose track was
 * a fixed slate (`#334155`) and whose number was literally white, so on the
 * light theme the score was white on a white card. Every colour is a token
 * now, and the ring is a named meter.
 */
function RiskGauge({ score }: { score: number }) {
  return (
    <RingGauge
      value={score}
      caption="Risk Score"
      format={formatGaugeNumber}
      tone={riskTone}
      size={120}
    />
  )
}

/** The History tab: every override of this decision, newest as the backend orders them. */
function OverrideHistory({ entries }: { entries: OverrideAuditEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="card text-sm text-[var(--color-text-muted)]">
        No overrides recorded for this decision — the recommendation above is the gate&apos;s own.
      </div>
    )
  }
  return (
    <div className="card border border-[var(--color-border)]">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        <History className="w-4 h-4 text-[var(--color-text-muted)]" />
        Override History ({entries.length})
      </h3>
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
    </div>
  )
}

function policyLabel(decision: ReleaseCouncilDecision): string {
  const level =
    decision.policy_level === 'hardcoded' ? 'system defaults'
      : decision.policy_level === 'system' ? 'system policy'
        : 'project policy'
  return decision.policy_version != null ? `${level} v${decision.policy_version}` : level
}

export default function ReleaseGatePage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { council: decision, isLoading, isError, refresh } = useReleaseCouncil(runId ?? null)
  const { data: aiConfig } = useAIConfig()
  const llmMode = isLLMAvailable(aiConfig)
  const [tab, setTab] = useTabParam<GateTab>(GATE_TABS, 'why')
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
      <div className="space-y-4">
        <PageHeader
          compact
          title="Release Gate"
          subtitle="Recent runs across all projects — select a run to view its gate decision"
          helpTopic={HELP_TOPIC}
        />
        {runs.length === 0 ? (
          <EmptyState
            icon={<Shield className="h-10 w-10" />}
            title="No runs found"
            description="Upload test results to generate release gate assessments."
          />
        ) : (
          <div className="card" data-primary="">
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

  // The run's analysis (Run Intelligence + Deep Investigation's clusters) is
  // the run page's Analysis tab since UX redesign P4.
  const analysisHref = `/runs/${runId}?tab=analysis`

  if (isError || !decision) {
    return (
      <div className="space-y-4">
        <PageHeader
          compact
          title="Release Gate"
          subtitle={`Run ${runId?.slice(0, 8) ?? '—'}`}
          helpTopic={HELP_TOPIC}
        />
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="No release decision found"
          description="Deep investigation has not been triggered for this run yet."
          action={
            <button
              onClick={() => navigate(analysisHref)}
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

  // The backend records WHICH input decided the verdict; "pass_rate_floor"
  // means the composite was floored rather than computed from the dimensions.
  const snapshot = (decision.input_snapshot ?? {}) as Record<string, unknown>
  const floorApplied = snapshot.verdict_driver === 'pass_rate_floor'
  const floorPct =
    typeof snapshot.no_go_floor_pct === 'number' ? snapshot.no_go_floor_pct : null
  const cfg = hasEvidence
    ? (REC_CONFIG[decision.recommendation as Recommendation] ?? REC_CONFIG.CONDITIONAL_GO)
    : REC_CONFIG.PENDING
  const Icon = cfg.icon

  // The verdict card's short list: the stored blocking issues, or — for a
  // conditional verdict with none — the conditions that stand between it and
  // a GO. Every item is the decision's own text; the full lists are in Why.
  const blockers = decision.blocking_issues
  const conditions = decision.conditions_for_go
  const cardList = blockers.length > 0
    ? { label: 'Top blockers', items: blockers }
    : conditions.length > 0 ? { label: 'Conditions for GO', items: conditions } : null

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

  // Linked cluster insights, placed under the Context group whose share chart
  // summarises them.
  const clustersCard = decision.cluster_insights.length > 0 && (
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
              to={analysisHref}
              className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] shrink-0"
            >
              Details →
            </Link>
          </div>
        ))}
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Release Gate"
        subtitle={`Build ${decision.build_number ?? runId} — AI-powered go/no-go assessment`}
        helpTopic={HELP_TOPIC}
        actions={
          <>
            <SuiteBadge
              primary={(decision as unknown as { primary_suite_name?: string | null; suite_names?: string[] | null }).primary_suite_name}
              all={(decision as unknown as { suite_names?: string[] | null }).suite_names}
            />
            <Link
              to={analysisHref}
              className="btn-secondary text-sm flex items-center gap-2"
            >
              Run analysis <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </>
        }
        overflow={[
          {
            label: 'Export PDF',
            icon: <FileDown className="h-3.5 w-3.5" />,
            onClick: async () => {
              const { downloadPdf } = await import('@/services/reportExportService')
              downloadPdf(runId, 'executive').catch(() => toast.error('PDF export failed'))
            },
          },
          {
            label: 'Share report',
            icon: <Share2 className="h-3.5 w-3.5" />,
            onClick: async () => {
              const { createShareLink } = await import('@/services/reportExportService')
              try {
                const link = await createShareLink(runId, 'executive')
                const ok = await copyTextToClipboard(link.share_url)
                if (ok) toast.success('Share link copied to clipboard')
                else toast.error('Clipboard access denied — copy manually')
              } catch { toast.error('Share failed') }
            },
          },
        ]}
      />

      {decision.synthesized && (
        // Quick-look notice — the backend synthesised this view from
        // the run's aggregates because no persisted ReleaseDecision
        // exists yet (deep investigation has not run). The page is
        // still useful at a glance, but cluster insights / defect
        // breakdown / LLM narrative are blank, so we surface a CTA.
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-lg border border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-2.5"
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
            onClick={() => navigate(analysisHref)}
            className="btn-secondary flex-shrink-0 text-xs"
          >
            Run deep investigation
          </button>
        </div>
      )}

      {/* The verdict, first (UX redesign P4 item 6): the recommendation, the
          pass rate, the ring, the top blockers and Override — everything a
          reader needs to act, on the first screen. Why it is so is in the
          tabs below; the pipeline that produced it at the very bottom. */}
      <div data-primary="" className={clsx('card border rounded-xl p-5', cfg.bg)}>
        {/* Stacked below `sm` (VIZ-106): a 120 px ring beside the verdict does
            not fit a phone. From `sm` up it is the row it always was. */}
        <div data-verdict-row="" className="flex flex-col items-start sm:flex-row sm:items-center gap-5">
          <Icon className={clsx('w-14 h-14 shrink-0', cfg.colour)} />
          <div className="flex-1 min-w-0">
            {/* An h2: the card is the page's first section, and the sections
                below it (each tab's h3s, the Context group's) follow it in
                the outline instead of jumping from the h1. */}
            <h2 className="text-xs font-normal text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Recommendation</h2>
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
            {decision.human_override && (
              <p className="text-xs text-[var(--status-broken)] mt-1">
                <span className="uppercase tracking-wider">Human override applied:</span>{' '}
                <span className="text-[var(--color-text-secondary)]">{decision.human_override}</span>
              </p>
            )}
          </div>
          {hasEvidence && <RiskGauge score={decision.risk_score} />}
        </div>

        {/* Why the gauge can disagree with the dimension breakdown in Why.
            A run under `no_go_floor_pct` has its composite raised to the NO_GO
            floor without any dimension changing, so the page showed "60" beside
            a weighted total of 17 with nothing connecting them. The backend has
            published `verdict_driver` in `input_snapshot` all along and no code
            read it — the value-nothing-consumes pattern. */}
        {hasEvidence && floorApplied && (
          <p className="text-xs text-[var(--status-broken)] mt-3">
            Risk score raised to the NO-GO floor because the pass rate is below{' '}
            {floorPct != null ? `${floorPct}%` : 'the floor'} — not derived from the
            dimension breakdown in Why.
          </p>
        )}

        {cardList && (
          <div className="mt-4">
            <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">{cardList.label}</p>
            <ul aria-label={cardList.label} className="space-y-1">
              {cardList.items.slice(0, TOP_BLOCKERS).map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                  <span className={clsx('mt-0.5 shrink-0', blockers.length > 0 ? 'text-[var(--status-failed)]' : 'text-[var(--status-broken)]')}>
                    {blockers.length > 0 ? '✕' : '→'}
                  </span>
                  {item}
                </li>
              ))}
            </ul>
            {cardList.items.length > TOP_BLOCKERS && (
              <button
                type="button"
                onClick={() => setTab('why')}
                className="mt-1 text-xs text-[var(--color-accent)] hover:underline"
              >
                {cardList.items.length - TOP_BLOCKERS} more in Why →
              </button>
            )}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--color-border)] pt-3 text-xs">
          {decision.policy_level && (
            <span className="inline-flex items-center gap-1.5 text-[var(--color-text-muted)]">
              <Shield className="w-3.5 h-3.5" />
              Evaluated with{' '}
              <span className="text-[var(--color-text)] font-medium">{policyLabel(decision)}</span>
            </span>
          )}
          {!overrideMode && (
            <button
              type="button"
              onClick={() => setOverrideMode(true)}
              className="btn-secondary ml-auto text-xs"
            >
              Override decision
            </button>
          )}
        </div>

        {overrideMode && (
          <div className="mt-3 space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
              <Shield className="w-4 h-4 text-[var(--color-text-muted)]" />
              QA Lead Override
            </h3>
            <div className="flex gap-2">
              {(['GO', 'CONDITIONAL_GO', 'NO_GO'] as Recommendation[]).map(rec => (
                <button
                  key={rec}
                  type="button"
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
              aria-label="Reason for override"
              rows={3}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] resize-none"
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleOverride}
                disabled={submitting}
                className="px-4 py-1.5 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] text-sm rounded-lg disabled:opacity-50"
              >
                {submitting ? 'Saving…' : 'Apply Override'}
              </button>
              <button
                type="button"
                onClick={() => { setOverrideMode(false); setOverrideReason('') }}
                className="px-4 py-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      <Tabs
        ariaLabel="Release gate sections"
        value={tab}
        onChange={setTab}
        items={[
          { id: 'why', label: 'Why' },
          { id: 'context', label: 'Context' },
          { id: 'history', label: 'History', count: decision.override_audit.length },
        ]}
      />

      {tab === 'why' && (
        <div data-tab-panel="why" className="space-y-4">
          {(blockers.length > 0 || conditions.length > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Blocking issues, in full (the card above lists the first few) */}
              {blockers.length > 0 && (
                <div className="card">
                  <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3 flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-[var(--status-failed)]" />
                    Blocking Issues
                  </h3>
                  <ul className="space-y-2">
                    {blockers.map((issue, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                        <span className="text-[var(--status-failed)] mt-0.5 shrink-0">✕</span>
                        {issue}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Conditions for go */}
              {conditions.length > 0 && (
                <div className="card">
                  <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3 flex items-center gap-2">
                    <Zap className="w-4 h-4 text-[var(--status-broken)]" />
                    Conditions for GO
                  </h3>
                  <ul className="space-y-2">
                    {conditions.map((cond, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                        <span className="text-[var(--status-broken)] mt-0.5 shrink-0">→</span>
                        {cond}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
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

          {/* Reasoning */}
          {decision.reasoning && (
            <div className="card">
              <h3 className="text-sm font-semibold text-[var(--color-text)] mb-2">{llmMode ? 'AI Reasoning' : 'Decision Rationale'}</h3>
              <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">{decision.reasoning}</p>
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

          {/* Risk dimensions */}
          {decision.dimension_scores.length > 0 && (
            <CriticalityMatrix
              dimensionScores={decision.dimension_scores}
              title="Risk Dimension Breakdown"
              defaultExpanded={false}
            />
          )}
        </div>
      )}

      {tab === 'context' && (
        <div data-tab-panel="context" className="space-y-4">
          {/* The catalogue's "Context" group (VIZ-408, plan 2.5): in its own
              tab below the recommendation card, directly above the cluster
              list its share chart summarises (R2-8). It is handed the run, the
              build label and the STORED clusters — never the recommendation,
              the risk score or an override — so it cannot restyle itself by
              verdict. Its own boundary: a chunk that fails to load or a
              section that throws is one error card, never the route's error
              page over the stored verdict (rule 6). Until the chunk arrives,
              its heading, note and height hold the place (R1-7). */}
          <SectionErrorBoundary message="Failed to load charts">
            <Suspense
              fallback={<GateContextPending build={decision.build_number ?? runId} clusters={decision.cluster_insights} />}
            >
              <GateCatalogue runId={runId} build={decision.build_number ?? runId} clusters={decision.cluster_insights} />
            </Suspense>
          </SectionErrorBoundary>
          {clustersCard}
        </div>
      )}

      {tab === 'history' && (
        <div data-tab-panel="history">
          <OverrideHistory entries={decision.override_audit} />
        </div>
      )}

      {/* The decision flow (policy rules → cluster review → evidence → gate)
          is the mechanism behind the verdict above: collapsed, at the end. */}
      <Disclosure title="How this was decided" defaultOpen={false}>
        <WorkflowTimeline
          title="Release decision flow"
          subtitle="Policy rules, cluster review, evidence synthesis, and the final gate recommendation"
          stages={workflow?.stages ?? []}
          events={workflow?.events ?? []}
          stageOrder={workflow?.stageOrder ?? []}
          showInspector
          showEventFeed
        />
      </Disclosure>
    </div>
  )
}
