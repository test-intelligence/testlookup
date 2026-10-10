/**
 * Run Intelligence — the run's AI analysis.
 *
 * Since the UX redesign P4 the analysis lives on the Run page's **Analysis**
 * tab (`/runs/:runId?tab=analysis`; `/runs/:runId/intelligence` redirects
 * there). The body that tab hosts is exported from here as
 * `RunIntelligenceBody`, in the page template's order:
 *
 *   StatusBanner → the release verdict in one line (gate · action, risk,
 *                  blockers, AI confidence; "Decision trail" opens the drawer),
 *                  its reasoning and first blocking issue under it.
 *   What failed  → the PRIMARY content (`data-primary`): failure cards with
 *                  criticality pill, error block and Open / Decision trail /
 *                  File defect actions.
 *   (slot)       → the host's own section (the Run page puts Deep
 *                  Investigation's clusters here).
 *   Disclosures  → "How this score is computed" (composite-risk meter +
 *                  dimension grid) and "Evidence behind the verdict" (pipeline ribbon, test
 *                  outcome, recommended actions, AI confidence, failure
 *                  categories, step flips, provenance) — collapsed, so they
 *                  draw nothing until opened.
 *
 * `RunDecisionReport` (the verified decision report: claims, verification,
 * report versions) is exported for the Run page's **Evidence** tab.
 *
 * The page component below (no longer routed; kept so the body has a
 * standalone host and its tests a page) renders its own header, the decision
 * report and the body. Removed in P4 (`02-design-spec.md` §5 "Run" row):
 * the persona tabs and the placeholder category rows ("None detected this
 * run" for categories the data never named).
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Check, Copy as CopyIcon,
  RefreshCw,
  ShieldCheck, Stethoscope,
  TicketCheck, UserRound, Wrench, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import StatusBanner, { type BannerFact, type BannerState } from '@/components/ui/StatusBanner'
import Disclosure from '@/components/ui/Disclosure'
import AISuggestion from '@/components/ai/AISuggestion'
import DecisionTrailDrawer from '@/components/ai/DecisionTrailDrawer'
import DecisionIntelligencePanel from '@/components/ai/DecisionIntelligencePanel'
import { deriveDecisionTrustState } from '@/components/ai/decisionTrustState'
import DefectPromotionModal from '@/components/ai/DefectPromotionModal'
import RunStepFlipCard from '@/components/runs/RunStepFlipCard'
import { useDecisionReportVersions, useRunIntelligence } from '@/hooks/useRunIntelligence'
import { usePermissions } from '@/hooks/usePermissions'
import ReviewBanner from '@/components/reviews/ReviewBanner'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { onboardingService } from '@/services/onboardingService'
import type {
  DimensionScore,
  FailureClusterIntel,
  PipelineStage,
  ReleaseDecisionIntel,
  RunIntelligence,
} from '@/services/runIntelligenceService'
import { copyTextToClipboard } from '@/utils/clipboard'
import { computeRunOutcome } from '@/utils/runOutcome'
import GaugeBar from '@/components/charts/GaugeBar'
import { readReportVersion } from './run/runActions'

// ── Types ───────────────────────────────────────────────────────────────────
type Gate = 'GO' | 'CONDITIONAL_GO' | 'NO_GO' | 'PENDING'

// ── Verdict theming ─────────────────────────────────────────────────────────
const GATE_THEME: Record<Gate, {
  /** The banner's state (its pill word and hue). */
  banner: BannerState
  gate: string
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
  action: string
}> = {
  GO: {
    banner: 'go',
    gate:   'var(--status-passed)',
    pillBg: 'color-mix(in srgb, var(--status-passed) 15%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    label:  'Go',
    action: 'ship cleared',
  },
  CONDITIONAL_GO: {
    banner: 'conditional',
    gate:   'var(--status-broken)',
    pillBg: 'color-mix(in srgb, var(--status-broken) 15%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Conditional Go',
    action: 'proceed with mitigation',
  },
  NO_GO: {
    banner: 'no_go',
    gate:   'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 15%, transparent)',
    pillBd: 'var(--alert-border-soft)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'No-Go',
    action: 'ship blocked',
  },
  PENDING: {
    banner: 'pending',
    gate:   'var(--color-text-secondary)',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    label:  'Pending',
    action: 'awaiting evidence',
  },
}

function gateOf(decision: ReleaseDecisionIntel | null | undefined): Gate {
  if (!decision?.recommendation) return 'PENDING'
  return decision.recommendation
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt || !completedAt) return '—'
  const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

function totalElapsed(stages: PipelineStage[]): string {
  if (stages.length === 0) return '—'
  const starts = stages.map(s => s.started_at).filter(Boolean) as string[]
  const ends = stages.map(s => s.completed_at).filter(Boolean) as string[]
  if (starts.length === 0 || ends.length === 0) return '—'
  const start = Math.min(...starts.map(s => new Date(s).getTime()))
  const end = Math.max(...ends.map(s => new Date(s).getTime()))
  return formatDuration(new Date(start).toISOString(), new Date(end).toISOString())
}

type StageDisplayStatus = 'done' | 'skipped' | 'warn' | 'failed' | 'running' | 'pending'
function stageStatus(s: PipelineStage): StageDisplayStatus {
  const x = (s.status || '').toLowerCase()
  if (x === 'completed' || x === 'success' || x === 'done')   return 'done'
  if (x === 'skipped')                                         return 'skipped'
  if (x === 'failed' || x === 'error')                         return 'failed'
  if (x === 'running' || x === 'in_progress')                  return 'running'
  if (x === 'warning' || s.fallback_used)                      return 'warn'
  return 'pending'
}

const STAGE_DISPLAY_NAME: Record<string, string> = {
  ingestion:          'Ingestion',
  anomaly:            'Anomaly detection',
  rca:                'Root cause analysis',
  failure_clustering: 'Failure clustering',
  cluster:            'Failure clustering',
  defect_triage:      'Defect triage',
  triage:             'Defect triage',
  flaky_sentinel:     'Flaky sentinel',
  flaky:              'Flaky sentinel',
  test_health:        'Test health',
  health:             'Test health',
  summary:            'Summary',
  release_risk:       'Release risk',
  release:            'Release risk',
  decision_report:    'Decision report',
  decision_report_critic: 'Report critic',
}

const PIPELINE_ORDER = [
  'ingestion', 'anomaly', 'rca', 'failure_clustering',
  'defect_triage', 'flaky_sentinel', 'test_health', 'summary', 'release_risk',
  'decision_report', 'decision_report_critic',
]

function alignStages(raw: PipelineStage[]): (PipelineStage | null)[] {
  // Project the raw stages onto the canonical 9-position pipeline. Keeps
  // visual stability across runs even when some stages didn't execute.
  const byKey = new Map<string, PipelineStage>()
  for (const s of raw) {
    const k = s.stage_name.toLowerCase()
    byKey.set(k, s)
    // alias the second key in pairs (cluster/failure_clustering, triage/defect_triage, …)
  }
  return PIPELINE_ORDER.map((slot) => byKey.get(slot) ?? byKey.get(slot.split('_')[0]) ?? null)
}

function GhostBtn({
  children, onClick, title, disabled,
}: { children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50"
      style={{ borderColor: 'var(--color-border)' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
    >
      {children}
    </button>
  )
}

// ── Score breakdown (the "How this score is computed" disclosure) ──────────
// The composite-risk meter and the dimension grid used to sit in a verdict
// card above everything; the verdict itself is now the StatusBanner, and the
// score's working moves below the primary content (P3 template, step 3).

function RiskMeter({
  gate, score, pillBg, pillBd, pillFg, pillLabel, meterColor,
}: { gate: Gate; score: number | null; pillBg: string; pillBd: string; pillFg: string; pillLabel: string; meterColor: string }) {
  const clamped = score === null ? 0 : Math.max(0, Math.min(100, score))
  return (
    <div>
      <div className="flex items-end justify-between">
        <div>
          <div
            className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]"
            style={{ letterSpacing: 'var(--tracking-wider)', marginBottom: 6 }}
          >
            Composite risk score
          </div>
          <span className="font-bold tabular-nums leading-none" style={{ fontSize: 44, color: meterColor, letterSpacing: '-0.02em' }}>
            {gate === 'PENDING' || score === null ? '—' : clamped}
          </span>
          <span className="text-[13px] text-[var(--color-text-muted)] ml-1">/ 100</span>
        </div>
        <span
          className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold"
          style={{ background: pillBg, border: `1px solid ${pillBd}`, color: pillFg }}
        >
          {pillLabel}
        </span>
      </div>
      {/* The header above stays page markup (tests read its "—"); only the
          bar and its scale are the kit's. No score, or a PENDING gate, is
          not measured: an empty track, never a bar at 0. Risk runs
          green-to-red, so the fill ends in the colour of its own band. */}
      <GaugeBar
        className="mt-3"
        value={gate === 'PENDING' || score === null ? null : score}
        label="Composite risk score"
        valueText={`${clamped} of 100, ${pillLabel}`}
        gradient="risk"
        ticks={RISK_TICKS}
      />
    </div>
  )
}

const RISK_TICKS = [
  { value: 0, label: 'Safe' },
  { value: 30, label: 'Conditional' },
  { value: 70, label: 'Block' },
  { value: 100 },
]

function DimensionGrid({ scores, fallback }: { scores: DimensionScore[]; fallback: null }) {
  // Use up to 4 dimensions; pad with neutral placeholders so the grid stays
  // visually balanced even when the backend hasn't scored every dimension.
  const top = (scores ?? []).slice(0, 4)
  const padded: (DimensionScore | null)[] = [...top]
  while (padded.length < 4) padded.push(fallback)

  // If we have nothing to show at all, render a single-cell empty hint.
  if (top.length === 0) {
    return (
      <div
        className="rounded-md border text-[12px] text-[var(--color-text-muted)] px-3 py-3 text-center"
        style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
      >
        Dimension scores will appear once the release-risk stage completes.
      </div>
    )
  }

  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
      {padded.map((d, i) => <DimensionTile key={i} score={d} />)}
    </div>
  )
}

function DimensionTile({ score }: { score: DimensionScore | null }) {
  if (!score) {
    return (
      <div
        className="rounded-sm px-2.5 py-2 border opacity-50"
        style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
      >
        <div
          className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex justify-between"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          <span>—</span>
        </div>
      </div>
    )
  }
  const pct = Math.round(score.score)
  const tone: 'good' | 'warn' | 'bad' | 'neutral' =
    pct >= 70 ? 'bad' : pct >= 40 ? 'warn' : pct > 0 ? 'good' : 'neutral'
  const valueColor =
    tone === 'bad' ? 'var(--status-failed)' :
    tone === 'warn' ? 'var(--status-broken)' :
    tone === 'good' ? 'var(--status-passed)' : 'var(--color-text-secondary)'
  const barColor =
    tone === 'bad' ? 'var(--status-failed)' :
    tone === 'warn' ? 'var(--status-broken)' :
    tone === 'good' ? 'var(--status-passed)' : 'var(--color-text-faint)'
  return (
    <div
      className="rounded-sm px-2.5 py-2 border"
      style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex justify-between"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        <span>{score.label}</span>
        <span className="text-[var(--color-text-faint)] font-medium">{Math.round(score.weight * 100)}%</span>
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <span className="text-[14px] font-semibold tabular-nums min-w-[36px]" style={{ color: valueColor }}>
          {pct === 0 ? '—' : pct}
        </span>
        <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
          <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: barColor }} />
        </div>
      </div>
    </div>
  )
}

// ── Pipeline ribbon ─────────────────────────────────────────────────────────
function PipelineRibbon({
  stages,
  confidencePct,
  evidenceCount,
  toolCount,
  hasPerTestGap,
}: {
  stages: PipelineStage[]
  confidencePct: number
  evidenceCount: number
  toolCount: number
  /** When the run-level aggregate reports failures but per-test rows
   *  weren't persisted, every pipeline stage renders as a placeholder
   *  (—). Render a banner so the user understands why. */
  hasPerTestGap?: boolean
}) {
  const aligned = useMemo(() => alignStages(stages), [stages])
  const completedCount = aligned.filter(s => s && stageStatus(s) === 'done').length
  const skippedCount = aligned.filter(s => s && stageStatus(s) === 'skipped').length
  const allDone = aligned.every(s => !s || ['done', 'skipped', 'failed'].includes(stageStatus(s)))
  const total = totalElapsed(stages)

  const confTone: 'good' | 'warn' | 'bad' | 'neutral' =
    confidencePct >= 70 ? 'good' : confidencePct >= 40 ? 'warn' : confidencePct > 0 ? 'bad' : 'neutral'
  const confBg =
    confTone === 'good' ? 'color-mix(in srgb, var(--status-passed) 12%, transparent)' :
    confTone === 'warn' ? 'color-mix(in srgb, var(--status-broken) 12%, transparent)' :
    confTone === 'bad'  ? 'color-mix(in srgb, var(--status-failed) 12%, transparent)' :
    'var(--color-bg-secondary)'
  const confFg =
    confTone === 'good' ? 'var(--status-passed)' :
    confTone === 'warn' ? 'var(--status-broken)' :
    confTone === 'bad'  ? 'var(--status-failed)' : 'var(--color-text-muted)'
  const confBd =
    confTone === 'good' ? 'color-mix(in srgb, var(--status-passed) 25%, transparent)' :
    confTone === 'warn' ? 'color-mix(in srgb, var(--status-broken) 25%, transparent)' :
    confTone === 'bad'  ? 'color-mix(in srgb, var(--status-failed) 25%, transparent)' :
    'var(--color-border)'

  return (
    <section
      className="card"
      style={{ padding: '16px 18px 18px', marginBottom: 14, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)' }}
    >
      <div className="flex items-start justify-between gap-2.5 mb-3.5 flex-wrap">
        <div>
          <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">AI pipeline · Deep Analysis</h3>
          <div className="text-[12px] text-[var(--color-text-muted)] mt-0.5">
            <strong style={{ color: 'var(--status-passed)', fontWeight: 500 }}>● {allDone ? 'Completed' : 'Running'}</strong>
            {' · '}
            {aligned.length} stages
            {' · '}{completedCount} done
            {skippedCount > 0 && <> · {skippedCount} skipped</>}
            {' · '}{total} total
          </div>
        </div>
        <div className="flex items-center gap-2.5 text-[12px] text-[var(--color-text-muted)]">
          <span
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold"
            style={{ background: confBg, color: confFg, border: `1px solid ${confBd}` }}
            title="AI confidence"
          >
            Confidence {confidencePct}%
          </span>
          <span>· {evidenceCount} evidence item{evidenceCount === 1 ? '' : 's'} · {toolCount} tool{toolCount === 1 ? '' : 's'}</span>
        </div>
      </div>

      {hasPerTestGap && completedCount === 0 && (
        <div
          className="rounded-md mb-3 px-3 py-2 text-[12px] leading-relaxed"
          style={{
            background: 'color-mix(in srgb, var(--status-broken) 6%, transparent)',
            borderLeft: '3px solid color-mix(in srgb, var(--status-broken) 40%, transparent)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <strong className="text-[var(--color-text)]">Per-test detail is being recovered.</strong>
          {' '}
          The run reports failure counters but per-test rows weren't persisted during ingestion (live-stream
          buffer was evicted before persist). A retroactive backfill task synthesises placeholder rows for
          affected runs on an hourly cadence — once it fires, this run's failures will appear on{' '}
          <a href="/my-failures" className="text-[var(--color-accent)] hover:underline">/my-failures</a>{' '}
          and below. Aggregate counters stay accurate in the meantime; re-running the build will produce a
          fresh run with full per-test detail.
        </div>
      )}
      <div className="grid overflow-x-auto" style={{ gridTemplateColumns: `repeat(${aligned.length}, minmax(100px, 1fr))` }}>
        {aligned.map((s, i) => (
          <StageCell key={i} num={i + 1} slot={PIPELINE_ORDER[i]} stage={s} isLast={i === aligned.length - 1} />
        ))}
      </div>
    </section>
  )
}

function StageCell({ num, slot, stage, isLast }: { num: number; slot: string; stage: PipelineStage | null; isLast: boolean }) {
  const status: StageDisplayStatus = stage ? stageStatus(stage) : 'pending'
  const dur = stage ? formatDuration(stage.started_at, stage.completed_at) : '—'
  const name = stage?.stage_name ? (STAGE_DISPLAY_NAME[stage.stage_name.toLowerCase()] ?? stage.stage_name) : (STAGE_DISPLAY_NAME[slot] ?? slot)

  const ic = (() => {
    if (status === 'done')    return { bg: 'var(--status-passed-soft)', fg: 'var(--status-passed)', glyph: <Check className="h-[9px] w-[9px]" strokeWidth={3} /> }
    if (status === 'warn')    return { bg: 'color-mix(in srgb, var(--status-broken) 18%, transparent)', fg: 'var(--status-broken)', glyph: <AlertTriangle className="h-[9px] w-[9px]" strokeWidth={2.5} /> }
    if (status === 'failed')  return { bg: 'color-mix(in srgb, var(--status-failed) 18%, transparent)', fg: 'var(--status-failed)', glyph: <XCircle className="h-[9px] w-[9px]" strokeWidth={2.5} /> }
    if (status === 'running') return { bg: 'color-mix(in srgb, var(--color-accent) 18%, transparent)', fg: 'var(--color-accent)', glyph: <RefreshCw className="h-[9px] w-[9px] animate-spin" /> }
    if (status === 'skipped') return { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-faint)', glyph: <span className="text-[10px] leading-none">—</span> }
    return { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-faint)', glyph: <span className="text-[10px] leading-none">·</span> }
  })()

  const trackFill = status === 'done' ? 'var(--status-passed)'
    : status === 'warn' ? 'var(--gate-conditional)'
    : status === 'failed' ? 'var(--gate-no-go)'
    : status === 'running' ? 'var(--color-accent)'
    : status === 'skipped' ? 'transparent'
    : 'var(--color-border)'

  return (
    <div
      role="group"
      aria-label={`Stage ${num}: ${name}, ${status}`}
      data-testid="pipeline-stage"
      title={status === 'skipped' ? (stage?.skipped_reason || 'Skipped') : `${name} · ${dur}`}
      className={clsx(
        'relative text-left',
        status === 'skipped' && 'opacity-55',
      )}
      style={{
        padding: '10px 8px',
        borderRight: isLast ? '0' : '1px solid var(--color-border)',
      }}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-[10px] font-semibold tabular-nums text-[var(--color-text-faint)]">
          {String(num).padStart(2, '0')}
        </span>
        <span
          className="inline-flex items-center justify-center rounded-full"
          style={{ width: 14, height: 14, background: ic.bg, color: ic.fg, border: status === 'skipped' ? '1px dashed var(--color-border-light)' : undefined }}
        >
          {ic.glyph}
        </span>
      </div>
      <div className={clsx('text-[12px] font-semibold leading-[1.25]', status === 'skipped' ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text)]')}>
        {name}
        {status === 'skipped' && (
          <span
            className="block text-[9.5px] uppercase font-medium mt-0.5 text-[var(--color-text-faint)]"
            style={{ letterSpacing: '.06em' }}
          >
            Skipped
          </span>
        )}
      </div>
      <div className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums mt-1">{dur}</div>

      {/* progress track */}
      <div className="absolute left-0 right-0 bottom-0 h-0.5" style={{ background: 'var(--color-border)' }}>
        <i className="block h-full" style={{ width: status === 'skipped' ? '0%' : '100%', background: trackFill }} />
      </div>
    </div>
  )
}

// ── Body: Test outcome ─────────────────────────────────────────────────────
function CardShell({
  title,
  rightSlot,
  children,
}: {
  title: string
  rightSlot?: React.ReactNode
  children?: React.ReactNode
}) {
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

function TestOutcomeCard({
  run,
  failureClusters,
  affectedSuites,
  categoryBreakdown,
  clusteringRan,
}: {
  run: RunIntelligence['run']
  failureClusters: FailureClusterIntel[]
  affectedSuites: Array<{ suite: string; failed_count: number }>
  categoryBreakdown: Record<string, number>
  /**
   * Whether failure clustering actually executed for this run.
   *
   * `failure_clusters: []` means two different things and the reader cannot tell
   * them apart: "clustering ran and grouped nothing" or "clustering never ran".
   * Only the `deep` workflow contains the `failure_clustering` stage
   * (`DEEP_REQUIRED_STAGES`), so an `offline` pipeline always reports an empty
   * list. Showing that as `0 / no clusters` states a measurement that was never
   * taken -- on a run with real failures.
   */
  clusteringRan: boolean
}) {
  const { passed, broken, skipped, failed, total, evaluated, passRate } =
    computeRunOutcome(run)
  const dominantCategory = Object.entries(categoryBreakdown).sort((a, b) => b[1] - a[1])[0]?.[0]?.replace(/_/g, ' ').toLowerCase() ?? '—'
  const topSuite = affectedSuites[0]?.suite

  return (
    <CardShell
      title="Test outcome"
      rightSlot={topSuite ? (
        <>
          <span>Affected suite</span>
          <SuiteChip suite={topSuite} />
        </>
      ) : <span>—</span>}
    >
      <div className="grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}>
        <Stat tone={passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad'} label="Pass rate" value={`${passRate.toFixed(1)}`} unit="%" tiny={`${passed} / ${evaluated} evaluated`} borderRight />
        <Stat label="Total tests" value={`${total}`} tiny={`across ${affectedSuites.length || 1} suite${affectedSuites.length === 1 ? '' : 's'}`} borderRight />
        <Stat tone={failed > 0 ? 'bad' : undefined} label="Failed" value={`${failed}`} tiny={broken > 0 ? `incl. ${broken} broken` : failed > 0 ? dominantCategory : '—'} borderRight />
        <Stat label="Skipped" value={`${skipped}`} tiny={skipped > 0 ? 'in run' : '—'} borderRight />
        <Stat
          label="Clusters"
          value={clusteringRan ? `${failureClusters.length}` : '—'}
          tiny={
            !clusteringRan
              ? 'not run — deep investigation only'
              : failureClusters.length > 0
                ? `${failureClusters.length} cluster${failureClusters.length === 1 ? '' : 's'}`
                : 'no clusters found'
          }
        />
      </div>

      <div className="px-4 pb-3 pt-2">
        <div
          className="flex items-baseline justify-between text-[11px] uppercase text-[var(--color-text-muted)] mb-1.5"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          <span>Result distribution</span>
          <span className="text-[var(--color-text-secondary)] font-medium">{passed} passed · {failed} failed{skipped > 0 ? ` · ${skipped} skipped` : ''}</span>
        </div>
        <div className="h-2 rounded-full overflow-hidden flex" style={{ background: 'var(--color-bg-secondary)' }}>
          {passed > 0 && <div style={{ flex: passed, background: 'var(--status-passed)' }} />}
          {failed > 0 && <div style={{ flex: failed, background: 'var(--status-failed)' }} />}
          {skipped > 0 && <div style={{ flex: skipped, background: 'var(--status-broken)' }} />}
        </div>
        <div className="flex gap-3.5 mt-1.5 text-[11px] text-[var(--color-text-muted)]">
          <Legend color="var(--status-passed)" label="Passed" />
          {failed > 0 && <Legend color="var(--status-failed)" label={`Failed · ${dominantCategory}`} />}
          {skipped > 0 && <Legend color="var(--status-broken)" label="Skipped" />}
        </div>
      </div>
    </CardShell>
  )
}

function Stat({
  label, value, unit, tiny, tone, borderRight,
}: { label: string; value: string; unit?: string; tiny?: string; tone?: 'warn' | 'bad' | 'good'; borderRight?: boolean }) {
  const numColor = tone === 'bad' ? 'var(--status-failed)' : tone === 'warn' ? 'var(--status-broken)' : tone === 'good' ? 'var(--status-passed)' : 'var(--color-text)'
  return (
    <div className="px-4 py-3.5 flex flex-col gap-1" style={{ borderRight: borderRight ? '1px solid var(--color-border)' : '0' }}>
      <span
        className="text-[10.5px] uppercase text-[var(--color-text-muted)]"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        {label}
      </span>
      <span className="text-[24px] font-bold tabular-nums leading-none" style={{ color: numColor }}>
        {value}
        {unit && <small className="text-[12px] font-medium text-[var(--color-text-muted)] ml-0.5">{unit}</small>}
      </span>
      {tiny && <span className="text-[10.5px] text-[var(--color-text-muted)]">{tiny}</span>}
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

function SuiteChip({ suite }: { suite: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-mono text-[10.5px]"
      style={{
        background: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
        color: 'var(--color-accent)',
        border: '1px solid color-mix(in srgb, var(--color-accent) 25%, transparent)',
      }}
    >
      {suite}
    </span>
  )
}

// ── Body: What failed ──────────────────────────────────────────────────────
const MAX_FAILURE_BLOCKS = 3

function WhatFailedCard({
  clusters,
  runId,
  onPromote,
  onDecisionTrail,
  aggregateFailedTests,
  aggregateTotalTests,
  failedTestRows,
}: {
  clusters: FailureClusterIntel[]
  runId: string
  /** Absent below QA engineer: filing a defect needs that role. */
  onPromote?: (cluster: FailureClusterIntel) => void
  onDecisionTrail: () => void
  /** ``test_runs.failed_tests`` aggregate (set by ingest even when per-test
   *  rows are missing). When > 0 with zero clusters, surface the data gap. */
  aggregateFailedTests: number
  /** ``test_runs.total_tests`` aggregate — used to detect runs that
   *  legitimately had zero tests at all (vs runs that lost per-test detail). */
  aggregateTotalTests: number
  /** Failed or broken per-test rows the run actually has (``affected_suites``
   *  is counted from them). */
  failedTestRows: number
}) {
  if (clusters.length === 0 && aggregateFailedTests > 0 && failedTestRows > 0) {
    // The rows are there; only the analysis is not. It said "per-test detail
    // missing ... the per-test rows aren't available" on every run with
    // failures and no clusters, which is every run not yet analysed: a run
    // with all 22 rows on its Tests tab (browser E2E pass, 2026-10-08).
    return (
      <CardShell
        title="What failed"
        rightSlot={<span>{aggregateFailedTests} failure{aggregateFailedTests === 1 ? '' : 's'} · not analysed yet</span>}
      >
        <div className="px-4 py-4 text-[13px] space-y-2">
          <p className="m-0 text-[var(--color-text-secondary)]">
            <strong className="text-[var(--color-text)]">{aggregateFailedTests} failure{aggregateFailedTests === 1 ? '' : 's'}</strong>
            {aggregateTotalTests > 0 ? <> out of {aggregateTotalTests} tests</> : null}, not clustered yet:
            no analysis has run for this run. Analyze failures (below) groups them and looks for the root cause.
          </p>
          <Link to={`/runs/${runId}?tab=tests&status=FAILED`} className="text-[12px] text-[var(--color-accent)] hover:underline">
            Open the failed tests →
          </Link>
        </div>
      </CardShell>
    )
  }
  if (clusters.length === 0) {
    // Distinguish two zero-cluster states:
    //   (a) The run genuinely had no failures (total_tests > 0, failed = 0)
    //       or no tests at all (total = 0). Render the standard empty state.
    //   (b) The run-level aggregate reports failures (failed > 0) but no
    //       FailureCluster rows / per-test rows exist. This happens when the
    //       live-stream Redis event buffer expired before persist_live_session
    //       could read the per-test events (see worker/tasks.py:213-231 —
    //       "Live persist: event buffer empty"). The run aggregates ARE
    //       reliable; the per-test detail just wasn't captured. Explain so
    //       the user doesn't think the page is broken (2026-05-15 report).
    const hasPerTestGap = aggregateFailedTests > 0
    if (hasPerTestGap) {
      return (
        <CardShell
          title="What failed"
          rightSlot={
            <span>{aggregateFailedTests} failure{aggregateFailedTests === 1 ? '' : 's'} · per-test detail missing</span>
          }
        >
          <div className="px-4 py-4 text-[13px] space-y-2">
            <p className="m-0 text-[var(--color-text-secondary)]">
              The run aggregate reports <strong className="text-[var(--color-text)]">{aggregateFailedTests} failure{aggregateFailedTests === 1 ? '' : 's'}</strong>
              {aggregateTotalTests > 0 ? <> out of {aggregateTotalTests} tests</> : null}, but the
              per-test rows aren't available for this run — so AI clustering, root-cause analysis,
              and the failure breakdown can't be shown.
            </p>
            <p className="m-0 text-[12px] text-[var(--color-text-muted)]">
              This typically happens when a live-stream ingest's Redis event buffer expired
              before the per-test persistence task ran. The run-level pass / fail counts
              shown elsewhere are still accurate; just the per-test detail is missing.
              Re-running the build will produce a fresh run with complete data.
            </p>
          </div>
        </CardShell>
      )
    }
    return (
      <CardShell title="What failed" rightSlot={<span>0 failures</span>}>
        <div className="px-4 py-6 text-[13px] text-[var(--color-text-secondary)]">
          No failures in this run.
        </div>
      </CardShell>
    )
  }

  const shown = clusters.slice(0, MAX_FAILURE_BLOCKS)
  return (
    <CardShell
      title="What failed"
      rightSlot={<span>{shown.length} of {clusters.length} failure{clusters.length === 1 ? '' : 's'} shown</span>}
    >
      {shown.map((c) => (
        <FailureBlock
          key={c.cluster_id}
          cluster={c}
          runId={runId}
          onPromote={onPromote ? () => onPromote(c) : undefined}
          onDecisionTrail={onDecisionTrail}
        />
      ))}
      {clusters.length > shown.length && (
        <div className="px-4 py-2.5 text-[12px] text-[var(--color-text-muted)] border-t" style={{ borderColor: 'var(--color-border)' }}>
          + {clusters.length - shown.length} more failure{clusters.length - shown.length === 1 ? '' : 's'} not shown
        </div>
      )}
    </CardShell>
  )
}

function FailureBlock({
  cluster,
  runId,
  onPromote,
  onDecisionTrail,
}: {
  cluster: FailureClusterIntel
  runId: string
  onPromote?: () => void
  onDecisionTrail: () => void
}) {
  const firstTestId = cluster.member_test_ids[0]
  const errPreview = cluster.representative_error?.split('\n').slice(0, 4).join('\n') ?? ''
  return (
    <div
      className="relative"
      style={{
        padding: 16,
        borderLeft: '3px solid var(--status-failed)',
        background: 'linear-gradient(90deg, color-mix(in srgb, var(--status-failed) 6%, transparent), transparent 30%), var(--color-bg-card)',
        borderTop: '1px solid var(--color-border)',
      }}
    >
      <div className="flex items-center justify-between gap-2.5 flex-wrap mb-2">
        <span className="font-mono text-[13px] text-[var(--color-text)] font-medium truncate">
          {cluster.label}
        </span>
        <CriticalityPill criticality={cluster.criticality_level} />
      </div>
      <div className="flex flex-wrap gap-3.5 text-[12px] text-[var(--color-text-muted)] mb-2.5">
        <span>{cluster.size} test{cluster.size === 1 ? '' : 's'} in cluster</span>
        {cluster.cohesion_score != null && (
          <span>· cohesion {Math.round(cluster.cohesion_score * 100)}%</span>
        )}
      </div>
      {errPreview && (
        <pre
          role="region"
          aria-label="Error stack trace"
          className="font-mono text-[12px] whitespace-pre-wrap m-0"
          style={{
            background: 'var(--color-bg)',
            border: '1px solid var(--color-border)',
            borderLeft: '2px solid color-mix(in srgb, var(--status-failed) 55%, transparent)',
            borderRadius: 'var(--radius-sm)',
            padding: '10px 12px',
            color: 'var(--color-text-secondary)',
          }}
        >
          {errPreview}
        </pre>
      )}
      <div className="flex flex-wrap gap-2 mt-3">
        {firstTestId && (
          <Link
            to={`/runs/${runId}/tests/${firstTestId}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors"
            style={{ borderColor: 'var(--color-border)' }}
          >
            Open test case
          </Link>
        )}
        <GhostBtn onClick={onDecisionTrail} title="Why the AI assigned this category">
          Decision trail
        </GhostBtn>
        {onPromote && (
          <GhostBtn onClick={onPromote} title="Promote this cluster to a defect (Jira)">
            <TicketCheck className="h-3.5 w-3.5" /> File defect
          </GhostBtn>
        )}
      </div>
    </div>
  )
}

// The cluster carries a criticality level, not a failure classification, so
// the pill names the criticality it was given — and is absent when the
// cluster was never scored.
function CriticalityPill({ criticality }: { criticality: FailureClusterIntel['criticality_level'] }) {
  if (!criticality) return null
  const severe = criticality === 'CRITICAL' || criticality === 'HIGH'
  const bg = severe ? 'color-mix(in srgb, var(--status-failed) 15%, transparent)' : 'var(--color-bg-secondary)'
  const fg = severe ? 'var(--status-failed)' : 'var(--color-text-faint)'
  const pretty = criticality.charAt(0) + criticality.slice(1).toLowerCase()
  return (
    <span
      data-testid="cluster-criticality"
      className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-semibold uppercase"
      style={{ background: bg, color: fg, letterSpacing: 'var(--tracking-wide)' }}
    >
      {pretty} criticality
    </span>
  )
}

// ── Body: Recommended actions ──────────────────────────────────────────────
// Per updated README §5.8:
//   - Default order: Developer → QA → Release Manager → SRE. (The persona
//     tabs that re-ordered it were removed in P4.)
//   - Rows that have no copy in `roleActions` are rendered dimmed (60 %
//     opacity) and locked, not hidden — the spec wants the read of "this
//     role has nothing to do this run" to be explicit, not invisible.
function RecommendedActionsCard({
  roleActions,
  ownerHints,
}: {
  roleActions: Record<string, string>
  ownerHints?: Record<string, string>
}) {
  type Row = { id: string; label: string; tone: 'dev' | 'qa' | 'rm' | 'sre'; Icon: typeof Wrench }
  const orderedRows: Row[] = [
    { id: 'DEVELOPER',       label: 'Developer',       tone: 'dev', Icon: Wrench      },
    { id: 'QA',              label: 'QA',              tone: 'qa',  Icon: UserRound   },
    { id: 'RELEASE_MANAGER', label: 'Release Manager', tone: 'rm',  Icon: ShieldCheck },
    { id: 'SRE',             label: 'SRE',             tone: 'sre', Icon: Stethoscope },
  ]

  // We render every canonical role even when the action prose is empty —
  // dimmed rows are intentional per §5.8 ("de-prioritized roles").
  const hasAnyAction = orderedRows.some(r => roleActions[r.id])
  if (!hasAnyAction) {
    return (
      <CardShell title="Recommended actions" rightSlot={<span>Routed by role</span>}>
        <div className="p-4 text-[13px] text-[var(--color-text-muted)]">
          No role-specific actions generated for this run.
        </div>
      </CardShell>
    )
  }

  const copyText = (text: string) => {
    void copyTextToClipboard(text).then((ok) => {
      if (ok) toast.success('Action copied')
      else toast.error('Copy failed')
    })
  }

  return (
    <CardShell title="Recommended actions" rightSlot={<span>Routed by role</span>}>
      <div className="p-3.5 grid gap-2.5">
        {orderedRows.map((r) => {
          const txt = roleActions[r.id]
          const owner = ownerHints?.[r.id]
          // Dim a row only when there's no action prose (de-prioritized
          // role for this run).
          const dim = !txt
          return (
            <RoleRow
              key={r.id}
              tone={r.tone}
              Icon={r.Icon}
              label={r.label}
              owner={owner}
              text={txt ?? 'No action required for this role this run.'}
              dim={dim}
              onCopy={() => txt && copyText(txt)}
            />
          )
        })}
      </div>
    </CardShell>
  )
}

function RoleRow({
  tone, Icon, label, owner, text, dim, onCopy,
}: {
  tone: 'dev' | 'qa' | 'rm' | 'sre'
  Icon: typeof Wrench
  label: string
  owner?: string
  text: string
  dim: boolean
  onCopy: () => void
}) {
  const pal = {
    dev: { bg: 'color-mix(in srgb, var(--status-flaky) 10%, transparent)', fg: 'var(--status-flaky)' },
    qa:  { bg: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', fg: 'var(--color-accent)' },
    rm:  { bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)',  fg: 'var(--status-passed)' },
    sre: { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', fg: 'var(--status-broken)' },
  }[tone]
  return (
    <div
      className={clsx('grid gap-2.5 px-3 py-2.5 rounded-md border items-start', dim && 'opacity-65')}
      style={{ gridTemplateColumns: '22px 1fr auto', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <span className="inline-flex items-center justify-center rounded-full" style={{ width: 22, height: 22, background: pal.bg, color: pal.fg }}>
        <Icon className="h-3 w-3" />
      </span>
      <div className="min-w-0">
        <div
          className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex items-center gap-1.5"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          {label}
          {owner && (
            <span
              className="font-mono text-[11px] text-[var(--color-text-secondary)] px-1.5 py-px rounded-sm"
              style={{ background: 'var(--color-bg-secondary)', textTransform: 'none', letterSpacing: 0 }}
            >
              @{owner}
            </span>
          )}
        </div>
        <p className="text-[13px] text-[var(--color-text-secondary)] m-0 mt-1 leading-relaxed">{text}</p>
      </div>
      <button
        type="button"
        onClick={onCopy}
        title="Copy action text"
        className="inline-flex items-center gap-1.5 px-2 py-1 text-[12px] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] rounded transition-colors self-start"
      >
        <CopyIcon className="h-3 w-3" />
      </button>
    </div>
  )
}

// ── Body: AI confidence ────────────────────────────────────────────────────
function AIConfidenceCard({
  confidencePct,
  evidenceCount,
  toolCount,
  hasBaseline,
  llmUsed,
  fallbackUsed,
  criticalityScored,
  riskAssessed,
  generatedBy,
}: {
  confidencePct: number
  evidenceCount: number
  toolCount: number
  hasBaseline: boolean
  llmUsed: boolean
  fallbackUsed: boolean
  /** At least one failure cluster carries a criticality level. */
  criticalityScored: boolean
  /** The release_risk stage completed, or the decision has a composite risk. */
  riskAssessed: boolean
  /** provenance.generated_by — which engine produced the recommendation. */
  generatedBy?: string | null
}) {
  const tone: 'good' | 'warn' | 'bad' =
    confidencePct >= 70 ? 'good' : confidencePct >= 40 ? 'warn' : 'bad'
  const barFill = tone === 'good'
    ? 'linear-gradient(90deg, var(--status-passed), var(--status-passed))'
    : tone === 'warn'
      ? 'linear-gradient(90deg, var(--status-broken), var(--status-broken))'
      : 'linear-gradient(90deg, var(--status-failed), var(--status-broken))'

  const why = !llmUsed
    ? 'The pipeline ran and scored every dimension, but the LLM reasoning layer was unavailable. Treat the verdict as a deterministic fallback, not a high-trust recommendation.'
    : fallbackUsed
      ? 'LLM reasoning was attempted but fell back to deterministic rules for at least one stage. Confidence is reduced — verify recommendations before acting.'
      : 'Pipeline ran end-to-end with LLM reasoning. Recommendation is supported by evidence collected during analysis.'

  const gaps: { label: string; show: boolean }[] = [
    { label: 'No evidence artifacts collected', show: evidenceCount === 0 },
    { label: 'No baseline run for comparison',  show: !hasBaseline },
    { label: '0 tools invoked during analysis', show: toolCount === 0 },
  ]
  const passedChecks: string[] = []
  if (llmUsed && !fallbackUsed)  passedChecks.push('LLM reasoning')
  if (toolCount > 0)             passedChecks.push(`${toolCount} tool${toolCount === 1 ? '' : 's'} invoked`)
  if (evidenceCount > 0)         passedChecks.push(`${evidenceCount} evidence`)
  if (hasBaseline)               passedChecks.push('Baseline available')
  // Only claimed when the data shows the step produced something: a scored
  // cluster, and a completed release_risk stage or a computed composite risk.
  if (criticalityScored)         passedChecks.push('Criticality scoring')
  if (riskAssessed)              passedChecks.push('Release risk assessment')

  return (
    <CardShell
      title="AI confidence"
      rightSlot={<code className="text-[11px] text-[var(--color-text-secondary)]">v2 · pipeline</code>}
    >
      <div className="p-3.5">
        {/* US-15.1: the confidence figure, its calibration basis, the routing
            provenance and the fallback notice all come from the shared trust
            chrome — this card used to render a bare percentage with no basis. */}
        <AISuggestion
          bare
          label="release recommendation"
          confidence={confidencePct}
          lowConfidence={tone === 'bad'}
          provenance={{
            modeUsed: generatedBy ?? (llmUsed ? 'llm' : 'rules'),
            fallbackFrom: fallbackUsed || !llmUsed ? 'llm' : null,
            fallbackReason: fallbackUsed
              ? 'At least one pipeline stage answered deterministically instead.'
              : undefined,
          }}
        >
        <div className="flex items-center gap-2.5">
          <span className="text-[12px] text-[var(--color-text-muted)]">
            {tone === 'good' ? 'High — the suggestion is supported by collected evidence'
              : tone === 'warn' ? 'Moderate — review evidence before acting'
              : 'Low — the suggestion was derived deterministically, not by LLM reasoning'}
          </span>
        </div>
        <div className="rounded-full overflow-hidden mt-2" style={{ height: 4, background: 'var(--color-bg-secondary)' }}>
          <i className="block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, confidencePct))}%`, background: barFill }} />
        </div>

        <p className="text-[12.5px] mt-2.5 leading-[1.5]" style={{ color: 'var(--color-text-secondary)' }}>
          {why}
        </p>

        {gaps.some(g => g.show) && (
          <div className="grid gap-1.5 mt-2.5">
            {gaps.filter(g => g.show).map((g, i) => (
              <div
                key={i}
                className="flex items-center gap-2 text-[12px] text-[var(--color-text-secondary)] rounded-sm px-2.5 py-1.5"
                style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
              >
                <XCircle className="h-3.5 w-3.5 flex-none" style={{ color: 'var(--status-failed)' }} />
                {g.label}
              </div>
            ))}
          </div>
        )}

        {passedChecks.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2.5">
            {passedChecks.map((c, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px]"
                style={{ background: 'color-mix(in srgb, var(--status-passed) 10%, transparent)', color: 'var(--status-passed)', border: '1px solid color-mix(in srgb, var(--status-passed) 25%, transparent)' }}
              >
                <Check className="h-2.5 w-2.5" strokeWidth={3} /> {c}
              </span>
            ))}
          </div>
        )}
        </AISuggestion>
      </div>
    </CardShell>
  )
}

// ── Body: Failure category ─────────────────────────────────────────────────
// Only the categories the run's data names. Rows for categories it never
// named ("Flaky / Infrastructure / Test data — None detected this run",
// padded up to three) were placeholders and were removed in P4 (§5 "Run").
function FailureCategoryCard({ breakdown }: { breakdown: Record<string, number> }) {
  const total = Object.values(breakdown).reduce((s, n) => s + n, 0)
  const presentEntries = Object.entries(breakdown).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1])
  return (
    <CardShell title="Failure category" rightSlot={<span>{total} total</span>}>
      <div className="p-3.5 flex flex-col gap-2">
        {presentEntries.length === 0 && (
          <p className="m-0 text-[12px] text-[var(--color-text-muted)]">No failure was categorised in this run.</p>
        )}
        {presentEntries.map(([cat, count]) => (
          <CategoryRow key={cat} category={cat} count={count} totalAcrossAll={total} active />
        ))}
      </div>
    </CardShell>
  )
}

function CategoryRow({
  category, count, totalAcrossAll, active,
}: { category: string; count: number; totalAcrossAll: number; active: boolean }) {
  const pretty = category
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase())
  const isProductBug = /product/.test(category.toLowerCase()) || /bug/.test(category.toLowerCase())
  const pillBg = active && isProductBug ? 'color-mix(in srgb, var(--status-failed) 15%, transparent)' : 'var(--color-bg-secondary)'
  const pillFg = active && isProductBug ? 'var(--status-failed)' : 'var(--color-text-faint)'
  const rowBg = active && isProductBug ? 'color-mix(in srgb, var(--status-failed) 6%, transparent)' : 'var(--color-bg)'
  const rowBd = active && isProductBug ? 'color-mix(in srgb, var(--status-failed) 20%, transparent)' : 'var(--color-border)'
  const pct = totalAcrossAll > 0 ? Math.round((count / totalAcrossAll) * 100) : 0
  return (
    <div
      className={clsx(
        'flex items-center justify-between rounded-sm px-2.5 py-2',
        !active && 'opacity-60',
      )}
      style={{
        background: rowBg,
        border: active ? `1px solid ${rowBd}` : '1px dashed var(--color-border)',
      }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span
          className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-semibold uppercase"
          style={{ background: pillBg, color: pillFg, letterSpacing: 'var(--tracking-wide)' }}
        >
          {pretty}
        </span>
        <span className="text-[12px] text-[var(--color-text-secondary)] truncate">
          {count > 0 ? `${count} failure${count === 1 ? '' : 's'}` : 'None detected this run'}
        </span>
      </div>
      <span className="font-mono text-[12px]" style={{ color: active && isProductBug ? 'var(--status-failed)' : 'var(--color-text-faint)' }}>
        {count}{count > 0 && totalAcrossAll > 0 ? ` (${pct}%)` : ''}
      </span>
    </div>
  )
}

// ── Body: Provenance footer ─────────────────────────────────────────────────
function ProvenanceFooter({
  evidenceCount, toolCount, schemaVersion, onDecisionTrail,
}: {
  evidenceCount: number
  toolCount: number
  schemaVersion?: number
  onDecisionTrail: () => void
}) {
  return (
    <div
      className="flex items-center justify-between rounded-md px-3.5 py-2.5 text-[11.5px] text-[var(--color-text-muted)]"
      style={{ background: 'var(--color-bg)', border: '1px dashed var(--color-border)' }}
    >
      <span>
        <strong style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>Provenance</strong>
        {' · '}
        <code className="font-mono text-[11.5px]">pipeline v{schemaVersion ?? 2}</code>
        {' · '}
        {evidenceCount} evidence · {toolCount} tools
      </span>
      <button
        type="button"
        onClick={onDecisionTrail}
        className="inline-flex items-center gap-1 hover:underline"
        style={{ color: 'var(--color-accent)' }}
      >
        Decision trail <ArrowRight className="h-3 w-3" />
      </button>
    </div>
  )
}

/// ── The verdict, derived ────────────────────────────────────────────────────
interface Verdict {
  gate: Gate
  decision: ReleaseDecisionIntel | null
  /** A verified (terminal) decision report backs the verdict. */
  terminalBacked: boolean
  lede?: string
  affectedSuite?: string
  dimensions: DimensionScore[]
  /** The composite risk, rounded; null when the decision carries none. */
  score: number | null
}

/**
 * The verdict one intelligence payload states. A verified decision report
 * wins over the pipeline's own release decision; with a report envelope but
 * no verified report there is no verdict (PENDING), never the legacy one.
 */
function deriveVerdict(intelligence: RunIntelligence): Verdict {
  const { structured_summary, release_decision, failure_clusters, affected_suites } = intelligence
  const decisionReport = structured_summary?.decision_intelligence
  const latestVerification = structured_summary?.decision_report_verification
  const latestAttempt = structured_summary?.latest_decision_attempt
  const decisionTrust = deriveDecisionTrustState(decisionReport, latestVerification, latestAttempt)
  const reportRelease = decisionTrust.displayReport?.release_decision
  const retained: ReleaseDecisionIntel | null = decisionTrust.displayReport && reportRelease?.recommendation
    ? {
        recommendation: reportRelease.recommendation,
        risk_score: reportRelease.risk_score ?? reportRelease.composite_risk,
        composite_risk: reportRelease.composite_risk ?? null,
        blocking_issues: reportRelease.blocking_issues ?? [],
        conditions_for_go: reportRelease.conditions_for_go ?? [],
        reasoning: reportRelease.reasoning ?? '',
      }
    : null
  const hasEnvelope = Boolean(decisionReport || latestVerification || latestAttempt)
  const decision = retained ?? (hasEnvelope ? null : release_decision)
  const terminalBacked = Boolean(decisionTrust.displayReport)
  const rawScore = decision?.composite_risk ?? decision?.risk_score
  return {
    gate: gateOf(decision),
    decision,
    terminalBacked,
    lede: terminalBacked
      ? (decision?.reasoning || undefined)
      : (decision?.reasoning ?? structured_summary?.executive_summary ?? undefined),
    // Run-level dimensions approximated from the top cluster's scores.
    affectedSuite: terminalBacked ? undefined : affected_suites[0]?.suite,
    dimensions: terminalBacked ? [] : (failure_clusters[0]?.dimension_scores ?? []),
    score: typeof rawScore === 'number' ? Math.round(rawScore) : null,
  }
}

// ── Verdict banner (above the primary content) ─────────────────────────────
// Hold / Override / Approve-with-conditions controls are not rendered: there
// is no gate-decision endpoint to persist them, and a decision kept only in
// this browser's localStorage is not a decision anyone else sees.
function VerdictBanner({
  verdict, confidencePct, onDecisionTrail,
}: {
  verdict: Verdict
  confidencePct: number
  onDecisionTrail: () => void
}) {
  const t = GATE_THEME[verdict.gate]
  const blockers = verdict.decision?.blocking_issues ?? []
  const facts: BannerFact[] = [
    { label: 'Risk', value: verdict.gate === 'PENDING' || verdict.score === null ? '—' : `${verdict.score}/100` },
    { label: 'Blocking issues', value: blockers.length },
    { label: 'AI confidence', value: `${confidencePct}%` },
  ]
  const lede = verdict.lede
    ?? (verdict.gate === 'PENDING' ? 'Awaiting analysis — no release decision available yet.' : '')
  return (
    <section aria-label="Release verdict" aria-live="polite" className="space-y-1.5">
      <StatusBanner
        state={t.banner}
        title={
          <>
            <span style={{ color: t.gate }}>{t.label}</span>
            <span aria-hidden className="mx-1.5 text-[var(--color-text-muted)]">·</span>
            <span>{t.action}</span>
          </>
        }
        facts={facts}
        action={{ label: 'Decision trail', onClick: onDecisionTrail }}
      />
      {lede && <p className="m-0 max-w-[90ch] px-1 text-[13px] text-[var(--color-text-secondary)]">{lede}</p>}
      {blockers.length > 0 && (
        <p className="m-0 flex items-center gap-2 px-1 text-[13px] text-[var(--color-text-secondary)]">
          <XCircle aria-hidden className="h-4 w-4 flex-none text-[var(--status-failed)]" />
          <span className="min-w-0">
            <strong className="text-[var(--status-failed)]">Blocking</strong> · {blockers[0]}
            {blockers.length > 1 && <span className="text-[var(--color-text-muted)]"> · +{blockers.length - 1} more</span>}
          </span>
          {verdict.affectedSuite && (
            <span className="ml-auto text-[var(--color-text-muted)]">{verdict.affectedSuite} suite</span>
          )}
        </p>
      )}
    </section>
  )
}

// ── How this score is computed (a disclosure below the primary content) ────
function ScoreBreakdown({ verdict }: { verdict: Verdict }) {
  const t = GATE_THEME[verdict.gate]
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <RiskMeter
        gate={verdict.gate}
        score={verdict.score}
        pillBg={t.pillBg}
        pillBd={t.pillBd}
        pillFg={t.pillFg}
        pillLabel={t.label}
        meterColor={t.meter}
      />
      <DimensionGrid scores={verdict.dimensions} fallback={null} />
    </div>
  )
}

// ── The Analysis body ───────────────────────────────────────────────────────
/**
 * The run's AI analysis in the page template's order: the verdict banner,
 * What failed (the primary content), the host's own section, then the score's
 * working and the evidence, collapsed. The Run page's Analysis tab and the
 * standalone page both render exactly this.
 */
export function RunIntelligenceBody({
  runId,
  reportVersion = null,
  afterPrimary,
}: {
  runId: string
  /** The decision report version (`?report_version=`); null reads the latest. */
  reportVersion?: number | null
  /** The host's own section, between What failed and the disclosures. */
  afterPrimary?: ReactNode
}) {
  const { intelligence, isLoading, isError } = useRunIntelligence(runId, reportVersion)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)

  // Auto-complete the "View Run Intelligence" onboarding step the first time
  // intelligence loads for a real project. That step has no DB signal for
  // `auto_detect_progress` to key off (opening a page leaves no row) and no
  // other caller of `completeStep`, so without this it stays `pending` for
  // ever and a self-hoster's setup wizard never reaches 100%. Idempotent and
  // fire-and-forget server-side; keyed on the loaded flag + project so it runs
  // once per open, not on every refresh, and never with the synthetic "All
  // Projects" id. (It lives in the body: opening the Run page's Analysis tab
  // is what "viewing Run Intelligence" is now.)
  const intelligenceViewed = !isLoading && !isError && !!intelligence
  useEffect(() => {
    if (!intelligenceViewed) return
    if (!activeProjectId || activeProjectId === ALL_PROJECTS_ID) return
    void onboardingService.completeStep(activeProjectId, 'view_intelligence').catch(() => {})
  }, [intelligenceViewed, activeProjectId])

  const [decisionTrailOpen, setDecisionTrailOpen] = useState(false)
  const [promoteCluster, setPromoteCluster] = useState<FailureClusterIntel | null>(null)
  // Filing a defect needs QA engineer (POST .../promote, like /analytics/defects).
  const { isQaEngineer } = usePermissions()

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-center h-64"><LoadingSpinner /></div>
        {afterPrimary}
      </div>
    )
  }
  if (isError || !intelligence) {
    return (
      <div className="space-y-4">
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8 text-[var(--status-failed)]" />}
          title="Failed to load Run Intelligence"
          description="Could not fetch AI analysis data for this run."
        />
        {afterPrimary}
      </div>
    )
  }

  const {
    run, structured_summary, failure_clusters, category_breakdown,
    affected_suites, role_actions, pipeline_stages,
    avg_confidence, what_changed_since_last_good_run, provenance,
  } = intelligence

  const verdict = deriveVerdict(intelligence)
  const confidencePct = Math.round((avg_confidence ?? 0) * (avg_confidence > 1 ? 1 : 100))
  const evidenceCount = (structured_summary?.layer3_evidence?.data_sources_used?.length ?? 0)
    + (structured_summary?.layer3_evidence?.top_stack_traces?.length ?? 0)
    + (structured_summary?.layer3_evidence?.log_anomalies?.length ?? 0)
  const toolCount = provenance?.tools_used_count ?? provenance?.sources_used?.length ?? 0
  const llmUsed = !provenance?.fallback_used
  const fallbackUsed = !!provenance?.fallback_used
  const hasBaseline = !!what_changed_since_last_good_run

  return (
    <div className="space-y-4" data-run-analysis="">
      <VerdictBanner
        verdict={verdict}
        confidencePct={confidencePct}
        onDecisionTrail={() => setDecisionTrailOpen(true)}
      />

      {/* E8.5: the AI report's human-review status (E8.3 envelope). */}
      <ReviewBanner envelope={intelligence} />

      <section data-primary="" aria-label="What failed">
        <WhatFailedCard
          clusters={failure_clusters}
          runId={run.id}
          onPromote={isQaEngineer ? (c) => setPromoteCluster(c) : undefined}
          onDecisionTrail={() => setDecisionTrailOpen(true)}
          aggregateFailedTests={(run.failed_tests ?? 0) + (run.broken_tests ?? 0)}
          aggregateTotalTests={run.total_tests ?? 0}
          failedTestRows={(intelligence.affected_suites ?? []).reduce((n, s) => n + (s.failed_count ?? 0), 0)}
        />
      </section>

      {afterPrimary}

      <Disclosure
        title="How this score is computed"
        summary={verdict.score === null || verdict.gate === 'PENDING' ? 'no composite risk' : `composite risk ${verdict.score} of 100`}
      >
        <ScoreBreakdown verdict={verdict} />
      </Disclosure>

      <Disclosure
        title="Evidence behind the verdict"
        summary={`${pipeline_stages.length} pipeline stage${pipeline_stages.length === 1 ? '' : 's'} · ${evidenceCount} evidence · ${toolCount} tool${toolCount === 1 ? '' : 's'}`}
      >
        <div className="flex flex-col gap-3.5">
          <PipelineRibbon
            stages={pipeline_stages}
            confidencePct={confidencePct}
            evidenceCount={evidenceCount}
            toolCount={toolCount}
            hasPerTestGap={
              ((run.failed_tests ?? 0) + (run.broken_tests ?? 0)) > 0
              && failure_clusters.length === 0
            }
          />
          <div className="grid gap-3.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <div className="flex flex-col gap-3.5 min-w-0">
              <TestOutcomeCard
                run={run}
                failureClusters={failure_clusters}
                affectedSuites={affected_suites}
                categoryBreakdown={category_breakdown}
                // Read from the stage list rather than inferred from the workflow
                // name: a pipeline reports the stages it actually executed, so this
                // stays correct if clustering is ever added to another workflow.
                clusteringRan={pipeline_stages.some((s) => s.stage_name === 'failure_clustering')}
              />
              <RecommendedActionsCard
                roleActions={role_actions}
                ownerHints={structured_summary?.layer4_action_plan?.owner_hints}
              />
            </div>
            <div className="flex flex-col gap-3.5 min-w-0">
              <AIConfidenceCard
                confidencePct={confidencePct}
                evidenceCount={evidenceCount}
                toolCount={toolCount}
                hasBaseline={hasBaseline}
                llmUsed={llmUsed}
                fallbackUsed={fallbackUsed}
                criticalityScored={failure_clusters.some((c) => c.criticality_level != null)}
                riskAssessed={
                  pipeline_stages.some((s) => s.stage_name.toLowerCase() === 'release_risk' && stageStatus(s) === 'done')
                  || typeof verdict.decision?.composite_risk === 'number'
                }
                generatedBy={provenance?.generated_by}
              />
              <FailureCategoryCard breakdown={category_breakdown} />
              <RunStepFlipCard runId={run.id} />
              <ProvenanceFooter
                evidenceCount={evidenceCount}
                toolCount={toolCount}
                schemaVersion={provenance?.schema_version}
                onDecisionTrail={() => setDecisionTrailOpen(true)}
              />
            </div>
          </div>
        </div>
      </Disclosure>

      <DecisionTrailDrawer
        runId={run.id}
        open={decisionTrailOpen}
        onClose={() => setDecisionTrailOpen(false)}
      />

      {promoteCluster && (
        <DefectPromotionModal
          runId={run.id}
          clusterId={promoteCluster.cluster_id}
          clusterLabel={promoteCluster.label}
          onClose={() => setPromoteCluster(null)}
          onSuccess={(_defectId, jiraUrl) => {
            toast.success(jiraUrl ? 'Defect promoted and Jira ticket created' : 'Defect promoted')
            setPromoteCluster(null)
          }}
        />
      )}
    </div>
  )
}

// ── The verified decision report ────────────────────────────────────────────
/**
 * The run's agentic decision report — facts, inferences, recommendations,
 * their evidence, terminal verification and provenance — with its version
 * picker (`?report_version=`). The Run page's Evidence tab; the top section
 * of the standalone page. Renders nothing while the payload loads, and
 * nothing when the run has no report envelope (the panel's own rule).
 */
export function RunDecisionReport({ runId }: { runId: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedReportVersion = readReportVersion(searchParams)
  const { intelligence } = useRunIntelligence(runId, selectedReportVersion)
  const { versions } = useDecisionReportVersions(runId)
  if (!intelligence) return null
  const summary = intelligence.structured_summary
  return (
    <DecisionIntelligencePanel
      runId={runId}
      report={summary?.decision_intelligence}
      latestVerification={summary?.decision_report_verification}
      latestAttempt={summary?.latest_decision_attempt}
      reportVersion={summary?.decision_report}
      reportVersions={versions}
      selectedReportVersion={selectedReportVersion}
      onSelectReportVersion={(version) => {
        // Functional: the other keys (`tab`, the Tests filters) stay.
        setSearchParams((current) => {
          const next = new URLSearchParams(current)
          if (version == null) next.delete('report_version')
          else next.set('report_version', String(version))
          return next
        })
      }}
    />
  )
}

// ── Page ────────────────────────────────────────────────────────────────────
/**
 * The standalone host of the body (not routed since P4:
 * `/runs/:runId/intelligence` redirects to the Run page's Analysis tab).
 */
