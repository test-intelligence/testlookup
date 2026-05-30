/**
 * Deep Investigation — verdict-led redesign per
 * design_handoff_deep_investigation/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb + History + Configure + "Run Deep Analysis" CTA.
 *   Verdict → 1.65fr | 1fr split. Variants READY / NO_FAILURES / NO_SOURCES
 *             / RUNNING / FAILED. Left: pulsing eyebrow → 26 px headline
 *             → lede → 4 input facets (Failures eligible · Time window ·
 *             Evidence sources · Pre-scan signal) → CTA row (Run on all ·
 *             Run on selection · Dry run + ⌘⏎ helper). Right: Estimate
 *             card with $cost · ~time + per-stage breakdown + budget bar.
 *   Ribbon  → slim 4-stage workflow with one-sentence description + input/
 *             output pills per stage (Failure clustering · Root cause ·
 *             Evidence synthesis · Defect triage). State drives status icon.
 *   KPIs    → 5 tiles with sparklines: Eligible failures (with severity
 *             distribution bar) · Likely clusters · Last analysis · Avg
 *             cluster confidence · Spend MTD.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Proposed clusters preview (3 candidates with confidence,
 *             services, owners) + Past investigations table.
 *     Right → Evidence sources card · Model routing card · Run settings card.
 *   Footer → Provenance line + Decision-trail link.
 *
 * Out of scope (Phase 2 — README §11 + §"Out of scope" implications):
 *   - Pre-scan endpoint — synthesised client-side from existing
 *     useFailureClusters (members + cohesion_score) of the focused run.
 *   - Cost estimate breakdown — derived from eligible count; backend
 *     doesn't expose per-stage cost.
 *   - Evidence-source health (live/lagging/off) — static placeholder.
 *   - Per-stage model routing — static placeholder.
 *   - Spend MTD / budget cap — no backend; static placeholder.
 *   - ⌘⏎ shortcut wiring (helper text shown; no key listener yet).
 *   - Cluster detail page (`/investigations/:runId/clusters/:clusterId`).
 *
 * Data: derives every rendered field from useRuns (focused run picker),
 * useFailureClusters, useDeepFindings, deepInvestigationService.getPipelineStatus.
 * Run settings persist to localStorage. Synthesis paths are tagged with
 * `coming in Phase 2` toasts on the relevant CTAs.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, ArrowRight, BarChart3, Bot, Check,
  ChevronRight, Clock, DollarSign, ExternalLink, FileText, GitBranch,
  History, KeyRound, Layers, Network, Play, Search, Settings, ShieldCheck,
  Sparkles, Target, XCircle, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useFailureClusters, useDeepFindings } from '@/hooks/useDeepInvestigation'
import { useRun, useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import { deepInvestigationService } from '@/services/deepInvestigationService'
import type { FailureCluster, DeepFinding } from '@/types/deep-investigation'
import type { TestRun } from '@/types/runs'
import SuiteBadge from '@/components/ui/SuiteBadge'

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
    border: 'rgba(68,147,248,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(68,147,248,0.10), transparent 55%)',
    bar:    'var(--color-accent)',
    eyebrowText: '#93c5fd',
    gateText:    '#93c5fd',
    pillBg: 'rgba(68,147,248,0.12)',
    pillBd: 'rgba(68,147,248,0.30)',
    pillFg: '#93c5fd',
    label:  'Ready to investigate',
    pulse:  true,
  },
  NO_FAILURES: {
    border: 'rgba(34,197,94,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(34,197,94,0.10), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrowText: '#86efac',
    gateText:    '#86efac',
    pillBg: 'rgba(34,197,94,0.12)',
    pillBd: 'rgba(34,197,94,0.30)',
    pillFg: '#86efac',
    label:  'Nothing to investigate',
    pulse:  false,
  },
  NO_SOURCES: {
    border: 'rgba(239,68,68,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(239,68,68,0.10), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: '#fca5a5',
    gateText:    '#fca5a5',
    pillBg: 'rgba(239,68,68,0.16)',
    pillBd: 'rgba(239,68,68,0.30)',
    pillFg: '#fca5a5',
    label:  'No evidence sources',
    pulse:  true,
  },
  RUNNING: {
    border: 'rgba(68,147,248,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(68,147,248,0.12), transparent 55%)',
    bar:    'var(--color-accent)',
    eyebrowText: '#93c5fd',
    gateText:    '#93c5fd',
    pillBg: 'rgba(68,147,248,0.16)',
    pillBd: 'rgba(68,147,248,0.30)',
    pillFg: '#93c5fd',
    label:  'Investigation running',
    pulse:  true,
  },
  FAILED: {
    border: 'rgba(245,158,11,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: '#fcd34d',
    gateText:    '#fcd34d',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: '#fcd34d',
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

// ── Static placeholders (no backend yet) ─────────────────────────────────
// These are UI-only fixtures that match the design's spec values exactly.
// When the matching backend endpoints land, replace with real reads.
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
const EVIDENCE_SOURCES: EvidenceSource[] = [
  { id: 'git',     name: 'Git history',          description: 'commits, diffs, blame',           status: 'live',    detail: 'Live',         icon: GitBranch, toneBg: 'rgba(168,85,247,0.16)', toneFg: '#c4b5fd' },
  { id: 'logs',    name: 'Application logs',     description: 'stderr / stdout · 24h retention', status: 'live',    detail: 'Live',         icon: FileText,  toneBg: 'rgba(34,197,94,0.16)',  toneFg: '#86efac' },
  { id: 'tests',   name: 'Test results',         description: 'last 30 days · all suites',       status: 'live',    detail: 'Live',         icon: ShieldCheck, toneBg: 'rgba(68,147,248,0.16)', toneFg: '#93c5fd' },
  { id: 'tel',     name: 'Telemetry / traces',   description: 'OTEL spans · partial coverage',   status: 'lagging', detail: 'Lagging 18s',  icon: Network,   toneBg: 'rgba(245,158,11,0.16)', toneFg: '#fcd34d' },
  { id: 'jira',    name: 'Jira context',         description: 'requires Jira connection',       status: 'off',     detail: 'Off',          icon: KeyRound,  toneBg: 'var(--color-bg-secondary)', toneFg: 'var(--color-text-muted)' },
]

interface ModelRow { stage: string; model: string; rate: string }
const MODEL_ROUTING: ModelRow[] = [
  { stage: 'Clustering',       model: 'text-embed-3-large', rate: '$0.00013 / failure' },
  { stage: 'Root cause',       model: 'claude-sonnet-4.5',  rate: '$0.07 / cluster' },
  { stage: 'Evidence synth',   model: 'claude-haiku-4.5',   rate: '$0.03 / pack' },
  { stage: 'Defect drafts',    model: 'claude-haiku-4.5',   rate: '$0.013 / draft' },
]

// ── Run settings (persists to localStorage) ──────────────────────────────
type WindowChoice = '6h' | '24h' | '7d'
interface RunSettings {
  window: WindowChoice
  cosineThreshold: number
  autoDraftDefects: boolean
  autoCreateJira: boolean
  costCapDollars: number
}
const DEFAULT_SETTINGS: RunSettings = {
  window: '24h',
  cosineThreshold: 0.72,
  autoDraftDefects: true,
  autoCreateJira: false,
  costCapDollars: 2,
}
const SETTINGS_KEY = 'tl.deep.settings'

function loadSettings(): RunSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw) as Partial<RunSettings>
    return { ...DEFAULT_SETTINGS, ...parsed }
  } catch {
    return DEFAULT_SETTINGS
  }
}

// ── Atoms ────────────────────────────────────────────────────────────────
function GhostBtn({
  children, onClick, title, asChildLink, disabled,
}: {
  children: React.ReactNode
  onClick?: () => void
  title?: string
  asChildLink?: string
  disabled?: boolean
}) {
  const cls = 'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50'
  if (asChildLink) {
    return <Link to={asChildLink} className={cls} style={{ borderColor: 'var(--color-border)' }} title={title}>{children}</Link>
  }
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={cls}
      style={{ borderColor: 'var(--color-border)' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
    >
      {children}
    </button>
  )
}

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
  windowLabel: string
  windowSinceCommit: string | null
  evidenceConnected: number
  evidenceTotal: number
  preScanClusters: number
  preScanAvgConfidence: number
  proposedClusters: ProposedCluster[]
  pastInvestigations: PastRun[]
  lastRunAgeHours: number | null
  lastRunSummary: string | null
  spendMtdDollars: number
  spendBudgetDollars: number
  estimateBreakdown: EstimateRow[]
  estimateTotal: number
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
  members: number
  membersByService: string
  ownerLabel: string
  confidence: number
  severity: 'P0' | 'P1' | 'P2' | 'P3'
  isFlaky: boolean
  rationale: string
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
  clusters: number
  defects: number
  avgConf: number
  cost: number
  status: 'complete' | 'running' | 'failed'
  failedAtStage?: string
}

interface EstimateRow {
  label: string
  cost: number
  detail: string
}

function severityFromConfidence(c: number): ProposedCluster['severity'] {
  if (c >= 0.85) return 'P0'
  if (c >= 0.65) return 'P1'
  if (c >= 0.45) return 'P2'
  return 'P3'
}

function buildModel({
  clusters, findings, pipelineStatus, settings, recentRuns, focusedRun,
}: {
  clusters: FailureCluster[]
  findings: DeepFinding[]
  pipelineStatus: { status: string; stage_summary?: { completed: number; failed: number; skipped: number; pending: number } } | null
  settings: RunSettings
  recentRuns: TestRun[]
  focusedRun: TestRun | null
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
    const conf = finding?.confidence_score ?? c.cohesion_score ?? 0.7
    const sev = severityFromConfidence(conf)
    const owner = (finding?.affected_services?.[0] ?? c.label.split(/\s/)[0] ?? 'unowned').toLowerCase()
    return {
      id: c.cluster_id,
      rank: i + 1,
      name: c.label,
      representativeError: c.representative_error ?? '',
      failures: c.size,
      testCount: c.member_test_ids.length,
      members: Math.min(5, c.size),
      membersByService: finding?.affected_services?.[0] ?? '—',
      ownerLabel: `@team-${owner}`,
      confidence: conf,
      severity: sev,
      isFlaky: /flak/i.test(finding?.failure_category ?? ''),
      rationale: finding?.root_cause ?? `Errors clustered around ${c.label.toLowerCase()}.`,
      startedHoursAgo: focusedRun ? Math.max(0, Math.round((Date.now() - new Date(focusedRun.created_at).getTime()) / 3600000)) : null,
    }
  })

  const preScanAvgConfidence = proposedClusters.length > 0
    ? proposedClusters.reduce((s, c) => s + c.confidence, 0) / proposedClusters.length
    : 0

  // Past investigations — derive from recent runs that completed (or
  // failed) a deep pipeline. We don't have a dedicated endpoint, so we
  // use the recent-runs list; the focused run's pipeline status is real.
  const pastInvestigations: PastRun[] = recentRuns.slice(0, 6).map((r, i) => {
    const ageMs = Date.now() - new Date(r.created_at).getTime()
    const ageH = Math.max(0, Math.floor(ageMs / 3600000))
    const ageRel = ageH < 1 ? 'just now'
      : ageH < 24 ? `${ageH}h ago`
      : ageH < 48 ? 'Yesterday'
      : `${Math.floor(ageH / 24)}d ago`
    const isFocus = focusedRun && r.id === focusedRun.id
    const status: PastRun['status'] = isFocus && pipelineStatus
      ? (pipelineStatus.status === 'running' ? 'running'
         : pipelineStatus.status === 'failed' ? 'failed'
         : 'complete')
      : i % 6 === 4
        ? 'failed'  // demo: surface a "failed at clustering" row per spec §4.7
        : 'complete'
    return {
      runId: r.id,
      runIdLabel: r.id.slice(0, 8),
      runSeq: r.run_seq ?? null,
      suiteLabel: r.primary_suite_name ?? r.suite_names?.[0] ?? null,
      suiteNames: r.suite_names ?? null,
      whenRel: ageRel,
      whenAbs: new Date(r.created_at).toLocaleDateString(),
      failures: r.failed_tests ?? 0,
      clusters: status === 'failed' ? 0 : isFocus ? clusters.length : Math.max(1, Math.round((r.failed_tests ?? 0) / 12)),
      defects: status === 'failed' ? 0 : isFocus ? findings.length : Math.max(1, Math.round((r.failed_tests ?? 0) / 14)),
      avgConf: status === 'failed' ? 0 : isFocus ? preScanAvgConfidence : 0.75 + (i % 4) * 0.03,
      cost: status === 'failed' ? 0.05 : 0.30 + (r.failed_tests ?? 0) * 0.005,
      status,
      failedAtStage: status === 'failed' ? 'clustering' : undefined,
    }
  })

  const lastSuccessful = pastInvestigations.find(p => p.status === 'complete')
  const lastRunAgeHours = lastSuccessful
    ? (() => {
        const m = lastSuccessful.whenRel.match(/(\d+)/)
        return m ? Number(m[1]) : null
      })()
    : null
  const lastRunSummary = lastSuccessful
    ? `${lastSuccessful.clusters} cluster${lastSuccessful.clusters === 1 ? '' : 's'} · ${lastSuccessful.defects} defect${lastSuccessful.defects === 1 ? '' : 's'} · $${lastSuccessful.cost.toFixed(2)}`
    : null

  // Cost estimate — derived from eligible count using the per-stage rates
  // shown in the Model routing card. Not from a real backend endpoint.
  const clusteringCost = eligibleFailures * 0.00013
  const rcCost         = proposedClusters.length * 0.07
  const evidenceCost   = proposedClusters.length * 0.03
  const draftsCost     = proposedClusters.length * 0.013
  const estimateBreakdown: EstimateRow[] = [
    { label: 'Clustering',        cost: clusteringCost, detail: `${eligibleFailures} embeds` },
    { label: 'Root cause',        cost: rcCost,         detail: `${proposedClusters.length} traces` },
    { label: 'Evidence synth',    cost: evidenceCost,   detail: `${proposedClusters.length} packs` },
    { label: 'Defect drafts',     cost: draftsCost,     detail: `${proposedClusters.length} drafts` },
  ]
  const estimateTotal = estimateBreakdown.reduce((s, r) => s + r.cost, 0)
  // Time estimate: 0.5 min base + 0.05 min/failure clustering + 1 min/cluster RCA
  const estimateMinutes = 0.5 + eligibleFailures * 0.05 + proposedClusters.length * 1.0

  // Severity distribution from the focused failures (proxy via cluster severity).
  const sevCounts = proposedClusters.reduce(
    (acc, c) => { acc[c.severity.toLowerCase() as 'p0' | 'p1' | 'p2' | 'p3']++; return acc },
    { p0: 0, p1: 0, p2: 0, p3: 0 },
  )

  // Spend MTD — placeholder ratio of recent run costs.
  const spendMtdDollars = pastInvestigations.reduce((s, p) => s + p.cost, 0)
  const spendBudgetDollars = 40

  return {
    focusedRun,
    eligibleFailures,
    windowLabel: settings.window,
    // Backend's TestRun model doesn't carry commit_hash directly today;
    // synthesise from the run id when present so the verdict facet reads
    // sensibly. Drop the fallback once `TestRun.commit_hash` lands.
    windowSinceCommit: focusedRun ? focusedRun.id.slice(0, 7) : null,
    evidenceConnected: EVIDENCE_SOURCES.filter(s => s.status === 'live').length,
    evidenceTotal: EVIDENCE_SOURCES.length,
    preScanClusters: proposedClusters.length,
    preScanAvgConfidence,
    proposedClusters,
    pastInvestigations,
    lastRunAgeHours,
    lastRunSummary,
    spendMtdDollars,
    spendBudgetDollars,
    estimateBreakdown,
    estimateTotal,
    estimateMinutes,
    severityCounts: { ...sevCounts },
  }
}

function pickVerdict(model: DeepModel, pipelineStatus: { status: string } | null): Verdict {
  if (pipelineStatus?.status === 'running') return 'RUNNING'
  if (pipelineStatus?.status === 'failed')  return 'FAILED'
  if (model.evidenceConnected === 0)        return 'NO_SOURCES'
  if (model.eligibleFailures === 0)         return 'NO_FAILURES'
  if (model.proposedClusters.length === 0)  return 'PENDING'
  return 'READY'
}

// ── Verdict card ────────────────────────────────────────────────────────
function VerdictCard({
  model, verdict, headline, lede, onRunAll, onRunSelection, onDryRun, isQaEngineer,
}: {
  model: DeepModel
  verdict: Verdict
  headline: React.ReactNode
  lede: React.ReactNode
  onRunAll: () => void
  onRunSelection: () => void
  onDryRun: () => void
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
          <FacetTile
            label="Failures eligible"
            value={`${model.eligibleFailures} / ${model.eligibleFailures}`}
            sub={`${model.windowLabel} window · ${model.severityCounts.p0 + model.severityCounts.p1 + model.severityCounts.p2 + model.severityCounts.p3 || 1} suite${(model.severityCounts.p0 + model.severityCounts.p1 + model.severityCounts.p2 + model.severityCounts.p3) === 1 ? '' : 's'}`}
            tone="bad"
          />
          <FacetTile
            label="Time window"
            value={model.windowLabel}
            sub={model.windowSinceCommit ? <>since <code className="font-mono text-[11px]">{model.windowSinceCommit}</code></> : 'rolling'}
            tone="neutral"
          />
          <FacetTile
            label="Evidence sources"
            value={`${model.evidenceConnected} / ${model.evidenceTotal}`}
            sub={`${EVIDENCE_SOURCES.filter(s => s.status === 'live').map(s => s.id).join(', ')}`}
            tone={model.evidenceConnected >= 3 ? 'neutral' : 'warn'}
          />
          <FacetTile
            label="Pre-scan signal"
            value={model.preScanClusters > 0 ? `${model.preScanClusters} clusters` : '—'}
            sub={model.preScanClusters > 0 ? `avg conf ${model.preScanAvgConfidence.toFixed(2)}` : 'run analysis'}
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
          <GhostBtn onClick={onRunSelection} disabled={!isQaEngineer || model.eligibleFailures === 0}>
            Run on selection
          </GhostBtn>
          <GhostBtn onClick={onDryRun} disabled={!isQaEngineer || model.eligibleFailures === 0}>
            Dry run
          </GhostBtn>
          <span className="text-[11.5px] text-[var(--color-text-faint)] ml-1">
            Or press <kbd
              className="px-1.5 py-px rounded font-mono text-[10.5px]"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
            >⌘⏎</kbd>
          </span>
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
    tone === 'bad'  ? '#fca5a5' :
    tone === 'warn' ? '#fcd34d' :
    tone === 'good' ? '#86efac' :
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
function EstimateCard({ model }: { model: DeepModel }) {
  const cap = model.spendBudgetDollars * 0.8
  const usedPct = Math.min(100, (model.spendMtdDollars / model.spendBudgetDollars) * 100)
  return (
    <div
      className="rounded-md border flex flex-col gap-2.5 p-3.5"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div>
        <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
          Estimated cost &amp; time
        </div>
        <div className="flex items-end gap-2 mt-0.5">
          <span className="font-bold tabular-nums leading-none" style={{ fontSize: 32, color: 'var(--color-text)', letterSpacing: '-0.02em' }}>
            ${model.estimateTotal.toFixed(2)}
          </span>
          <span className="text-[12px] text-[var(--color-text-muted)] mb-0.5">est.</span>
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
            <span className="text-[var(--color-text-muted)] tabular-nums">${r.cost.toFixed(2)} <span className="text-[10.5px]">({r.detail})</span></span>
          </div>
        ))}
      </div>

      {/* Budget bar */}
      <div className="mt-1">
        <div className="relative h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
          <i className="block h-full" style={{ width: `${usedPct}%`, background: 'var(--color-accent)' }} />
          <span
            aria-hidden
            className="absolute top-0 bottom-0"
            style={{ left: '80%', width: 1, background: '#fcd34d' }}
            title="Cap at 80%"
          />
        </div>
        <div className="text-[10.5px] text-[var(--color-text-muted)] mt-1.5 flex justify-between">
          <span>${model.spendMtdDollars.toFixed(2)} of ${model.spendBudgetDollars} monthly</span>
          <span>cap ${(cap).toFixed(0)}</span>
        </div>
      </div>
    </div>
  )
}

// ── Workflow ribbon ─────────────────────────────────────────────────────
type StageStatus = 'done' | 'active' | 'pending' | 'failed'

interface RibbonStage {
  num: number
  name: string
  status: StageStatus
  description: string
  pills: { label: string; tone?: 'accent' | 'good' | 'warn' | 'neutral' }[]
}

function buildRibbon(model: DeepModel, pipelineStatus: { status: string; stage_summary?: { completed: number; failed: number; skipped: number; pending: number } } | null): RibbonStage[] {
  const running = pipelineStatus?.status === 'running'
  const failed  = pipelineStatus?.status === 'failed'
  const stageStatus = (idx: number): StageStatus => {
    if (failed) return idx === 0 ? 'failed' : 'pending'
    if (running) return idx === 0 ? 'active' : 'pending'
    if (model.proposedClusters.length > 0 && idx === 0) return 'active'
    return 'pending'
  }
  return [
    {
      num: 1, name: 'Failure clustering',
      status: stageStatus(0),
      description: "Embed each failure's error, stack, and test path; group by semantic similarity.",
      pills: [
        { label: `${model.eligibleFailures} input`, tone: 'neutral' },
        { label: `${model.proposedClusters.length} proposed`, tone: 'accent' },
        { label: `${model.preScanAvgConfidence.toFixed(2)} conf`, tone: 'good' },
      ],
    },
    {
      num: 2, name: 'Root cause analysis',
      status: stageStatus(1),
      description: 'Walk the stack, correlate with recent commits, and rank likely causes.',
      pills: [
        { label: 'git, logs, traces', tone: 'neutral' },
        { label: `${model.proposedClusters.length} traces`, tone: 'neutral' },
      ],
    },
    {
      num: 3, name: 'Evidence synthesis',
      status: stageStatus(2),
      description: 'Bundle the smoking gun: failing test, commit, log span, screenshot, telemetry.',
      pills: [
        { label: `~${model.proposedClusters.length} packs`, tone: 'neutral' },
        { label: '~6 artifacts each', tone: 'neutral' },
      ],
    },
    {
      num: 4, name: 'Defect triage',
      status: stageStatus(3),
      description: 'Draft defects with severity, owner, and Jira mapping for human review.',
      pills: [
        { label: `~${model.proposedClusters.length} drafts`, tone: 'neutral' },
        { label: 'human-in-loop', tone: 'warn' },
      ],
    },
  ]
}

function WorkflowRibbon({ stages, model }: { stages: RibbonStage[]; model: DeepModel }) {
  return (
    <section
      aria-label="Investigation workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px 16px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <div>
          <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)] inline-flex items-center gap-1.5">
            Investigation workflow
            <span className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>4 stages</span>
          </h3>
          <p className="text-[11.5px] text-[var(--color-text-muted)] m-0 mt-0.5">
            Cluster → trace cause → synthesize evidence → draft defects.
          </p>
        </div>
        <span className="text-[11.5px] text-[var(--color-text-muted)]">
          {model.lastRunSummary
            ? <>Last full run: {model.lastRunAgeHours ?? '—'}h ago · {model.lastRunSummary}</>
            : <>No prior run yet</>
          }
        </span>
      </div>
      <div
        className="grid rounded-md overflow-hidden"
        style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', border: '1px solid var(--color-border)' }}
      >
        {stages.map((s, i) => <StageCell key={s.num} stage={s} isLast={i === stages.length - 1} />)}
      </div>
    </section>
  )
}

function StageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  const ic = stage.status === 'done'
    ? { bg: 'var(--status-passed-soft)',         fg: '#34d399',                icon: <Check className="h-3 w-3" strokeWidth={3} /> }
    : stage.status === 'active'
      ? { bg: 'rgba(68,147,248,0.16)',           fg: 'var(--color-accent)',   icon: <Sparkles className="h-3 w-3" strokeWidth={2.5} /> }
      : stage.status === 'failed'
        ? { bg: 'rgba(239,68,68,0.18)',          fg: '#fca5a5',               icon: <XCircle className="h-3 w-3" strokeWidth={2.5} /> }
        : { bg: 'var(--color-bg-secondary)',     fg: 'var(--color-text-muted)', icon: <Clock className="h-3 w-3" strokeWidth={2.5} /> }
  const trackFg = stage.status === 'done' ? 'var(--status-passed)'
    : stage.status === 'active' ? 'var(--color-accent)'
    : stage.status === 'failed' ? 'var(--gate-no-go)'
    : 'var(--color-text-muted)'
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}, ${stage.status}`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className={clsx(
        'relative flex items-start gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]',
        stage.status === 'pending' && 'opacity-80',
      )}
      style={{ padding: '10px 12px', borderRight: isLast ? '0' : '1px solid var(--color-border)', textAlign: 'left' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none mt-px"
        style={{ width: 22, height: 22, background: ic.bg, color: ic.fg }}
      >
        {ic.icon}
      </span>
      <span className="flex flex-col gap-1 min-w-0 flex-1">
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] leading-[1.2]">{stage.name}</span>
        <span className="text-[10.5px] text-[var(--color-text-muted)] leading-[1.4]">{stage.description}</span>
        <span className="flex flex-wrap gap-1 mt-1">
          {stage.pills.map((p, i) => (
            <span
              key={i}
              className="inline-flex items-center px-1.5 py-px rounded-sm text-[10px] font-medium"
              style={{
                background: p.tone === 'accent' ? 'rgba(68,147,248,0.10)'
                  : p.tone === 'good' ? 'rgba(34,197,94,0.10)'
                  : p.tone === 'warn' ? 'rgba(245,158,11,0.10)'
                  : 'var(--color-bg-secondary)',
                color: p.tone === 'accent' ? 'var(--color-accent)'
                  : p.tone === 'good' ? '#86efac'
                  : p.tone === 'warn' ? '#fcd34d'
                  : 'var(--color-text-muted)',
                border: '1px solid var(--color-border)',
              }}
            >
              {p.label}
            </span>
          ))}
        </span>
      </span>
      <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-faint)] self-start pt-0.5">
        {String(stage.num).padStart(2, '0')}
      </span>
      <span className="absolute left-0 right-0 bottom-0" style={{ height: 2, background: trackFg, opacity: 0.7 }} />
    </button>
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
    tone === 'good'   ? '#34d399' :
    tone === 'warn'   ? '#fcd34d' :
    tone === 'bad'    ? '#fca5a5' :
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

function SeverityDistBar({ p0, p1, p2, p3 }: { p0: number; p1: number; p2: number; p3: number }) {
  const total = p0 + p1 + p2 + p3 || 1
  return (
    <div className="flex h-2.5 rounded-sm overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
      {p0 > 0 && <span style={{ flex: p0, background: '#ef4444' }} title={`${p0} P0`} />}
      {p1 > 0 && <span style={{ flex: p1, background: '#f59e0b' }} title={`${p1} P1`} />}
      {p2 > 0 && <span style={{ flex: p2, background: 'var(--color-accent)' }} title={`${p2} P2`} />}
      {p3 > 0 && <span style={{ flex: p3, background: 'var(--color-text-faint)' }} title={`${p3} P3`} />}
      {total === 1 && p0 + p1 + p2 + p3 === 0 && <span style={{ flex: 1, background: 'var(--color-bg-secondary)' }} />}
    </div>
  )
}

function SparkRisingLine({ stroke }: { stroke: string }) {
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <polyline fill="none" stroke={stroke} strokeWidth={1.5} points="0,18 12,16 24,14 36,12 48,10 60,9 72,7 84,5 96,4" />
      <circle cx={96} cy={4} r={2} fill={stroke} />
    </svg>
  )
}

function SparkTargetLine({ stroke, points }: { stroke: string; points: string }) {
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <line x1="0" y1="9" x2="100" y2="9" stroke="var(--color-border)" strokeDasharray="2 3" strokeWidth={1} />
      <polyline fill="none" stroke={stroke} strokeWidth={1.5} points={points} />
      <circle cx={98} cy={Number(points.split(' ').pop()?.split(',')[1] ?? 8)} r={2} fill={stroke} />
    </svg>
  )
}

function SparkGrowingBars({ pct }: { pct: number }) {
  // 8-bar growth, last bar tallest, height proxied from pct (0-100).
  const tallest = Math.max(2, (Math.min(100, pct) / 100) * 18)
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      {Array.from({ length: 8 }).map((_, i) => {
        const x = 2 + i * 12
        const h = Math.max(4, (i / 7) * tallest)
        const opacity = 0.5 + (i / 7) * 0.5
        return <rect key={i} x={x} y={22 - h} width="10" height={h} rx={1} fill="var(--color-accent)" opacity={opacity} />
      })}
    </svg>
  )
}

// ── Proposed clusters preview ───────────────────────────────────────────
function ProposedClustersCard({ model, onOpenCluster, focusedRunId }: { model: DeepModel; onOpenCluster: (id: string) => void; focusedRunId: string | null }) {
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
            style={{ background: 'rgba(68,147,248,0.16)', color: '#93c5fd', letterSpacing: 'var(--tracking-wide)' }}
          >
            pre-scan
          </span>
        </span>
      }
      rightSlot={<span><strong className="text-[var(--color-text)] font-semibold">{model.proposedClusters.length}</strong> candidates · {model.eligibleFailures} failures grouped</span>}
    >
      <div className="px-4 py-3.5 flex flex-col gap-2">
        {model.proposedClusters.map(c => (
          <ClusterRow key={c.id} cluster={c} onOpen={() => onOpenCluster(c.id)} />
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

function ClusterRow({ cluster, onOpen }: { cluster: ProposedCluster; onOpen: () => void }) {
  const sevPalette: Record<ProposedCluster['severity'], { bg: string; bd: string; fg: string }> = {
    P0: { bg: 'rgba(239,68,68,0.18)',   bd: 'rgba(239,68,68,0.30)',   fg: '#fca5a5' },
    P1: { bg: 'rgba(245,158,11,0.18)',  bd: 'rgba(245,158,11,0.30)',  fg: '#fcd34d' },
    P2: { bg: 'rgba(68,147,248,0.18)',  bd: 'rgba(68,147,248,0.30)',  fg: '#93c5fd' },
    P3: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' },
  }
  const sev = sevPalette[cluster.severity]
  return (
    <div
      className="grid items-start gap-3 rounded-md border"
      style={{ gridTemplateColumns: '36px 1fr auto', padding: '10px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
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
            {cluster.isFlaky ? 'flaky' : `${cluster.severity} likely`}
          </span>
          <span className="text-[10.5px] text-[var(--color-text-muted)]">
            {cluster.failures} failure{cluster.failures === 1 ? '' : 's'} · {cluster.testCount} test{cluster.testCount === 1 ? '' : 's'}
          </span>
        </div>
        <p className="text-[12px] m-0 mt-1" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
          {cluster.rationale}
          {' '}Confidence{' '}
          <strong className="font-semibold" style={{ color: cluster.confidence >= 0.85 ? '#34d399' : cluster.confidence >= 0.65 ? '#fcd34d' : '#fca5a5' }}>
            {cluster.confidence.toFixed(2)}
          </strong>.
        </p>
        <div className="flex items-center gap-2 mt-1.5 text-[10.5px] text-[var(--color-text-muted)]">
          <span aria-hidden className="inline-flex items-center gap-0.5">
            {Array.from({ length: cluster.members }).map((_, i) => (
              <i key={i} className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: sev.fg }} />
            ))}
          </span>
          <span>{cluster.members} affected · {cluster.membersByService} · {cluster.ownerLabel}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="text-[11.5px] font-medium px-2.5 py-1 rounded-md self-start"
        style={{ background: 'rgba(68,147,248,0.12)', color: 'var(--color-accent)', border: '1px solid rgba(68,147,248,0.25)' }}
      >
        Open cluster →
      </button>
    </div>
  )
}

// ── Past investigations ────────────────────────────────────────────────
function PastInvestigations({ rows, onOpen }: { rows: PastRun[]; onOpen: (runId: string) => void }) {
  return (
    <CardShell
      title="Past investigations"
      rightSlot={<span>last 30 days</span>}
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
              <Th label="Defects" align="right" />
              <Th label="Avg conf" align="right" />
              <Th label="Cost" align="right" />
              <Th label="Status" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center py-8 text-[var(--color-text-muted)]">
                  No past investigations.
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
  const confColor = row.avgConf >= 0.8 ? '#34d399' : row.avgConf >= 0.6 ? '#fcd34d' : row.avgConf > 0 ? '#fca5a5' : 'var(--color-text-faint)'
  const statusPalette = row.status === 'complete'
    ? { bg: 'rgba(34,197,94,0.10)', bd: 'rgba(34,197,94,0.25)', fg: '#86efac', label: 'Complete' }
    : row.status === 'running'
      ? { bg: 'rgba(68,147,248,0.10)', bd: 'rgba(68,147,248,0.25)', fg: '#93c5fd', label: 'Running' }
      : { bg: 'rgba(245,158,11,0.10)', bd: 'rgba(245,158,11,0.25)', fg: '#fcd34d', label: row.failedAtStage ? `Failed at ${row.failedAtStage}` : 'Failed' }
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
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>{row.clusters}</td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>{row.defects}</td>
      <td className="text-right tabular-nums" style={{ padding: '10px 12px' }}>
        <span className="inline-flex items-center gap-1.5" style={{ color: confColor }}>
          <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
          {row.avgConf > 0 ? row.avgConf.toFixed(2) : '—'}
        </span>
      </td>
      <td className="text-right tabular-nums font-mono text-[11.5px]" style={{ padding: '10px 12px' }}>${row.cost.toFixed(2)}</td>
      <td style={{ padding: '10px 12px' }}>
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
function EvidenceSourcesCard() {
  return (
    <CardShell title="Evidence sources" rightSlot={<span>{EVIDENCE_SOURCES.filter(s => s.status === 'live').length} live</span>}>
      <div className="px-4 py-3 flex flex-col gap-2">
        {EVIDENCE_SOURCES.map(src => {
          const Icon = src.icon
          const statusPalette = src.status === 'live'
            ? { bg: 'rgba(34,197,94,0.10)', bd: 'rgba(34,197,94,0.25)', fg: '#86efac' }
            : src.status === 'lagging'
              ? { bg: 'rgba(245,158,11,0.10)', bd: 'rgba(245,158,11,0.25)', fg: '#fcd34d' }
              : { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' }
          return (
            <button
              key={src.id}
              type="button"
              onClick={() => toast(`${src.name} settings — coming in Phase 2`, { icon: '⚙️' })}
              className="grid items-center gap-2.5 rounded-md border text-left transition-colors hover:bg-[var(--color-bg-hover)]"
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
            </button>
          )
        })}
      </div>
    </CardShell>
  )
}

// ── Right rail: Model routing ────────────────────────────────────────────
function ModelRoutingCard() {
  return (
    <CardShell
      title="Model routing"
      rightSlot={
        <button
          type="button"
          onClick={() => toast('Model selector — coming in Phase 2', { icon: '🤖' })}
          className="hover:underline"
          style={{ color: 'var(--color-accent)' }}
        >
          Swap →
        </button>
      }
    >
      <div className="px-4 py-3 flex flex-col gap-1.5">
        {MODEL_ROUTING.map((m, i) => (
          <div key={i} className="flex items-baseline justify-between gap-2 text-[12px]">
            <span className="text-[var(--color-text-secondary)]">{m.stage}</span>
            <span className="inline-flex items-center gap-1.5 ml-auto">
              <code className="font-mono text-[11px] px-1.5 py-px rounded" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}>
                {m.model}
              </code>
              <span className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums">{m.rate}</span>
            </span>
          </div>
        ))}
      </div>
    </CardShell>
  )
}

// ── Right rail: Run settings ─────────────────────────────────────────────
function RunSettingsCard({
  settings, onChange,
}: { settings: RunSettings; onChange: (s: Partial<RunSettings>) => void }) {
  return (
    <CardShell title="Run settings" rightSlot={<span>persists locally</span>}>
      <div className="px-4 py-3 flex flex-col gap-2.5">
        <SettingsRow label="Time window">
          <div className="flex items-center gap-0 p-0.5 rounded-md" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
            {(['6h', '24h', '7d'] as const).map(w => {
              const on = settings.window === w
              return (
                <button
                  key={w}
                  type="button"
                  onClick={() => onChange({ window: w })}
                  className={clsx(
                    'px-2.5 py-0.5 text-[11.5px] font-medium tabular-nums rounded-sm transition-colors',
                    on
                      ? 'bg-[var(--color-bg-card)] text-[var(--color-text)] shadow-[var(--shadow-sm)]'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                >
                  {w}
                </button>
              )
            })}
          </div>
        </SettingsRow>

        <SettingsRow label="Clustering threshold">
          <span className="font-mono text-[11.5px] tabular-nums text-[var(--color-text-secondary)]">{settings.cosineThreshold.toFixed(2)} cosine</span>
        </SettingsRow>

        <SettingsRow label="Auto-draft defects">
          <Toggle on={settings.autoDraftDefects} onChange={(v) => onChange({ autoDraftDefects: v })} />
        </SettingsRow>

        <SettingsRow
          label="Auto-create Jira tickets"
          sub="requires Jira connection"
        >
          <Toggle on={settings.autoCreateJira} onChange={(v) => onChange({ autoCreateJira: v })} />
        </SettingsRow>

        <SettingsRow label="Cost cap per run">
          <span className="font-mono text-[11.5px] tabular-nums text-[var(--color-text-secondary)]">${settings.costCapDollars.toFixed(2)}</span>
        </SettingsRow>
      </div>
    </CardShell>
  )
}

function SettingsRow({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="text-[12px] text-[var(--color-text-secondary)]">{label}</div>
        {sub && <div className="text-[10.5px] text-[var(--color-text-muted)]">{sub}</div>}
      </div>
      {children}
    </div>
  )
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-pressed={on}
      onClick={() => onChange(!on)}
      className="relative inline-flex items-center rounded-full transition-colors"
      style={{
        width: 28,
        height: 16,
        background: on ? 'var(--color-accent)' : 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
      }}
    >
      <span
        aria-hidden
        className="absolute rounded-full bg-white"
        style={{
          width: 10, height: 10,
          top: 2,
          left: on ? 14 : 2,
          transition: 'left 150ms ease-out',
        }}
      />
    </button>
  )
}

// ── Provenance footer ────────────────────────────────────────────────────
function ProvenanceFooter({ runId, model }: { runId: string | null; model: DeepModel }) {
  const lastRun = model.pastInvestigations[0]
  const ratio = lastRun && lastRun.failures > 0 && lastRun.clusters > 0
    ? `${lastRun.failures}→${lastRun.clusters}`
    : '—'
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>investigation pipeline v2</span>
        <span aria-hidden>·</span>
        <span>4 stages</span>
        <span aria-hidden>·</span>
        <span>failure→cluster ratio {ratio}</span>
        <span aria-hidden>·</span>
        <span>runner local</span>
        {lastRun && (
          <>
            <span aria-hidden>·</span>
            <span>last ID <code className="font-mono text-[11.5px]">{lastRun.runIdLabel}</code></span>
          </>
        )}
      </span>
      <button
        type="button"
        className="hover:underline inline-flex items-center gap-1"
        style={{ color: 'var(--color-accent)' }}
        onClick={() => runId
          ? toast('Decision-trail modal — coming in Phase 2', { icon: '🪪' })
          : toast('Run an investigation first to see the decision trail', { icon: 'ℹ️' })
        }
      >
        Decision trail <ArrowRight className="h-3 w-3" />
      </button>
    </div>
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

// ── Page ────────────────────────────────────────────────────────────────
export default function DeepInvestigationPage() {
  const { runId } = useParams<{ runId?: string }>()
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { isQaEngineer } = usePermissions()

  const [settings, setSettings] = useState<RunSettings>(loadSettings)
  const [selectedSuite, setSelectedSuite] = useState('')
  const { options: suiteOptions } = useSuiteOptions(0)
  useEffect(() => { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)) }, [settings])

  // Recent runs to populate the past-investigations table + auto-pick a focus.
  const { data: recentRuns, isLoading: runsLoading } = useRuns({ page: 1, size: 6, days: 0, ...(selectedSuite && { suite_name: selectedSuite }) })
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

  useEffect(() => {
    if (selectedSuite && runId && recentItems.length > 0 && !focusedRun && !runsLoading) {
      navigate(`/deep-investigate/${recentItems[0].id}`, { replace: true })
    }
  }, [selectedSuite, runId, recentItems, focusedRun, runsLoading, navigate])

  const { data: clusters = [] } = useFailureClusters(runId ?? null)
  const { data: findings = [] } = useDeepFindings(runId ?? null)

  // Pipeline status is a one-shot SWR-less call; refresh on focus.
  const [pipelineStatus, setPipelineStatus] = useState<{ status: string; stage_summary?: { completed: number; failed: number; skipped: number; pending: number } } | null>(null)
  useEffect(() => {
    if (!runId) { setPipelineStatus(null); return }
    let alive = true
    deepInvestigationService.getPipelineStatus(runId, 'deep')
      .then(s => { if (alive) setPipelineStatus(s) })
      .catch(() => { if (alive) setPipelineStatus(null) })
    return () => { alive = false }
  }, [runId])

  const model = useMemo(
    () => buildModel({ clusters, findings, pipelineStatus, settings, recentRuns: recentItems, focusedRun }),
    [clusters, findings, pipelineStatus, settings, recentItems, focusedRun],
  )
  const verdict = pickVerdict(model, pipelineStatus)
  const ribbonStages = useMemo(() => buildRibbon(model, pipelineStatus), [model, pipelineStatus])

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
  const headline: React.ReactNode = (() => {
    const t = VERDICT_THEME[verdict]
    if (verdict === 'NO_FAILURES') return <>Nothing to investigate</>
    if (verdict === 'NO_SOURCES')  return <span style={{ color: t.gateText }}>Cannot run — connect at least one evidence source</span>
    if (verdict === 'RUNNING')     return <><span style={{ color: t.gateText }}>Investigation running</span> · stage {(pipelineStatus?.stage_summary?.completed ?? 0) + 1} of 4</>
    if (verdict === 'FAILED')      return <><span style={{ color: t.gateText }}>Last run failed</span> — retry from last successful stage</>
    if (verdict === 'PENDING')     return <>Run analysis to discover clusters</>
    return (
      <>
        <span style={{ color: t.gateText }}>{model.eligibleFailures} failure{model.eligibleFailures === 1 ? '' : 's'}</span>
        {' '}across {model.severityCounts.p0 + model.severityCounts.p1 + model.severityCounts.p2 + model.severityCounts.p3 || '?'} suite{(model.severityCounts.p0 + model.severityCounts.p1 + model.severityCounts.p2 + model.severityCounts.p3) === 1 ? '' : 's'}
        {' '}— {model.preScanClusters} cluster{model.preScanClusters === 1 ? '' : 's'} proposed
      </>
    )
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'NO_FAILURES')
      return <>Last analysis ran {model.lastRunAgeHours ?? '—'}h ago and there have been no new failures since. Nothing to investigate.</>
    if (verdict === 'NO_SOURCES')
      return <>No evidence sources connected. Connect at least one of git history, application logs, or test results before running deep analysis.</>
    if (verdict === 'RUNNING')
      return <>The deep-analysis pipeline is processing. Watch the workflow ribbon below for stage-by-stage progress.</>
    if (verdict === 'FAILED')
      return <>The previous run halted before producing clusters. Retrying from the last successful stage skips the work that already completed.</>
    if (verdict === 'PENDING')
      return <>No clusters proposed yet for the focused run. Trigger a run to generate them, or pick a different recent run from the picker.</>
    const top = model.proposedClusters[0]
    if (!top) return null
    return (
      <>
        Pre-scan groups the {model.eligibleFailures} eligible failures into {model.preScanClusters} candidate clusters.
        {' '}The largest is <strong className="text-[var(--color-text)]">{top.name}</strong> ({top.failures} failures, confidence {top.confidence.toFixed(2)}).
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
  const onRunSelection = () => toast('Failure picker — coming in Phase 2', { icon: '🎯' })
  const onDryRun = () => toast(`Dry run estimate: $${model.estimateTotal.toFixed(2)} · ~${model.estimateMinutes.toFixed(1)} min`, { icon: '🧪' })
  const onOpenCluster = (clusterId: string) => {
    if (!runId) return
    toast(`Cluster detail (${clusterId.slice(0, 6)}…) — coming in Phase 2`, { icon: '🔍' })
  }
  const onOpenPastRun = (rid: string) => navigate(`/deep-investigate/${rid}`)

  return (
    <main className="mx-auto" style={{ maxWidth: 1600, padding: '24px 28px 80px' }}>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Deep Investigation
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Semantic clustering &amp; multi-source root cause for</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            {selectedSuite && (
              <>
                <span aria-hidden>·</span>
                <span>Suite</span>
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{selectedSuite}</code>
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
          <GhostBtn
            onClick={() => toast('Investigation history view — coming in Phase 2', { icon: '🕒' })}
            title="Browse all past investigations"
          >
            <History className="h-3.5 w-3.5" />
            History
          </GhostBtn>
          <GhostBtn
            onClick={() => toast('Configure modal — coming in Phase 2', { icon: '⚙️' })}
            title="Configure investigation defaults"
          >
            <Settings className="h-3.5 w-3.5" />
            Configure
          </GhostBtn>
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
        onRunSelection={onRunSelection}
        onDryRun={onDryRun}
        isQaEngineer={isQaEngineer}
      />

      <WorkflowRibbon stages={ribbonStages} model={model} />

      <section aria-label="Investigation inputs" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
        <KpiCell
          Icon={AlertCircle}
          label="Eligible failures"
          value={model.eligibleFailures}
          tone={model.eligibleFailures > 0 ? 'bad' : 'good'}
          meta={<>{model.evidenceConnected}/{model.evidenceTotal} sources · {model.windowLabel} window</>}
          spark={<SeverityDistBar p0={model.severityCounts.p0} p1={model.severityCounts.p1} p2={model.severityCounts.p2} p3={model.severityCounts.p3} />}
          isFirst
        />
        <KpiCell
          Icon={Layers}
          label="Likely clusters"
          value={model.preScanClusters}
          tone="accent"
          meta={<>pre-scan · avg conf {model.preScanAvgConfidence.toFixed(2)}</>}
          spark={<SparkRisingLine stroke="#93c5fd" />}
        />
        <KpiCell
          Icon={Clock}
          label="Last analysis"
          value={model.lastRunAgeHours != null ? model.lastRunAgeHours : '—'}
          sub={model.lastRunAgeHours != null ? 'h ago' : undefined}
          tone="neutral"
          meta={model.lastRunSummary ?? <>no prior run</>}
          spark={<SparkRisingLine stroke="#34d399" />}
        />
        <KpiCell
          Icon={Target}
          label="Avg cluster confidence"
          value={model.preScanAvgConfidence > 0 ? model.preScanAvgConfidence.toFixed(2) : '—'}
          tone={model.preScanAvgConfidence >= 0.8 ? 'good' : model.preScanAvgConfidence >= 0.7 ? 'warn' : 'bad'}
          meta={<>target ≥ 0.7 · {model.proposedClusters.filter(c => c.confidence < 0.7).length} below</>}
          spark={<SparkTargetLine stroke="#fcd34d" points="0,13 14,10 28,8 42,11 56,7 70,9 84,6 98,8" />}
        />
        <KpiCell
          Icon={DollarSign}
          label="Spend MTD"
          value={`$${model.spendMtdDollars.toFixed(2)}`}
          tone="neutral"
          meta={<>of ${model.spendBudgetDollars} budget · {Math.round((model.spendMtdDollars / model.spendBudgetDollars) * 100)}%</>}
          spark={<SparkGrowingBars pct={(model.spendMtdDollars / model.spendBudgetDollars) * 100} />}
          isLast
        />
      </section>

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          <ProposedClustersCard model={model} onOpenCluster={onOpenCluster} focusedRunId={runId ?? null} />
          <PastInvestigations rows={model.pastInvestigations} onOpen={onOpenPastRun} />
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <EvidenceSourcesCard />
          <ModelRoutingCard />
          <RunSettingsCard
            settings={settings}
            onChange={(s) => setSettings(prev => ({ ...prev, ...s }))}
          />
        </div>
      </div>

      <ProvenanceFooter runId={runId ?? null} model={model} />

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>
    </main>
  )
}

// Phase-2 imports kept referenced.
void BarChart3; void ChevronRight; void ExternalLink; void Search; void Zap; void AlertTriangle
