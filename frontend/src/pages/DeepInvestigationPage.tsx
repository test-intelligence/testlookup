/**
 * Deep Investigation — verdict-led redesign per
 * design_handoff_deep_investigation/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb + suite filter + "Run Deep Analysis" CTA.
 *   Verdict → 1.65fr | 1fr split. Variants READY / NO_FAILURES / NO_SOURCES
 *             / RUNNING / FAILED. Left: pulsing eyebrow → 26 px headline
 *             → lede → 4 input facets (Failures eligible · Scope ·
 *             Evidence sources · Pre-scan signal) → "Run on all" CTA.
 *             Right: Cost & time card (last run's actual cost, a workload
 *             heuristic, the real budget bar).
 *   Cockpit → AI-1 Investigator cockpit.
 *   KPIs    → 5 tiles: Eligible failures · Likely clusters · Last analysis
 *             · Avg cluster confidence · Spend MTD.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Proposed clusters preview + Recent runs table.
 *     Right → Evidence sources card · Model routing card.
 *
 * Pre-scan is synthesised client-side from useFailureClusters (members +
 * cohesion_score) of the focused run; there is no pre-scan endpoint.
 *
 * Data: derives every rendered field from useRuns (focused run picker),
 * useFailureClusters, useDeepFindings, usePipelineStatus,
 * useIntegrationStatus (evidence-source health), useProjectUsage/useProjectQuota
 * (spend + budget), and useDecisionTrail (per-stage model routing, actual
 * cost and when the last analysis finished). The page has no time window of
 * its own: the failures it counts are the focused run's (or, with no run
 * focused, the latest runs'), and `windowLabel` names exactly that.
 */
import { useEffect, useMemo, useRef } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle, Bot, Clock, Database, DollarSign, FileText,
  GitBranch, KeyRound, Layers, Mail, MessageSquare, Play, Plug,
  Server, ShieldCheck, Sparkles, Target,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import AISuggestion from '@/components/ai/AISuggestion'
import EmptyState from '@/components/ui/EmptyState'
import InvestigatorCockpit from '@/components/investigator/InvestigatorCockpit'
import GaugeBar from '@/components/charts/GaugeBar'
import type { GaugeBands } from '@/components/charts/gaugeBar.model'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useFailureClusters, useDeepFindings, usePipelineStatus } from '@/hooks/useDeepInvestigation'
import { useDecisionTrail } from '@/hooks/useDecisionTrail'
import { useIntegrationStatus } from '@/hooks/useIntegrationHealth'
import { useProjectQuota, useProjectUsage } from '@/hooks/useLlmBudget'
import { useRun, useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { scopeKey } from '@/lib/scopeParams'
import { suiteMatchesValue } from '@/utils/suiteFilters'
import { usePermissions } from '@/hooks/usePermissions'
import { deepInvestigationService } from '@/services/deepInvestigationService'
import type { FailureCluster, DeepFinding } from '@/types/deep-investigation'
import type { DecisionTrailResponse } from '@/types/decisionTrail'
import type { IntegrationStatus } from '@/services/integrationHealthService'
import type { LlmQuotaRead, LlmUsageRead } from '@/services/llmBudgetService'
import type { TestRun } from '@/types/runs'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { formatRunWhen, shortAgo } from '@/utils/formatters'
import { isPipelineInProgress } from '@/types/agent'

// ── Verdict ──────────────────────────────────────────────────────────────
type Verdict = 'READY' | 'NO_FAILURES' | 'NO_SOURCES' | 'RUNNING' | 'FAILED' | 'PENDING'

interface VerdictTheme {
  border: string
  glow: string
  bar: string
  eyebrowText: string
  gateText: string
  pillBg: string
  pillBd: string
  pillFg: string
  label: string
  pulse: boolean
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  READY: {
    border: 'color-mix(in srgb, var(--color-accent) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--color-accent) 10%, transparent), transparent 55%)',
    bar:    'var(--color-accent)',
    eyebrowText: 'var(--color-accent)',
    gateText:    'var(--color-accent)',
    pillBg: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
    pillFg: 'var(--color-accent)',
    label:  'Ready to investigate',
    pulse:  true,
  },
  NO_FAILURES: {
    border: 'color-mix(in srgb, var(--status-passed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrowText: 'var(--status-passed)',
    gateText:    'var(--status-passed)',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    label:  'Nothing to investigate',
    pulse:  false,
  },
  NO_SOURCES: {
    border: 'color-mix(in srgb, var(--status-failed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: 'var(--status-failed)',
    gateText:    'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    label:  'No evidence sources',
    pulse:  true,
  },
  RUNNING: {
    border: 'color-mix(in srgb, var(--color-accent) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--color-accent) 12%, transparent), transparent 55%)',
    bar:    'var(--color-accent)',
    eyebrowText: 'var(--color-accent)',
    gateText:    'var(--color-accent)',
    pillBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
    pillFg: 'var(--color-accent)',
    label:  'Investigation running',
    pulse:  true,
  },
  FAILED: {
    border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText:    'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    label:  'Last run failed',
    pulse:  true,
  },
  PENDING: {
    border: 'var(--color-border)',
    glow:   'transparent',
    bar:    'var(--color-border-light)',
    eyebrowText: 'var(--color-text-muted)',
    gateText:    'var(--color-text-secondary)',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    label:  'Awaiting runs',
    pulse:  false,
  },
}

// ── Evidence-source view model (real integration-health reads) ──────────
// Backed by GET /api/v1/integration-health/status via useIntegrationStatus.
// The vocab maps probe statuses onto the card's original live/lagging/off
// design: healthy→live, degraded/timeout→lagging, everything else→off.
interface EvidenceSource {
  id: string
  name: string
  description: string
  status: 'live' | 'lagging' | 'off'
  detail: string
  icon: typeof GitBranch
  toneBg: string
  toneFg: string
}

interface ProviderMeta {
  name: string
  description: string
  icon: typeof GitBranch
  toneBg: string
  toneFg: string
}

const PROVIDER_META: Record<string, ProviderMeta> = {
  github:   { name: 'GitHub',           description: 'commits, diffs, blame',        icon: GitBranch,     toneBg: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', toneFg: 'var(--status-flaky)' },
  jira:     { name: 'Jira context',     description: 'defect & ticket context',      icon: KeyRound,      toneBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', toneFg: 'var(--color-accent)' },
  splunk:   { name: 'Splunk logs',      description: 'application log search',       icon: FileText,      toneBg: 'color-mix(in srgb, var(--status-passed) 16%, transparent)',  toneFg: 'var(--status-passed)' },
  ocp:      { name: 'OpenShift / K8s',  description: 'cluster & pod telemetry',      icon: Server,        toneBg: 'color-mix(in srgb, var(--status-broken) 16%, transparent)', toneFg: 'var(--status-broken)' },
  slack:    { name: 'Slack',            description: 'notification channel',         icon: MessageSquare, toneBg: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', toneFg: 'var(--status-flaky)' },
  teams:    { name: 'Microsoft Teams',  description: 'notification channel',         icon: MessageSquare, toneBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', toneFg: 'var(--color-accent)' },
  smtp:     { name: 'SMTP email',       description: 'notification delivery',        icon: Mail,          toneBg: 'color-mix(in srgb, var(--status-passed) 16%, transparent)',  toneFg: 'var(--status-passed)' },
  ollama:   { name: 'Ollama LLM',       description: 'local model runtime',          icon: Bot,           toneBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', toneFg: 'var(--color-accent)' },
  chromadb: { name: 'ChromaDB',         description: 'semantic search index',        icon: Database,      toneBg: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', toneFg: 'var(--status-flaky)' },
}

const GENERIC_PROVIDER_META: Omit<ProviderMeta, 'name' | 'description'> = {
  icon: Plug, toneBg: 'var(--color-bg-secondary)', toneFg: 'var(--color-text-muted)',
}


function toEvidenceSource(s: IntegrationStatus): EvidenceSource {
  const meta: ProviderMeta = PROVIDER_META[s.provider] ?? {
    ...GENERIC_PROVIDER_META,
    name: s.provider.charAt(0).toUpperCase() + s.provider.slice(1),
    description: 'external integration',
  }
  const status: EvidenceSource['status'] =
    s.status === 'healthy' ? 'live'
    : s.status === 'degraded' || s.status === 'timeout' ? 'lagging'
    : 'off'
  const checked = s.last_checked_at ? `checked ${shortAgo(s.last_checked_at)}` : null
  const description = [s.message ?? meta.description, checked].filter(Boolean).join(' · ')
  return {
    id: s.provider,
    name: meta.name,
    description,
    status,
    detail: status === 'live' ? 'Live' : status === 'lagging' ? 'Degraded' : 'Off',
    icon: meta.icon,
    toneBg: status === 'off' ? 'var(--color-bg-secondary)' : meta.toneBg,
    toneFg: status === 'off' ? 'var(--color-text-muted)' : meta.toneFg,
  }
}

/**
 * The platform's own ingested test results are always an evidence source —
 * not an external probe, but demonstrably present whenever runs exist.
 */
function testResultsSource(hasRuns: boolean): EvidenceSource {
  return {
    id: 'tests',
    name: 'Test results',
    description: hasRuns ? 'runs, failures & history in this workspace' : 'no runs ingested yet',
    status: hasRuns ? 'live' : 'off',
    detail: hasRuns ? 'Live' : 'Off',
    icon: ShieldCheck,
    toneBg: hasRuns ? 'color-mix(in srgb, var(--color-accent) 16%, transparent)' : 'var(--color-bg-secondary)',
    toneFg: hasRuns ? 'var(--color-accent)' : 'var(--color-text-muted)',
  }
}

// ── Atoms ────────────────────────────────────────────────────────────────
function PrimaryBtn({
  children, onClick, title, disabled, large,
}: { children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean; large?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={clsx(
        'inline-flex items-center gap-1.5 font-medium rounded-md transition-colors disabled:opacity-50',
        large ? 'px-4 py-2 text-[14px]' : 'px-3 py-1.5 text-[13px]',
      )}
      style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')}
    >
      {children}
    </button>
  )
}

// ── Page model ──────────────────────────────────────────────────────────
interface DeepModel {
  // Pass-through of the focused TestRun so the header can render its
  // suite badge alongside the project label without re-threading the
  // whole run through every consumer.
  focusedRun: TestRun | null
  eligibleFailures: number
  /** What the failure count covers: the focused run, or the latest runs. */
  windowLabel: string
  /** When the focused run was created (compact), for the Scope facet. */
  windowSince: string | null
  evidenceSources: EvidenceSource[]
  evidenceConnected: number
  evidenceTotal: number
  preScanClusters: number
  /** Mean confidence of the SCORED proposed clusters; null when none is scored. */
  preScanAvgConfidence: number | null
  proposedClusters: ProposedCluster[]
  pastInvestigations: PastRun[]
  lastRunAgeHours: number | null
  lastRunSummary: string | null
  /** Real month-to-date LLM spend for the project (GET /llm-usage). */
  spendMtdDollars: number
  /** Real monthly hard cap (GET /llm-quota); null when no budget is set. */
  spendBudgetDollars: number | null
  /** Soft-warn threshold pct from the quota, for the budget-bar marker. */
  spendSoftWarnPct: number | null
  /** Actual cost of the focused run's last investigation (decision trail). */
  lastRunCostDollars: number | null
  estimateBreakdown: EstimateRow[]
  estimateMinutes: number
  severityCounts: { p0: number; p1: number; p2: number; p3: number }
}

interface ProposedCluster {
  id: string
  rank: number
  name: string
  representativeError: string
  failures: number
  testCount: number
  /** The first service the cluster's AI finding names; null without one. */
  affectedService: string | null
  /** 0-1; null when neither the finding nor the cluster carries a score. */
  confidence: number | null
  /** Derived from a real score only; null for an unscored cluster. */
  severity: 'P0' | 'P1' | 'P2' | 'P3' | null
  isFlaky: boolean
  rationale: string
  /** US-15.1: true when `rationale` is a real AI finding's root cause rather
   *  than the generated "errors clustered around …" fallback sentence. Only
   *  the former gets AI trust chrome — the fallback is not an AI conclusion. */
  fromFinding: boolean
  /** DeepFinding.origin — "pipeline" (real output), "seed" (demo data),
   *  "unknown" (legacy rows). Already on the wire; US-15.1 renders it. */
  origin?: DeepFinding['origin']
  /** DeepFinding.confidence_basis — likewise already on the wire. */
  confidenceBasis?: DeepFinding['confidence_basis']
  startedHoursAgo: number | null
}

interface PastRun {
  runId: string
  runIdLabel: string
  /** Per-(project, suite) human-readable run number, 1-based. Mirrors
   *  the same value shown on /runs, /live, /my-failures, etc. May be
   *  null for legacy runs ingested before the field existed. */
  runSeq: number | null
  suiteLabel: string | null
  suiteNames: string[] | null
  whenRel: string
  whenAbs: string
  failures: number
  /** The four columns below are known only for the FOCUSED run (its
   *  clusters, findings, decision-trail cost and pipeline status); every
   *  other row is null and renders "—". They used to be invented:
   *  failures/12 clusters, failures/14 defects, 0.75 + (i % 4) * 0.03
   *  confidence, $0.30 + failures * $0.005, and every fifth row "Failed at
   *  clustering" (a hard-coded demo row). */
  clusters: number | null
  findings: number | null
  avgConf: number | null
  cost: number | null
  status: 'complete' | 'running' | 'failed' | null
}

interface EstimateRow {
  label: string
  detail: string
}

/** DeepFinding.origin → the provenance phrasing shown in the trust chrome.
 *  "seed" is called out explicitly: demo rows must never read as real
 *  pipeline output (US-15.1). */
const ORIGIN_LABELS: Record<NonNullable<DeepFinding['origin']>, string> = {
  pipeline: 'the deep-analysis pipeline',
  seed: 'seeded demo data',
  unknown: 'an unrecorded source (legacy row)',
}

function severityFromConfidence(c: number): ProposedCluster['severity'] {
  if (c >= 0.85) return 'P0'
  if (c >= 0.65) return 'P1'
  if (c >= 0.45) return 'P2'
  return 'P3'
}

function buildModel({
  clusters, findings, pipelineStatus, recentRuns, focusedRun,
  evidenceSources, usage, quota, trail,
}: {
  clusters: FailureCluster[]
  findings: DeepFinding[]
  pipelineStatus: { status: string; stage_summary?: { completed: number; failed: number; skipped: number; pending: number } } | null
  recentRuns: TestRun[]
  focusedRun: TestRun | null
  evidenceSources: EvidenceSource[]
  usage: LlmUsageRead | undefined
  quota: LlmQuotaRead | null
  trail: DecisionTrailResponse | undefined
}): DeepModel {
  // Eligible failures = sum of failed_tests across the focused run + any
  // recent un-investigated runs in the window.
  const eligibleFailures = focusedRun?.failed_tests
    ?? recentRuns.reduce((s, r) => s + (r.failed_tests ?? 0), 0)

  // Pre-scan: synthesise from existing cluster data on the focused run.
  // When the backend grows a cheap-embedding pre-scan endpoint, swap this
  // for a real call and drop the synthesis.
  const proposedClusters: ProposedCluster[] = clusters.slice(0, 3).map((c, i) => {
    const finding = findings.find(f => f.cluster_id === c.cluster_id)
    // confidence_score arrives as either a 0-1 fraction or a 0-100 percent
    // depending on the producer. Normalise to 0-1 — everything downstream
    // (severity bands, the trust chrome) assumes a fraction, and a raw 91
    // used to render as "9100%". A cluster with neither score is UNSCORED:
    // it used to get an invented 0.7 (a "P1 likely" band it never earned).
    const rawConf = finding?.confidence_score ?? c.cohesion_score ?? null
    const conf = rawConf == null ? null : rawConf > 1 ? rawConf / 100 : rawConf
    const sev = conf == null ? null : severityFromConfidence(conf)
    return {
      id: c.cluster_id,
      rank: i + 1,
      name: c.label,
      representativeError: c.representative_error ?? '',
      failures: c.size,
      testCount: c.member_test_ids.length,
      affectedService: finding?.affected_services?.[0] ?? null,
      confidence: conf,
      severity: sev,
      isFlaky: /flak/i.test(finding?.failure_category ?? ''),
      rationale: finding?.root_cause ?? `Errors clustered around ${c.label.toLowerCase()}.`,
      fromFinding: Boolean(finding?.root_cause),
      origin: finding?.origin,
      confidenceBasis: finding?.confidence_basis ?? null,
      startedHoursAgo: focusedRun ? Math.max(0, Math.round((Date.now() - new Date(focusedRun.created_at).getTime()) / 3600000)) : null,
    }
  })

  // Averages the scored clusters only; an unscored cluster is not a 0.
  const scoredConfidences = proposedClusters
    .map(c => c.confidence)
    .filter((c): c is number => c != null)
  const preScanAvgConfidence = scoredConfidences.length > 0
    ? scoredConfidences.reduce((s, c) => s + c, 0) / scoredConfidences.length
    : null

  // Recent runs — the run picker. There is no investigations endpoint, so
  // only the focused run carries investigation data (see PastRun).
  const trailCost = trail && trail.total_cost_usd > 0 ? trail.total_cost_usd : null
  const pastInvestigations: PastRun[] = recentRuns.slice(0, 6).map((r) => {
    const ageMs = Date.now() - new Date(r.created_at).getTime()
    const ageH = Math.max(0, Math.floor(ageMs / 3600000))
    const ageRel = ageH < 1 ? 'just now'
      : ageH < 24 ? `${ageH}h ago`
      : ageH < 48 ? 'Yesterday'
      : `${Math.floor(ageH / 24)}d ago`
    const isFocus = Boolean(focusedRun && r.id === focusedRun.id)
    const status: PastRun['status'] = isFocus && pipelineStatus
      ? (isPipelineInProgress(pipelineStatus.status) ? 'running'
         : pipelineStatus.status === 'failed' ? 'failed'
         : 'complete')
      : null
    return {
      runId: r.id,
      runIdLabel: r.id.slice(0, 8),
      runSeq: r.run_seq ?? null,
      suiteLabel: r.primary_suite_name ?? r.suite_names?.[0] ?? null,
      suiteNames: r.suite_names ?? null,
      whenRel: ageRel,
      // Date + time (was date-only) so two runs on the same day are
      // distinguishable; shared compact format with /live + /agents.
      whenAbs: formatRunWhen(r.created_at),
      failures: r.failed_tests ?? 0,
      clusters: isFocus ? clusters.length : null,
      findings: isFocus ? findings.length : null,
      avgConf: isFocus ? preScanAvgConfidence : null,
      cost: isFocus ? trailCost : null,
      status,
    }
  })

  // Last analysis = the focused run's completed deep pipeline (decision
  // trail). This used to read the first "complete" row of the invented
  // table above, and parsed its age from the RUN's "3d ago" label — so a
  // three-day-old run read "3h ago".
  const lastAnalysisDone = trail?.pipeline_status === 'completed'
  const lastRunAgeHours = lastAnalysisDone && trail?.completed_at
    ? Math.max(0, Math.floor((Date.now() - new Date(trail.completed_at).getTime()) / 3600000))
    : null
  const lastRunSummary = lastAnalysisDone
    ? [
        `${clusters.length} cluster${clusters.length === 1 ? '' : 's'}`,
        `${findings.length} finding${findings.length === 1 ? '' : 's'}`,
        ...(trailCost != null ? [`$${trailCost.toFixed(2)}`] : []),
      ].join(' · ')
    : null

  // Workload breakdown — real counts per stage. There is no server-side
  // price book, so no per-stage $ figures are fabricated here; the estimate
  // card shows the decision trail's ACTUAL cost for the last investigation.
  const estimateBreakdown: EstimateRow[] = [
    { label: 'Clustering',        detail: `${eligibleFailures} embeds` },
    { label: 'Root cause',        detail: `${proposedClusters.length} traces` },
    { label: 'Evidence synth',    detail: `${proposedClusters.length} packs` },
    { label: 'Defect drafts',     detail: `${proposedClusters.length} drafts` },
  ]
  // Time estimate: 0.5 min base + 0.05 min/failure clustering + 1 min/cluster RCA
  const estimateMinutes = 0.5 + eligibleFailures * 0.05 + proposedClusters.length * 1.0

  // Severity distribution of the SCORED proposed clusters (an unscored one
  // has no severity).
  const sevCounts = proposedClusters.reduce(
    (acc, c) => {
      if (c.severity) acc[c.severity.toLowerCase() as 'p0' | 'p1' | 'p2' | 'p3']++
      return acc
    },
    { p0: 0, p1: 0, p2: 0, p3: 0 },
  )

  // Spend — real project LLM meter (GET /llm-usage) + quota (GET /llm-quota).
  // Honest $0.00 when the usage meter is empty; null budget when no quota
  // has been configured (the UI renders a "no budget set" state, never a
  // fabricated cap).
  const spendMtdDollars = usage?.total_cost_usd ?? 0
  const spendBudgetDollars =
    usage?.hard_cap_usd ?? (quota?.enabled ? quota.hard_cap_usd : null)
  const spendSoftWarnPct = quota?.enabled ? quota.soft_warn_threshold_pct : null

  return {
    focusedRun,
    eligibleFailures,
    // The page's real scope (it has no time window): eligibleFailures above
    // counts the focused run, or the latest runs when none is focused. This
    // used to read a "6h / 24h / 7d" setting that was never sent anywhere.
    windowLabel: focusedRun
      ? (focusedRun.run_seq != null ? `Run #${focusedRun.run_seq}` : `run ${focusedRun.id.slice(0, 8)}`)
      : `latest ${recentRuns.length} run${recentRuns.length === 1 ? '' : 's'}`,
    // A focused run's creation time. (This was "since <commit>", where the
    // "commit" was the first 7 characters of the run's UUID.)
    windowSince: focusedRun?.created_at ? formatRunWhen(focusedRun.created_at) : null,
    evidenceSources,
    evidenceConnected: evidenceSources.filter(s => s.status === 'live').length,
    evidenceTotal: evidenceSources.length,
    preScanClusters: proposedClusters.length,
    preScanAvgConfidence,
    proposedClusters,
    pastInvestigations,
    lastRunAgeHours,
    lastRunSummary,
    spendMtdDollars,
    spendBudgetDollars,
    spendSoftWarnPct,
    lastRunCostDollars: trailCost,
    estimateBreakdown,
    estimateMinutes,
    severityCounts: { ...sevCounts },
  }
}

function pickVerdict(model: DeepModel, pipelineStatus: { status: string } | null): Verdict {
  if (isPipelineInProgress(pipelineStatus?.status)) return 'RUNNING'
  if (pipelineStatus?.status === 'failed')  return 'FAILED'
  // NO_FAILURES before NO_SOURCES: with real integration-health data an
  // install with zero external integrations AND zero runs would otherwise
  // land on the misleading "connect an evidence source" verdict.
  if (model.eligibleFailures === 0)         return 'NO_FAILURES'
  if (model.evidenceConnected === 0)        return 'NO_SOURCES'
  if (model.proposedClusters.length === 0)  return 'PENDING'
  return 'READY'
}

// ── Verdict card ────────────────────────────────────────────────────────
function VerdictCard({
  model, verdict, headline, lede, onRunAll, isQaEngineer,
}: {
  model: DeepModel
  verdict: Verdict
  headline: React.ReactNode
  lede: React.ReactNode
  onRunAll: () => void
  isQaEngineer: boolean
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Investigation verdict"
      aria-live="polite"
      className="relative rounded-xl border overflow-hidden grid gap-6"
      style={{
        gridTemplateColumns: '1.65fr 1fr',
        background: `${t.glow}, var(--color-bg-card)`,
        borderColor: t.border,
        padding: '18px 20px',
        marginBottom: 14,
      }}
    >
      <span aria-hidden className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: t.bar }} />

      <div className="min-w-0" style={{ paddingLeft: 4 }}>
        <span
          className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase"
          style={{ color: t.eyebrowText, letterSpacing: 'var(--tracking-wider)' }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: t.bar,
              animation: t.pulse ? 'testlookup-pulse 1.6s ease-out infinite' : undefined,
            }}
            aria-hidden
          />
          {t.label}
        </span>
        <h2 className="font-bold m-0" style={{ fontSize: 'var(--text-display-sm)', lineHeight: 1.15, letterSpacing: '-0.02em', margin: '6px 0 6px' }}>
          {headline}
        </h2>
        <p className="text-[13px] m-0 mb-3.5 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
          {lede}
        </p>

        {/* Input facets */}
        <div className="grid gap-2.5 mb-3" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          {/* The sub-line used to say "N suites" where N was the number of
              proposed clusters. */}
          <FacetTile
            label="Failures eligible"
            value={model.eligibleFailures}
            sub={`in ${model.windowLabel}`}
            tone="bad"
          />
          <FacetTile
            label="Scope"
            value={model.windowLabel}
            sub={model.windowSince ?? 'no run focused'}
            tone="neutral"
          />
          <FacetTile
            label="Evidence sources"
            value={`${model.evidenceConnected} / ${model.evidenceTotal}`}
            sub={model.evidenceConnected > 0
              ? model.evidenceSources.filter(s => s.status === 'live').map(s => s.id).join(', ')
              : 'none live'}
            tone={model.evidenceConnected >= 3 ? 'neutral' : 'warn'}
          />
          <FacetTile
            label="Pre-scan signal"
            value={model.preScanClusters > 0 ? `${model.preScanClusters} clusters` : '—'}
            sub={model.preScanClusters === 0 ? 'run analysis'
              : model.preScanAvgConfidence != null ? `avg conf ${model.preScanAvgConfidence.toFixed(2)}`
              : 'unscored'}
            tone={model.preScanClusters > 0 ? 'good' : 'neutral'}
          />
        </div>

        {/* CTAs */}
        <div className="flex items-center gap-2 flex-wrap">
          <PrimaryBtn
            onClick={onRunAll}
            disabled={!isQaEngineer || verdict === 'NO_FAILURES' || verdict === 'NO_SOURCES'}
            title={isQaEngineer ? `Run on all ${model.eligibleFailures} failures` : 'QA Engineer role required'}
          >
            <Play className="h-3.5 w-3.5" />
            Run on all {model.eligibleFailures} failure{model.eligibleFailures === 1 ? '' : 's'}
          </PrimaryBtn>
        </div>
      </div>

      <div className="flex flex-col gap-3.5 pt-0.5 min-w-0">
        <EstimateCard model={model} />
      </div>
    </section>
  )
}

function FacetTile({
  label, value, sub, tone,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  tone: 'bad' | 'warn' | 'good' | 'neutral'
}) {
  const valueColor =
    tone === 'bad'  ? 'var(--status-failed)' :
    tone === 'warn' ? 'var(--status-broken)' :
    tone === 'good' ? 'var(--status-passed)' :
    'var(--color-text)'
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="text-[14px] font-semibold tabular-nums mt-0.5 truncate" style={{ color: valueColor }}>
        {value}
      </div>
      {sub && <div className="text-[10.5px] text-[var(--color-text-muted)] truncate">{sub}</div>}
    </div>
  )
}

// ── Estimate card ───────────────────────────────────────────────────────
// Headline $ is the ACTUAL cost of the focused run's last investigation
// (decision trail). There is no server-side price book, so no per-stage $
// estimates are fabricated — the breakdown lists real workload counts and
// the budget bar reads the real project spend/quota.
function EstimateCard({ model }: { model: DeepModel }) {
  const budget = model.spendBudgetDollars
  const usedPct = budget && budget > 0
    ? Math.min(100, (model.spendMtdDollars / budget) * 100)
    : 0
  const softWarnPct = model.spendSoftWarnPct
  return (
    <div
      className="rounded-md border flex flex-col gap-2.5 p-3.5"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div>
        <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
          Cost &amp; time
        </div>
        <div className="flex items-end gap-2 mt-0.5">
          <span className="font-bold tabular-nums leading-none" style={{ fontSize: 32, color: 'var(--color-text)', letterSpacing: '-0.02em' }}>
            {model.lastRunCostDollars != null ? `$${model.lastRunCostDollars.toFixed(2)}` : '—'}
          </span>
          <span className="text-[12px] text-[var(--color-text-muted)] mb-0.5">
            {model.lastRunCostDollars != null ? 'last run' : 'no costed run yet'}
          </span>
          <span
            className="ml-auto inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold tabular-nums"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
          >
            ~{model.estimateMinutes.toFixed(1)} min
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-1.5 text-[12px]">
        {model.estimateBreakdown.map((r, i) => (
          <div key={i} className="flex items-baseline justify-between gap-3">
            <span className="text-[var(--color-text-secondary)]">{r.label}</span>
            <span className="text-[var(--color-text-muted)] tabular-nums">{r.detail}</span>
          </div>
        ))}
      </div>

      {/* Budget bar — real spend vs the configured monthly quota. */}
      {budget != null && budget > 0 ? (
        <div className="mt-1">
          <div className="relative h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
            <i className="block h-full" style={{ width: `${usedPct}%`, background: 'var(--color-accent)' }} />
            {softWarnPct != null && softWarnPct > 0 && softWarnPct < 100 && (
              <span
                aria-hidden
                className="absolute top-0 bottom-0"
                style={{ left: `${softWarnPct}%`, width: 1, background: 'var(--status-broken)' }}
                title={`Soft warn at ${softWarnPct}%`}
              />
            )}
          </div>
          <div className="text-[10.5px] text-[var(--color-text-muted)] mt-1.5 flex justify-between">
            <span>${model.spendMtdDollars.toFixed(2)} of ${budget} monthly</span>
            {softWarnPct != null && <span>warn at {softWarnPct}%</span>}
          </div>
        </div>
      ) : (
        <div className="text-[10.5px] text-[var(--color-text-muted)] mt-1">
          ${model.spendMtdDollars.toFixed(2)} spent this month · no budget set — configure in Settings → Billing
        </div>
      )}
    </div>
  )
}

// ── KPI strip ───────────────────────────────────────────────────────────
type KpiTone = 'good' | 'warn' | 'bad' | 'accent' | 'neutral'

function KpiCell({
  Icon, label, value, sub, meta, tone = 'neutral', spark, isFirst, isLast,
}: {
  Icon?: typeof Sparkles
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  meta?: React.ReactNode
  tone?: KpiTone
  spark?: React.ReactNode
  isFirst?: boolean
  isLast?: boolean
}) {
  const valueColor =
    tone === 'good'   ? 'var(--status-passed)' :
    tone === 'warn'   ? 'var(--status-broken)' :
    tone === 'bad'    ? 'var(--status-failed)' :
    tone === 'accent' ? 'var(--color-accent)' :
    'var(--color-text)'
  return (
    <div
      className="flex flex-col gap-1"
      style={{
        padding: '14px 18px',
        background: 'var(--color-bg-card)',
        borderTop:    '1px solid var(--color-border)',
        borderBottom: '1px solid var(--color-border)',
        borderRight:  '1px solid var(--color-border)',
        borderLeft:   isFirst ? '1px solid var(--color-border)' : '0',
        borderTopLeftRadius:     isFirst ? 'var(--radius-lg)' : 0,
        borderBottomLeftRadius:  isFirst ? 'var(--radius-lg)' : 0,
        borderTopRightRadius:    isLast  ? 'var(--radius-lg)' : 0,
        borderBottomRightRadius: isLast  ? 'var(--radius-lg)' : 0,
      }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex items-center gap-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {Icon && <Icon className="h-3 w-3 opacity-70" />}
        <span>{label}</span>
      </div>
      <div className="font-bold tabular-nums leading-[1.1]" style={{ fontSize: 'var(--text-stat-lg)', letterSpacing: '-0.01em', color: valueColor }}>
        {value}
        {sub && <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-1">{sub}</span>}
      </div>
      {meta && <div className="text-[10.5px] text-[var(--color-text-muted)]">{meta}</div>}
      {spark && <div className="mt-1.5">{spark}</div>}
    </div>
  )
}

/** The confidence target the meta line states ("target ≥ 0.7"), and the KPI's own tone bands. */
const CONFIDENCE_TARGET = { value: 0.7, label: 'target' } as const
const CONFIDENCE_BANDS: GaugeBands = { direction: 'higher-is-better', thresholds: [0.7, 0.8] }
const CONFIDENCE_DOMAIN = [0, 1] as const
const confidenceText = (v: number) => v.toFixed(2)
const dollars = (v: number) => `$${v.toFixed(2)}`

/**
 * The five investigation KPIs. Owner decision OD-1 (Wave 2.5): a KPI glyph
 * draws a REAL series or a real scalar, or nothing. There is no per-day
 * history here (spend is per billing period, clusters are per run), so:
 *
 *   - Likely clusters: the proposed clusters by severity, P0..P3, as a
 *     stacked `GaugeBar` (only when there are clusters). It sat under
 *     "Eligible failures", where a three-segment bar under "12" read as a
 *     split of the 12 failures; it splits the clusters this cell counts
 *     (`preScanClusters` is `proposedClusters.length`). Its name is drawn
 *     above it and the counts are written under it, so neither the name nor
 *     the P0..P3 split is for a screen reader only, nor colour only (R2 F8);
 *   - Avg cluster confidence: a `GaugeBar` on 0-1 with the 0.7 target the
 *     meta line states; not measured until analysis has proposed a cluster;
 *   - Spend MTD: spend against the budget — and NO bar when there is no
 *     budget. It used to draw eight bars for "no budget set";
 *   - Last analysis: no glyph. It and Likely clusters were the same literal
 *     rising polyline, whatever the numbers said.
 *
 * With no proposed cluster there is no average confidence: both cells say
 * "—" in the neutral tone, never "avg conf 0.00" or a red "—".
 */
export function InvestigationKpiStrip({ model }: { model: DeepModel }) {
  const sev = model.severityCounts
  const clusterTotal = sev.p0 + sev.p1 + sev.p2 + sev.p3
  const unscoredClusters = model.proposedClusters.filter(c => c.confidence == null).length
  const severitySplit = [
    ...(['p0', 'p1', 'p2', 'p3'] as const)
      .filter((key) => sev[key] > 0)
      .map((key) => `${sev[key]} ${key.toUpperCase()}`),
    ...(unscoredClusters > 0 ? [`${unscoredClusters} unscored`] : []),
  ].join(' · ')
  const severityGauge = clusterTotal > 0 ? (
    <>
      <GaugeBar
        value={clusterTotal}
        domain={[0, clusterTotal]}
        size="sm"
        label="Proposed clusters by severity"
        showLabel
        segments={[
          { value: sev.p0, label: 'P0', tone: 'bad' },
          { value: sev.p1, label: 'P1', tone: 'warn' },
          { value: sev.p2, label: 'P2', tone: 'accent' },
          { value: sev.p3, label: 'P3', tone: 'neutral' },
        ]}
      />
      <div className="mt-1 text-[10.5px] tabular-nums text-[var(--color-text-secondary)]">{severitySplit}</div>
    </>
  ) : undefined
  // An average exists only when analysis proposed a cluster AND at least one
  // of them carries a real score.
  const avgConfidence = model.preScanClusters > 0 ? model.preScanAvgConfidence : null
  const hasClusters = model.preScanClusters > 0
  const scoredBelowTarget = model.proposedClusters
    .filter(c => c.confidence != null && c.confidence < CONFIDENCE_TARGET.value).length
  const confidenceGauge = (
    <GaugeBar
      value={avgConfidence}
      domain={CONFIDENCE_DOMAIN}
      tone={CONFIDENCE_BANDS}
      target={CONFIDENCE_TARGET}
      size="sm"
      format={confidenceText}
      label="Average cluster confidence"
    />
  )
  const budget = model.spendBudgetDollars
  const spendGauge = budget != null && budget > 0 ? (
    <GaugeBar
      value={model.spendMtdDollars}
      domain={[0, budget]}
      tone="accent"
      size="sm"
      format={dollars}
      label="Spend this month against the monthly budget"
    />
  ) : undefined
  return (
    <section aria-label="Investigation inputs" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
      <KpiCell
        Icon={AlertCircle}
        label="Eligible failures"
        value={model.eligibleFailures}
        tone={model.eligibleFailures > 0 ? 'bad' : 'good'}
        meta={<>{model.evidenceConnected}/{model.evidenceTotal} sources · {model.windowLabel}</>}
        isFirst
      />
      <KpiCell
        Icon={Layers}
        label="Likely clusters"
        value={model.preScanClusters}
        tone="accent"
        meta={<>pre-scan · avg conf {avgConfidence != null ? avgConfidence.toFixed(2) : '—'}</>}
        spark={severityGauge}
      />
      <KpiCell
        Icon={Clock}
        label="Last analysis"
        value={model.lastRunAgeHours != null ? model.lastRunAgeHours : '—'}
        sub={model.lastRunAgeHours != null ? 'h ago' : undefined}
        tone="neutral"
        meta={model.lastRunSummary ?? <>no prior run</>}
      />
      <KpiCell
        Icon={Target}
        label="Avg cluster confidence"
        value={avgConfidence != null ? avgConfidence.toFixed(2) : '—'}
        tone={avgConfidence == null ? 'neutral' : avgConfidence >= 0.8 ? 'good' : avgConfidence >= 0.7 ? 'warn' : 'bad'}
        meta={hasClusters && avgConfidence == null
          ? <>no proposed cluster has a score</>
          : <>target ≥ 0.7 · {scoredBelowTarget} below</>}
        spark={confidenceGauge}
      />
      <KpiCell
        Icon={DollarSign}
        label="Spend MTD"
        value={`$${model.spendMtdDollars.toFixed(2)}`}
        tone="neutral"
        meta={model.spendBudgetDollars != null && model.spendBudgetDollars > 0
          ? <>of ${model.spendBudgetDollars} budget · {Math.round((model.spendMtdDollars / model.spendBudgetDollars) * 100)}%</>
          : <>no budget set</>}
        spark={spendGauge}
        isLast
      />
    </section>
  )
}

// ── Proposed clusters preview ───────────────────────────────────────────
function ProposedClustersCard({ model, focusedRunId }: { model: DeepModel; focusedRunId: string | null }) {
  if (model.proposedClusters.length === 0) {
    return (
      <CardShell title="Proposed clusters" rightSlot={<span>0 candidates</span>}>
        <div className="px-4 py-8 text-center text-[13px] text-[var(--color-text-secondary)]">
          Run analysis to discover clusters.
        </div>
      </CardShell>
    )
  }
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          Proposed clusters
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold uppercase"
            style={{ background: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', color: 'var(--color-accent)', letterSpacing: 'var(--tracking-wide)' }}
          >
            pre-scan
          </span>
        </span>
      }
      rightSlot={<span><strong className="text-[var(--color-text)] font-semibold">{model.proposedClusters.length}</strong> candidates · {model.eligibleFailures} failures grouped</span>}
    >
      <div className="px-4 py-3.5 flex flex-col gap-2">
        {model.proposedClusters.map(c => (
          <ClusterRow key={c.id} cluster={c} />
        ))}
      </div>
      <div
        className="flex items-center justify-between gap-2 px-4 py-2.5 text-[11.5px] text-[var(--color-text-muted)]"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <span>
          Pre-scan groups failures by embedding similarity. Deep analysis walks the stack, fetches commits &amp; telemetry, and confirms or splits these.
        </span>
        {focusedRunId && (
          <Link
            to={`/runs/${focusedRunId}`}
            className="font-medium hover:underline whitespace-nowrap"
            style={{ color: 'var(--color-accent)' }}
          >
            View all {model.eligibleFailures} failures →
          </Link>
        )}
      </div>
    </CardShell>
  )
}

function ClusterRow({ cluster }: { cluster: ProposedCluster }) {
  const sevPalette: Record<NonNullable<ProposedCluster['severity']>, { bg: string; bd: string; fg: string }> = {
    P0: { bg: 'color-mix(in srgb, var(--status-failed) 18%, transparent)',   bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',   fg: 'var(--status-failed)' },
    P1: { bg: 'color-mix(in srgb, var(--status-broken) 18%, transparent)',  bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',  fg: 'var(--status-broken)' },
    P2: { bg: 'color-mix(in srgb, var(--color-accent) 18%, transparent)',  bd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',  fg: 'var(--color-accent)' },
    P3: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' },
  }
  // An unscored cluster earns no severity band: neutral chrome, "unscored".
  const sev = cluster.severity
    ? sevPalette[cluster.severity]
    : { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' }
  return (
    <div
      className="grid items-start gap-3 rounded-md border"
      style={{ gridTemplateColumns: '36px 1fr', padding: '10px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <span
        aria-hidden
        className="inline-flex items-center justify-center rounded-md font-bold tabular-nums text-[14px]"
        style={{ width: 32, height: 32, background: sev.bg, border: `1px solid ${sev.bd}`, color: sev.fg }}
      >
        {cluster.failures}
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-semibold text-[var(--color-text)] truncate">{cluster.name}</span>
          <span
            className="inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-semibold uppercase"
            style={{ background: sev.bg, border: `1px solid ${sev.bd}`, color: sev.fg, letterSpacing: 'var(--tracking-wide)' }}
          >
            {cluster.isFlaky ? 'flaky' : cluster.severity ? `${cluster.severity} likely` : 'unscored'}
          </span>
          <span className="text-[10.5px] text-[var(--color-text-muted)]">
            {cluster.failures} failure{cluster.failures === 1 ? '' : 's'} · {cluster.testCount} test{cluster.testCount === 1 ? '' : 's'}
          </span>
        </div>
        {cluster.fromFinding ? (
          // US-15.1: a real deep-pipeline root cause is an AI conclusion —
          // it renders in the shared trust chrome, with the row's `origin`
          // and `confidence_basis` (both already on the wire, previously
          // never rendered) surfaced as provenance.
          <AISuggestion
            bare
            className="mt-1"
            label="root cause"
            confidence={cluster.confidence != null ? Math.round(cluster.confidence * 100) : null}
            confidenceBasis={cluster.confidenceBasis}
            provenance={cluster.origin ? { modeUsed: ORIGIN_LABELS[cluster.origin] } : null}
          >
            <p className="text-[12px] m-0" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
              {cluster.rationale}
            </p>
          </AISuggestion>
        ) : (
          <p className="text-[12px] m-0 mt-1" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
            {cluster.rationale}
            {cluster.confidence != null ? (
              <>
                {' '}Cluster cohesion{' '}
                <strong className="font-semibold" style={{ color: cluster.confidence >= 0.85 ? 'var(--status-passed)' : cluster.confidence >= 0.65 ? 'var(--status-broken)' : 'var(--status-failed)' }}>
                  {cluster.confidence.toFixed(2)}
                </strong>.
              </>
            ) : <> No cohesion score.</>}
            {' '}No AI finding recorded for this cluster yet.
          </p>
        )}
        {/* Only the service the AI finding names. This line used to add an
            invented "@team-<word>" owner handle and "N affected" capped at 5. */}
        {cluster.affectedService && (
          <div className="mt-1.5 text-[10.5px] text-[var(--color-text-muted)]">
            service <code className="font-mono">{cluster.affectedService}</code>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Past investigations ────────────────────────────────────────────────
function PastInvestigations({ rows, onOpen }: { rows: PastRun[]; onOpen: (runId: string) => void }) {
  return (
    <CardShell
      title="Recent runs"
      rightSlot={<span>latest {rows.length} · investigation data for the focused run</span>}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
              <Th label="Test Suite" />
              {/* Per-(project, suite) human-readable run number,
                  alongside the suite the row belongs to. Same value
                  rendered on /runs, /live, /my-failures, /intelligence,
                  and /agents — the column is named "Run" everywhere. */}
              <Th label="Run" />
              <Th label="Failures" align="right" />
              <Th label="Clusters" align="right" />
              <Th label="Findings" align="right" />
              <Th label="Avg conf" align="right" />
              <Th label="Cost" align="right" />
              <Th label="Status" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center py-8 text-[var(--color-text-muted)]">
                  No runs yet.
                </td>
              </tr>
            )}
            {rows.map(r => <PastRow key={r.runId} row={r} onOpen={() => onOpen(r.runId)} />)}
          </tbody>
        </table>
      </div>
    </CardShell>
  )
}

function PastRow({ row, onOpen }: { row: PastRun; onOpen: () => void }) {
  const confColor = row.avgConf == null ? 'var(--color-text-faint)' : row.avgConf >= 0.8 ? 'var(--status-passed)' : row.avgConf >= 0.6 ? 'var(--status-broken)' : 'var(--status-failed)'
  const statusPalette = row.status === 'complete'
    ? { bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-passed) 25%, transparent)', fg: 'var(--status-passed)', label: 'Complete' }
    : row.status === 'running'
      ? { bg: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', bd: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', fg: 'var(--color-accent)', label: 'Running' }
      : row.status === 'failed'
        ? { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 25%, transparent)', fg: 'var(--status-broken)', label: 'Failed' }
        : null
  const unknown = <span className="text-[var(--color-text-faint)]">—</span>
  return (
    <tr
      style={{ borderBottom: '1px solid var(--color-border)' }}
      className="transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer"
      onClick={onOpen}
    >
      <td style={{ padding: '10px 12px' }}>
        <div className="flex flex-col min-w-0">
          {/* Clickable badge → /test-management Test Suites tab pre-
              filtered to this suite. Same deep-link shape used by the
              /runs and /live tables. SuiteBadge handles the
              ``stopPropagation`` internally so clicking the chip
              doesn't also fire the row's onClick (run-detail nav). */}
          <SuiteBadge
            primary={row.suiteLabel}
            all={row.suiteNames}
            className="self-start max-w-full"
            linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
          />
          <span className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums mt-1">
            {row.whenRel} · {row.whenAbs}
          </span>
        </div>
      </td>
      <td style={{ padding: '10px 12px' }}>
        {/* Run #N when known; fall back to the 8-char run-uuid slug
            (already computed as ``row.runIdLabel`` above) so legacy
            rows still have a clickable handle. */}
        <span className="font-mono text-[12px] text-[var(--color-text)]">
          {row.runSeq != null ? `Run #${row.runSeq}` : row.runIdLabel}
        </span>
      </td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>{row.failures}</td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>{row.clusters ?? unknown}</td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>{row.findings ?? unknown}</td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>
        {row.avgConf == null ? unknown : (
          <span className="inline-flex items-center gap-1.5" style={{ color: confColor }}>
            <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
            {row.avgConf.toFixed(2)}
          </span>
        )}
      </td>
      <td className="text-right tabular-nums font-mono text-[11.5px]" style={{ padding: '10px 12px' }}>
        {row.cost == null ? unknown : `$${row.cost.toFixed(2)}`}
      </td>
      <td style={{ padding: '10px 12px' }}>
        {statusPalette == null ? unknown : (
          <span
            className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[10.5px]"
            style={{ background: statusPalette.bg, border: `1px solid ${statusPalette.bd}`, color: statusPalette.fg }}
          >
            <i
              aria-hidden
              className={clsx(row.status === 'running' && 'animate-pulse')}
              style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }}
            />
            {statusPalette.label}
          </span>
        )}
      </td>
    </tr>
  )
}

function Th({ label, align }: { label: string; align?: 'right' }) {
  return (
    <th
      style={{
        padding: '8px 12px',
        textAlign: align ?? 'left',
        color: 'var(--color-text-muted)',
        fontWeight: 500,
        fontSize: 10.5,
        textTransform: 'uppercase',
        letterSpacing: 'var(--tracking-wider)',
      }}
    >
      {label}
    </th>
  )
}

// ── Right rail: Evidence sources ─────────────────────────────────────────
function EvidenceSourcesCard({
  sources, isLoading, isError,
}: { sources: EvidenceSource[]; isLoading: boolean; isError: boolean }) {
  const externalCount = sources.filter(s => s.id !== 'tests').length
  return (
    <CardShell title="Evidence sources" rightSlot={<span>{sources.filter(s => s.status === 'live').length} live</span>}>
      {isLoading && externalCount === 0 ? (
        <div className="px-4 py-6 flex justify-center"><LoadingSpinner size="sm" /></div>
      ) : (
      <>
      {(isError || externalCount === 0) && (
        <div className="px-4 pt-3 text-[12px] text-[var(--color-text-muted)]">
          {isError
            ? 'Could not load integration health.'
            : 'No integrations configured — connect Jira, GitHub, Splunk and more under Settings → Integration Health.'}
        </div>
      )}
      <div className="px-4 py-3 flex flex-col gap-2">
        {sources.map(src => {
          const Icon = src.icon
          const statusPalette = src.status === 'live'
            ? { bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-passed) 25%, transparent)', fg: 'var(--status-passed)' }
            : src.status === 'lagging'
              ? { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 25%, transparent)', fg: 'var(--status-broken)' }
              : { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' }
          return (
            <div
              key={src.id}
              className="grid items-center gap-2.5 rounded-md border text-left"
              style={{ gridTemplateColumns: '24px 1fr auto', padding: '8px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
            >
              <span className="inline-flex items-center justify-center rounded-full" style={{ width: 24, height: 24, background: src.toneBg, color: src.toneFg }}>
                <Icon className="h-3 w-3" />
              </span>
              <div className="min-w-0">
                <div className="text-[12.5px] text-[var(--color-text)] font-medium">{src.name}</div>
                <div className="text-[10.5px] text-[var(--color-text-muted)] truncate">{src.description}</div>
              </div>
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
                style={{ background: statusPalette.bg, border: `1px solid ${statusPalette.bd}`, color: statusPalette.fg }}
              >
                {src.detail}
              </span>
            </div>
          )
        })}
      </div>
      </>
      )}
    </CardShell>
  )
}

// ── Right rail: Model routing ────────────────────────────────────────────
// Renders the ACTUAL per-stage routing recorded in the focused run's
// decision trail (GET /runs/{id}/decision-trail) — analysis_mode /
// execution_path per stage plus a fallback flag. No static model catalog
// and no fabricated per-token rates: when the run has no trail yet, an
// explanatory empty state renders instead.
function ModelRoutingCard({
  trail, isLoading,
}: { trail: DecisionTrailResponse | undefined; isLoading: boolean }) {
  const stages = (trail?.stages ?? []).filter(s => s.analysis_mode || s.execution_path)
  return (
    <CardShell title="Model routing">
      {isLoading ? (
        <div className="px-4 py-5 flex justify-center"><LoadingSpinner size="sm" /></div>
      ) : stages.length === 0 ? (
        <div className="px-4 py-4 text-[12px] text-[var(--color-text-muted)]">
          Routing appears once an investigation runs — each stage records the
          engine it actually used (rules / ML / LLM) in the decision trail.
        </div>
      ) : (
        <div className="px-4 py-3 flex flex-col gap-1.5">
          {stages.map((s, i) => (
            <div key={i} className="flex items-baseline justify-between gap-2 text-[12px]">
              <span className="text-[var(--color-text-secondary)] capitalize">
                {s.stage_name.replace(/_/g, ' ')}
              </span>
              <span className="inline-flex items-center gap-1.5 ml-auto">
                {s.fallback_used && (
                  <span
                    className="inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-semibold"
                    style={{ background: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--status-broken) 25%, transparent)', color: 'var(--status-broken)' }}
                    title={s.fallback_reason ?? 'Fallback engine used'}
                  >
                    fallback
                  </span>
                )}
                <code className="font-mono text-[11px] px-1.5 py-px rounded" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}>
                  {s.analysis_mode ?? s.execution_path}
                </code>
                {s.analysis_mode && s.execution_path && s.execution_path !== s.analysis_mode && (
                  <span className="text-[10.5px] text-[var(--color-text-muted)]">{s.execution_path}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </CardShell>
  )
}

// ── Shared shell ─────────────────────────────────────────────────────────
function CardShell({
  title, rightSlot, children,
}: { title: React.ReactNode; rightSlot?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="flex items-center justify-between gap-2.5 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">{title}</h3>
        {rightSlot && <div className="flex items-center gap-2.5 text-[12px] text-[var(--color-text-muted)]">{rightSlot}</div>}
      </div>
      {children}
    </div>
  )
}

/** Whether a run belongs to any of the selected suites (OR within the axis). */
function runInSuites(run: TestRun, suites: readonly string[]): boolean {
  const own = [
    ...(run.suite_names ?? []),
    ...(run.primary_suite_name ? [run.primary_suite_name] : []),
  ]
  return suites.some(name => own.some(value => suiteMatchesValue(value, name)))
}

// ── Page ────────────────────────────────────────────────────────────────
export default function DeepInvestigationPage() {
  const { runId } = useParams<{ runId?: string }>()
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { isQaEngineer } = usePermissions()

  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter: pageSuiteFilter, suiteNames, suiteLabel } = usePageSuiteFilter()
  const { options: suiteOptions } = useSuiteOptions(0)

  // Recent runs to populate the past-investigations table + auto-pick a focus.
  const { data: recentRuns, isLoading: runsLoading, isValidating: runsValidating } = useRuns({ page: 1, size: 6, days: 0, ...(pageSuiteFilter && { suite_name: pageSuiteFilter }) })
  const recentItems = useMemo<TestRun[]>(() => (recentRuns?.items ?? []) as TestRun[], [recentRuns])

  // Auto-route to the most recent run if no runId is in the URL — preserved
  // from the prior page so deep links keep working.
  useEffect(() => {
    if (!runId && recentItems[0]?.id && !runsLoading && project) {
      navigate(`/deep-investigate/${recentItems[0].id}`, { replace: true })
    }
  }, [runId, recentItems, runsLoading, project, navigate])

  // Direct fetch for the deep-linked run when it isn't in the recent-6
  // list (the user arrived from another page with a runId that's older
  // than the page-size cutoff). Without this, ``focusedRun`` was null →
  // ``eligibleFailures`` 0 → the KPI panels rendered empty until the
  // user picked a suite (which re-fetched and surfaced the run via the
  // suite-filtered request). The direct fetch makes the KPIs populate
  // on initial load regardless of how the user arrived.
  const fallbackFetch = useRun(
    runId && !recentItems.find(r => r.id === runId) ? runId : undefined,
  )
  const focusedRun = (recentItems.find(r => r.id === runId)
    ?? (fallbackFetch.data as TestRun | undefined)
    ?? null)

  // The user CHANGED the suite and the focused run is not in it: jump to the
  // suite's newest run. Only a change made after mount counts. A suite that
  // was already applied when the page opened — the global filter (VIZ-303)
  // restored from storage or a link, or the flag resolving and revealing it —
  // is not a request to leave a deep-linked run, and redirecting on it opened
  // a different run than the link named. Never decided while anything it
  // depends on is loading: the suite's run list, or the direct fetch of the
  // focused run (it may well be in the suite, just older than the newest six).
  const suiteKey = scopeKey(suiteNames) ?? ''
  const suiteBaselineRef = useRef<string | null>(null)
  const focusedRunLoading = Boolean(fallbackFetch.isLoading)
  useEffect(() => {
    if (suiteBaselineRef.current === null) {
      suiteBaselineRef.current = suiteKey
      return
    }
    if (suiteKey === suiteBaselineRef.current) return
    if (!pageSuiteFilter || !runId) {
      suiteBaselineRef.current = suiteKey
      return
    }
    if (runsLoading || runsValidating || focusedRunLoading || recentItems.length === 0) return
    suiteBaselineRef.current = suiteKey
    if (recentItems.some(r => r.id === runId)) return
    if (focusedRun && runInSuites(focusedRun, suiteNames)) return
    navigate(`/deep-investigate/${recentItems[0].id}`, { replace: true })
  }, [suiteKey, suiteNames, pageSuiteFilter, runId, recentItems, focusedRun, runsLoading, runsValidating, focusedRunLoading, navigate])

  const { data: clusters = [] } = useFailureClusters(runId ?? null)
  const { data: findings = [] } = useDeepFindings(runId ?? null)

  // Pipeline status fetched declaratively via SWR (refreshes on focus); null
  // while loading, before a run is selected, or on a failed load.
  const { data: pipelineStatus = null } = usePipelineStatus(runId ?? null, 'deep')

  // Evidence-source health — real integration probes plus the intrinsic
  // "test results" source (live whenever runs exist in the workspace).
  const {
    statuses: integrationStatuses,
    isLoading: integrationsLoading,
    isError: integrationsError,
  } = useIntegrationStatus()
  const evidenceSources = useMemo<EvidenceSource[]>(
    () => [testResultsSource(recentItems.length > 0), ...integrationStatuses.map(toEvidenceSource)],
    [integrationStatuses, recentItems],
  )

  // Spend + budget — real project LLM meter. In "All Projects" mode fall
  // back to the focused run's project so the numbers stay scoped and real.
  const budgetProjectId =
    activeProjectId && activeProjectId !== ALL_PROJECTS_ID
      ? activeProjectId
      : focusedRun?.project_id ?? null
  const { usage } = useProjectUsage(budgetProjectId)
  const { quota } = useProjectQuota(budgetProjectId)

  // Actual per-stage routing + cost for the focused run.
  const { data: decisionTrail, isLoading: trailLoading } = useDecisionTrail(runId ?? null)

  const model = useMemo(
    () => buildModel({
      clusters, findings, pipelineStatus, recentRuns: recentItems, focusedRun,
      evidenceSources, usage, quota, trail: decisionTrail,
    }),
    [clusters, findings, pipelineStatus, recentItems, focusedRun, evidenceSources, usage, quota, decisionTrail],
  )
  const verdict = pickVerdict(model, pipelineStatus)

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Bot className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to start a deep investigation."
      />
    )
  }

  if (runsLoading && recentItems.length === 0) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }

  const projectLabel = project?.name ?? 'All Projects'

  // Verdict copy ────────────────────────────────────────────────────────
  // "stage N of M" from the pipeline's own stage summary (it said "of 4",
  // the count of the deleted ribbon's invented stages).
  const stageSummary = pipelineStatus?.stage_summary
  const stageTotal = stageSummary
    ? stageSummary.completed + stageSummary.failed + stageSummary.skipped + stageSummary.pending
    : 0
  const stageProgress = stageSummary && stageTotal > 0
    ? <> · stage {Math.min(stageTotal, stageSummary.completed + stageSummary.skipped + 1)} of {stageTotal}</>
    : null
  const headline: React.ReactNode = (() => {
    const t = VERDICT_THEME[verdict]
    if (verdict === 'NO_FAILURES') return <>Nothing to investigate</>
    if (verdict === 'NO_SOURCES')  return <span style={{ color: t.gateText }}>Cannot run — connect at least one evidence source</span>
    if (verdict === 'RUNNING')     return <><span style={{ color: t.gateText }}>Investigation running</span>{stageProgress}</>
    if (verdict === 'FAILED')      return <><span style={{ color: t.gateText }}>Last run failed</span> — retry from last successful stage</>
    if (verdict === 'PENDING')     return <>Run analysis to discover clusters</>
    return (
      <>
        {/* "across N suites" here counted the proposed clusters, not suites. */}
        <span style={{ color: t.gateText }}>{model.eligibleFailures} failure{model.eligibleFailures === 1 ? '' : 's'}</span>
        {' '}in {model.windowLabel}
        {' '}— {model.preScanClusters} cluster{model.preScanClusters === 1 ? '' : 's'} proposed
      </>
    )
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'NO_FAILURES')
      return <>No failures in {model.windowLabel}. Nothing to investigate.</>
    if (verdict === 'NO_SOURCES')
      return <>No evidence sources connected. Connect at least one of git history, application logs, or test results before running deep analysis.</>
    if (verdict === 'RUNNING')
      return <>The deep-analysis pipeline is processing. Model routing on the right fills in as each stage records the engine it used.</>
    if (verdict === 'FAILED')
      return <>The previous run halted before producing clusters. Retrying from the last successful stage skips the work that already completed.</>
    if (verdict === 'PENDING')
      return <>No clusters proposed yet for the focused run. Trigger a run to generate them, or pick a different recent run from the picker.</>
    const top = model.proposedClusters[0]
    if (!top) return null
    return (
      <>
        Pre-scan groups the {model.eligibleFailures} eligible failures into {model.preScanClusters} candidate clusters.
        {' '}The largest is <strong className="text-[var(--color-text)]">{top.name}</strong> ({top.failures} failures{top.confidence != null ? `, confidence ${top.confidence.toFixed(2)}` : ', unscored'}).
        {' '}Deep analysis will walk the stack, fetch commits + telemetry, and confirm or split these.
      </>
    )
  })()

  const onRunAll = async () => {
    if (!runId) return
    try {
      await deepInvestigationService.triggerDeep(runId, 'deep')
      toast.success(`Deep analysis queued for ${runId.slice(0, 8)}`)
    } catch {
      toast.error('Trigger failed')
    }
  }
  const onOpenPastRun = (rid: string) => navigate(`/deep-investigate/${rid}`)

  return (
    <PageShell>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Deep Investigation
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Semantic clustering &amp; multi-source root cause for</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            {suiteLabel && (
              <>
                <span aria-hidden>·</span>
                <span>Suite</span>
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{suiteLabel}</code>
              </>
            )}
            {model.focusedRun && (
              <>
                <span aria-hidden>·</span>
                {/* Header suite chip is clickable too — same target as
                    the past-investigations rows below. */}
                <SuiteBadge
                  primary={model.focusedRun.primary_suite_name}
                  all={model.focusedRun.suite_names}
                  linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
                />
              </>
            )}
            <span aria-hidden>·</span>
            <span>{model.eligibleFailures} failures ready</span>
            <span aria-hidden>·</span>
            <span>{model.preScanClusters} likely cluster{model.preScanClusters === 1 ? '' : 's'}</span>
            {model.lastRunAgeHours != null && (
              <>
                <span aria-hidden>·</span>
                <span>last analysis {model.lastRunAgeHours}h ago</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <SuiteFilterSelect
            value={selectedSuite}
            onChange={setSelectedSuite}
            options={suiteOptions}
            allLabel="All suites"
          />
          <PrimaryBtn
            large
            onClick={onRunAll}
            disabled={!isQaEngineer || verdict === 'NO_FAILURES' || verdict === 'NO_SOURCES' || !runId}
            title={isQaEngineer ? 'Trigger a deep investigation now' : 'QA Engineer role required'}
          >
            <Bot className="h-4 w-4" />
            Run Deep Analysis
          </PrimaryBtn>
        </div>
      </header>

      <VerdictCard
        model={model}
        verdict={verdict}
        headline={headline}
        lede={lede}
        onRunAll={onRunAll}
        isQaEngineer={isQaEngineer}
      />

      {/* AI-1 Investigator cockpit — hypothesis-loop agent (shadow mode).
          Project scope mirrors the spend/budget panels: the active project,
          falling back to the focused run's project in All-Projects mode. */}
      <InvestigatorCockpit runId={runId ?? null} projectId={budgetProjectId} />

      <InvestigationKpiStrip model={model} />

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          <ProposedClustersCard model={model} focusedRunId={runId ?? null} />
          <PastInvestigations rows={model.pastInvestigations} onOpen={onOpenPastRun} />
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <EvidenceSourcesCard
            sources={evidenceSources}
            isLoading={integrationsLoading}
            isError={integrationsError}
          />
          <ModelRoutingCard trail={decisionTrail} isLoading={!!runId && trailLoading} />
        </div>
      </div>

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>
    </PageShell>
  )
}

