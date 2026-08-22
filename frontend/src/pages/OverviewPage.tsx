import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, CheckCircle, Clock, HelpCircle, LayoutGrid, TrendingUp,
} from 'lucide-react'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import { useDashboardSummary, useFailureCategories, useTrendData } from '@/hooks/useMetrics'
import { useValueMetricsKpi } from '@/hooks/useValueMetrics'
import { useRuns } from '@/hooks/useRuns'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import FirstRunGuide from '@/components/onboarding/FirstRunGuide'
import { isFirstRunGuideDismissed, dismissFirstRunGuide } from '@/components/onboarding/firstRunSteps'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { describeEmptyWindow, formatAgeDays } from '@/utils/emptyWindow'
import { formatDuration, dayTimeAgo } from '@/utils/formatters'
import { clsx } from 'clsx'
import type { TrendPoint } from '@/types/metrics'
import type { DashboardMetricValue, DashboardSummary } from '@/types/analytics'
import type { TestRun } from '@/types/runs'

// `1` = last 24 hours. Label is rendered as "24h" (the only sub-day option);
// all other values render as `${d}d`.
const TIME_OPTIONS = [1, 7, 14, 30, 90] as const

// 5-state verdict layered over the backend's 4-band pass-rate classification
// (red/orange/yellow/green) plus the legacy "no data → PENDING" sentinel.
// ``WATCH`` is the yellow band — pass-rate is healthy but inside the project's
// caution zone (e.g. 95-99%). Still GO, just flagged for review.
type Verdict = 'GO' | 'WATCH' | 'CONDITIONAL' | 'NO_GO' | 'PENDING'

const VERDICT_THEME: Record<Verdict, {
  barColor: string
  glow: string
  border: string
  eyebrowDot: string
  eyebrowText: string
  gateText: string
  meterValue: string
  meterTrack: string
  meterFill: string
  eyebrowLabel: string
  headlineSuffix: string
}> = {
  GO: {
    barColor: 'var(--gate-go)',
    glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 10%, transparent), transparent 55%)',
    border: 'color-mix(in srgb, var(--status-passed) 35%, transparent)',
    eyebrowDot: 'var(--gate-go)',
    eyebrowText: 'var(--status-passed)',
    gateText: 'var(--status-passed)',
    meterValue: 'var(--status-passed)',
    meterTrack: 'color-mix(in srgb, var(--status-passed) 18%, transparent)',
    meterFill: 'linear-gradient(90deg, var(--status-passed), var(--status-passed))',
    eyebrowLabel: 'RELEASE READINESS',
    headlineSuffix: 'ship cleared',
  },
  WATCH: {
    // Yellow band — healthy but flagged. Uses a lemon/yellow palette to keep
    // it visually distinct from CONDITIONAL (which is amber/orange) and GO
    // (which is green).
    barColor: 'var(--status-skipped)',
    glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-skipped) 8%, transparent), transparent 55%)',
    border: 'color-mix(in srgb, var(--status-skipped) 35%, transparent)',
    eyebrowDot: 'var(--status-skipped)',
    eyebrowText: 'var(--status-skipped)',
    gateText: 'var(--status-skipped)',
    meterValue: 'var(--status-skipped)',
    meterTrack: 'color-mix(in srgb, var(--status-skipped) 18%, transparent)',
    meterFill: 'linear-gradient(90deg, var(--status-skipped), var(--status-skipped))',
    eyebrowLabel: 'RELEASE READINESS',
    headlineSuffix: 'go with watch',
  },
  CONDITIONAL: {
    // Orange band — softer warning than NO_GO. Uses a true amber/orange,
    // visually distinct from WATCH.
    barColor: 'var(--gate-conditional)',
    glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-broken) 10%, transparent), transparent 55%)',
    border: 'color-mix(in srgb, var(--status-broken) 35%, transparent)',
    eyebrowDot: 'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText: 'var(--status-broken)',
    meterValue: 'var(--status-broken)',
    meterTrack: 'color-mix(in srgb, var(--status-broken) 18%, transparent)',
    meterFill: 'linear-gradient(90deg, var(--status-broken), var(--status-broken))',
    eyebrowLabel: 'RELEASE READINESS',
    headlineSuffix: 'review before shipping',
  },
  NO_GO: {
    barColor: 'var(--gate-no-go)',
    glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)',
    border: 'color-mix(in srgb, var(--status-failed) 35%, transparent)',
    eyebrowDot: 'var(--gate-no-go)',
    eyebrowText: 'var(--status-failed)',
    gateText: 'var(--status-failed)',
    meterValue: 'var(--status-failed)',
    meterTrack: 'color-mix(in srgb, var(--status-failed) 18%, transparent)',
    meterFill: 'linear-gradient(90deg, var(--status-failed), var(--status-broken))',
    eyebrowLabel: 'RELEASE READINESS',
    headlineSuffix: 'ship blocked',
  },
  PENDING: {
    barColor: 'var(--color-border-light)',
    glow: 'transparent',
    border: 'var(--color-border)',
    eyebrowDot: 'var(--color-text-faint)',
    eyebrowText: 'var(--color-text-muted)',
    gateText: 'var(--color-text-secondary)',
    meterValue: 'var(--color-text-muted)',
    meterTrack: 'rgba(255,255,255,0.06)',
    meterFill: 'linear-gradient(90deg, #64748b, #94a3b8)',
    eyebrowLabel: 'RELEASE READINESS',
    headlineSuffix: 'awaiting evidence',
  },
}

function mapReadinessToVerdict(
  band: DashboardSummary['release_readiness_band'],
  readiness: DashboardSummary['release_readiness'],
  totalExecutions: number,
): Verdict {
  if (totalExecutions <= 0) return 'PENDING'
  // Prefer the 4-band classification when the backend returned one.
  if (band === 'green')  return 'GO'
  if (band === 'yellow') return 'WATCH'
  if (band === 'orange') return 'CONDITIONAL'
  if (band === 'red')    return 'NO_GO'
  // Legacy 3-state fallback for projects without an active policy.
  if (readiness == null) return 'PENDING'
  if (readiness === 'GREEN') return 'GO'
  if (readiness === 'AMBER') return 'CONDITIONAL'
  return 'NO_GO'
}

function gateLabel(v: Verdict): string {
  return v === 'GO' ? 'Go'
    : v === 'WATCH' ? 'Go (watch)'
    : v === 'CONDITIONAL' ? 'Conditional'
    : v === 'NO_GO' ? 'No-Go'
    : 'Pending'
}


// ── Sparkline ────────────────────────────────────────────────────────────
type SparkTone = 'good' | 'warn' | 'bad' | 'neutral'

const SPARK_COLOR: Record<SparkTone, string> = {
  good: 'var(--status-passed)',
  warn: 'var(--status-broken)',
  bad:  'var(--status-failed)',
  neutral: '#9198a1',
}

function Sparkline({ values, tone, gradId }: { values: number[]; tone: SparkTone; gradId: string }) {
  const w = 120
  const h = 28
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const span = Math.max(max - min, 1)
  const stepX = w / Math.max(values.length - 1, 1)
  const points = values.map((v, i) => {
    const x = i * stepX
    const y = h - 4 - ((v - min) / span) * (h - 8)
    return [x, y] as const
  })
  const linePath = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L ${w} ${h} L 0 ${h} Z`
  const last = points[points.length - 1]
  const color = SPARK_COLOR[tone]

  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="w-full h-7">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path d={linePath} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={2} fill={color} />
    </svg>
  )
}

// ── KPI card ─────────────────────────────────────────────────────────────
interface KpiProps {
  label: string
  value: string
  unit?: string
  delta?: { glyph: '▲' | '▼' | '▬'; text: string; tone: SparkTone }
  tone: SparkTone
  series?: number[]
  emptyMsg?: string
  gradId: string
  /** When set, renders a "View all →" footer linking to this route. */
  linkTo?: string
  /** Footer link label override (default: "View all"). */
  linkLabel?: string
}

function KpiCard({ label, value, unit, delta, tone, series, emptyMsg, gradId, linkTo, linkLabel }: KpiProps) {
  const isBad = tone === 'bad'
  const dotColor = SPARK_COLOR[tone]
  const valueIsDash = value === '—'
  return (
    <div
      className={clsx(
        'flex flex-col gap-1.5 px-3.5 py-3 rounded-xl bg-[var(--color-bg-card)]',
        'border border-[var(--color-border)] min-h-[108px]',
      )}
      style={isBad ? { borderColor: 'color-mix(in srgb, var(--status-failed) 35%, transparent)' } : undefined}
    >
      <div className="flex items-center gap-1.5 text-[11px] uppercase text-[var(--color-text-muted)] font-medium" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: dotColor }} aria-hidden />
        <span>{label}</span>
      </div>
      <div className="flex items-baseline justify-between gap-1.5">
        <span
          className={clsx(
            'tabular-nums leading-none',
            valueIsDash
              ? 'text-[22px] font-medium text-[var(--color-text-muted)]'
              : 'text-[26px] font-bold text-[var(--color-text)]',
          )}
          style={{ letterSpacing: '-0.02em' }}
        >
          {value}
          {unit && !valueIsDash && (
            <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-0.5">{unit}</span>
          )}
        </span>
        {delta && (
          <span
            className="text-[11px] font-semibold tabular-nums"
            title="Relative change vs the previous period of the same length"
            style={{
              color: delta.tone === 'good' ? 'var(--status-passed)'
                : delta.tone === 'bad' ? 'var(--status-failed)'
                : 'var(--color-text-muted)',
            }}
          >
            {delta.glyph} {delta.text}
          </span>
        )}
      </div>
      {series && series.length >= 2 ? (
        <Sparkline values={series} tone={tone} gradId={gradId} />
      ) : (
        <div
          className="h-7 pt-1.5 text-[11px] text-[var(--color-text-faint)]"
          style={{ borderTop: '1px dashed var(--color-border)' }}
        >
          {emptyMsg ?? '—'}
        </div>
      )}
      {linkTo && (
        <Link
          to={linkTo}
          className="text-[11px] text-[var(--color-accent)] hover:underline self-start -mt-0.5"
        >
          {linkLabel ?? 'View all'} →
        </Link>
      )}
    </div>
  )
}

// ── Verdict card ─────────────────────────────────────────────────────────
interface VerdictCardProps {
  verdict: Verdict
  newFailures24h: number
  newFailuresDelta: number
  totalExecutions: number
  windowDays: number
  generatedLabel: string
  passRate: number
  /** F-067: the population the rate is over, from the API. */
  passRateBasisLabel?: string | null
}

function VerdictCard({
  verdict, newFailures24h, newFailuresDelta, totalExecutions, windowDays,
  generatedLabel, passRate, passRateBasisLabel,
}: VerdictCardProps) {
  const t = VERDICT_THEME[verdict]
  const gate = gateLabel(verdict)
  // Real, weighted pass rate for the window — clamped for the meter width.
  const passRatePct = Math.min(100, Math.max(0, Math.round(passRate)))

  const lede = verdict === 'PENDING' ? (
    <>No test executions in the last {windowDays} days — readiness will assess once data lands in <code className="font-mono text-[12px]">release</code>.</>
  ) : verdict === 'NO_GO' ? (
    <>{newFailures24h} new failure{newFailures24h === 1 ? '' : 's'} in the last 24 h on a run that completed with failures still open. Resolve the failures or override before merging to <code className="font-mono text-[12px]">release</code>.</>
  ) : verdict === 'CONDITIONAL' ? (
    <>Some quality criteria need attention. Verify the warning evidence before merging to <code className="font-mono text-[12px]">release</code>.</>
  ) : (
    <>All quality gates passed across {totalExecutions} test execution{totalExecutions === 1 ? '' : 's'} in the last {windowDays} days. Safe to merge to <code className="font-mono text-[12px]">release</code>.</>
  )

  const reason1Tone: SparkTone = newFailures24h > 0 ? 'bad' : 'neutral'
  const reason2Tone: SparkTone = verdict === 'NO_GO' ? 'bad'
    : verdict === 'CONDITIONAL' ? 'warn'
    : verdict === 'GO' ? 'good'
    : 'neutral'

  const reason1Value = verdict === 'PENDING' ? '—' : `${newFailures24h}`
  const reason1Sub = verdict === 'PENDING'
    ? '(no data)'
    : (newFailuresDelta === 0 ? '(unchanged)'
       : newFailuresDelta > 0 ? `(was ${Math.max(newFailures24h - newFailuresDelta, 0)})`
       : `(▼ ${Math.abs(newFailuresDelta)})`)

  const reason2Value = verdict === 'PENDING' ? '—' : `${passRatePct}%`
  // F-067: name the POPULATION, not just "weighted". /overview counts every
  // execution and the Summary Report counts each distinct test once — 81.0%
  // vs 83.3% on the same window. Both are right; showing which is which is
  // what stops them reading as a contradiction. Falls back to the old copy
  // when the API omits it (a cached pre-#588 payload).
  const reason2Sub = verdict === 'PENDING'
    ? '(awaiting runs)'
    : `${passRateBasisLabel || 'weighted'} · ${windowDays}d`
  const reason3Value = `${totalExecutions}`
  // `total_executions_7d` counts TEST EXECUTIONS, not runs. Calling it runs
  // overstated the sample by the average tests-per-run: measured live at
  // "702 runs" against 104 real runs, and "60 runs" for a 6-run project.
  const reason3Sub = `test executions / ${windowDays} days`

  return (
    <div
      className="relative flex flex-col gap-3.5 rounded-xl border overflow-hidden"
      style={{
        background: `${t.glow}, var(--color-bg-card)`,
        borderColor: t.border,
        padding: '18px 18px 16px 21px',
      }}
    >
      <span aria-hidden className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: t.barColor }} />

      <div className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div
            className="flex items-center gap-2 text-[11px] font-semibold uppercase"
            style={{ color: t.eyebrowText, letterSpacing: 'var(--tracking-wider)' }}
          >
            <span
              className={clsx('h-1.5 w-1.5 rounded-full inline-block', verdict === 'NO_GO' && 'testlookup-verdict-pulse')}
              style={{
                background: t.eyebrowDot,
                animation: verdict === 'NO_GO' ? 'testlookup-pulse 1.6s ease-out infinite' : undefined,
              }}
              aria-hidden
            />
            <span>{t.eyebrowLabel}</span>
          </div>
          <h2
            className="text-[28px] font-bold mt-1.5 mb-1"
            style={{ color: 'var(--color-text)', lineHeight: 1.1, letterSpacing: '-0.02em' }}
          >
            <span style={{ color: t.gateText }}>{gate}</span>
            <span className="text-[var(--color-text-muted)] mx-2">·</span>
            <span>{t.headlineSuffix}</span>
          </h2>
          <p className="text-[13px] m-0 max-w-[56ch]" style={{ color: 'var(--color-text-secondary)' }}>
            {lede}
          </p>
        </div>

        <div className="flex flex-col items-end gap-1 shrink-0">
          <div
            className="text-[26px] font-bold tabular-nums leading-none"
            style={{ color: t.meterValue, letterSpacing: '-0.02em' }}
          >
            {verdict === 'PENDING' ? '—' : `${passRatePct}%`}
          </div>
          <div
            className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]"
            style={{ letterSpacing: 'var(--tracking-wider)' }}
          >
            Pass rate
          </div>
          <div className="w-[110px] h-1 rounded-full mt-1" style={{ background: t.meterTrack }}>
            <div
              className="h-full rounded-full transition-[width] duration-300"
              style={{ width: `${verdict === 'PENDING' ? 0 : passRatePct}%`, background: t.meterFill }}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2.5">
        <ReasonCard label="New failures · 24h" value={reason1Value} sub={reason1Sub} tone={reason1Tone} />
        <ReasonCard label="Pass rate" value={reason2Value} sub={reason2Sub} tone={reason2Tone} />
        <ReasonCard label="Sample size" value={reason3Value} sub={reason3Sub} tone="neutral" />
      </div>

      <div className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
        <Clock className="h-3.5 w-3.5" />
        <span>
          Verdict generated {generatedLabel} by{' '}
          <span className="font-medium text-[var(--color-text-secondary)]">Quality workflow</span>
        </span>
      </div>
    </div>
  )
}

function ReasonCard({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: SparkTone }) {
  const valueColor = tone === 'bad' ? 'var(--status-failed)'
    : tone === 'warn' ? 'var(--status-skipped)'
    : tone === 'good' ? 'var(--status-passed)'
    : 'var(--color-text)'
  return (
    <div
      className="rounded-md px-3 py-2.5 border"
      style={{ background: 'rgba(255,255,255,0.02)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="text-[11px] uppercase text-[var(--color-text-muted)]"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        {label}
      </div>
      <div className="text-[14px] font-semibold mt-1 tabular-nums" style={{ color: valueColor }}>
        {value}{' '}
        <small className="text-[12px] font-medium text-[var(--color-text-muted)]">{sub}</small>
      </div>
    </div>
  )
}

// ── Workflow ribbon ──────────────────────────────────────────────────────
type RibbonStageState = 'done' | 'warn' | 'pending' | 'running'

interface RibbonStage {
  name: string
  desc: string
  state: RibbonStageState
  pillText: string
  pillTone: 'accent' | 'red'
  /**
   * Route the card navigates to when clicked. The cards were styled as
   * ``cursor-pointer`` + ``tabIndex={0}`` for months but had no handler —
   * users reported the panels "show nothing." Each stage now drills into
   * the page that owns the underlying evidence.
   */
  linkTo: string
}

function buildRibbonStages(
  summary: DashboardSummary | undefined,
  totalExecutions: number,
  days: number,
): RibbonStage[] {
  const verdict = mapReadinessToVerdict(summary?.release_readiness_band, summary?.release_readiness, totalExecutions)
  const passRateRaw = (summary?.avg_pass_rate_7d?.value as number | undefined) ?? 0
  const newFailures = (summary?.new_failures_24h?.value as number | undefined) ?? 0
  const flaky = (summary?.flaky_test_count?.value as number | undefined) ?? 0
  const defects = (summary?.active_defects?.value as number | undefined) ?? 0

  if (totalExecutions <= 0) {
    return [
      { name: 'Quality Snapshot', state: 'pending', desc: `No runs captured in ${days} days`, pillText: 'awaiting data', pillTone: 'accent', linkTo: '/runs' },
      { name: 'Readiness Check',  state: 'pending', desc: 'Need ≥ 1 run to assess',           pillText: 'pending',       pillTone: 'accent', linkTo: '/release-gate' },
      { name: 'Trend Analysis',   state: 'pending', desc: 'No baseline yet',                   pillText: 'pending',       pillTone: 'accent', linkTo: '/trends' },
      { name: 'Action Focus',     state: 'pending', desc: 'No actions queued',                 pillText: 'pending',       pillTone: 'accent', linkTo: '/failures' },
    ]
  }

  const actionCount = newFailures + flaky
  // Pill text is a short, REAL status token per stage (no fabricated evidence
  // counts / stage durations — the dashboard has no per-stage timing or
  // evidence-count signal to report).
  return [
    {
      name: 'Quality Snapshot',
      state: 'done',
      desc: `Capture current state — ${totalExecutions} test execution${totalExecutions === 1 ? '' : 's'} / ${defects} active defect${defects === 1 ? '' : 's'}`,
      pillText: `${totalExecutions} execution${totalExecutions === 1 ? '' : 's'}`,
      pillTone: 'accent',
      linkTo: '/runs',
    },
    {
      name: 'Readiness Check',
      state: verdict === 'GO' ? 'done' : 'warn',
      desc: `Assess release fitness — ${Math.round(passRateRaw)}% pass rate over ${days}d`,
      pillText: gateLabel(verdict),
      pillTone: verdict === 'GO' ? 'accent' : 'red',
      linkTo: '/release-gate',
    },
    {
      name: 'Trend Analysis',
      state: 'done',
      desc: totalExecutions < 3
        // The sparse branch called executions "days", the other called them
        // "runs". They are neither.
        ? `Sparse data — ${totalExecutions} test execution${totalExecutions === 1 ? '' : 's'} vs ${days}-day baseline`
        : `${totalExecutions} test executions vs ${days}-day baseline`,
      pillText: `${days}d baseline`,
      pillTone: 'accent',
      linkTo: '/trends',
    },
    {
      name: 'Action Focus',
      state: 'done',
      desc: actionCount > 0
        ? `${actionCount} action${actionCount === 1 ? '' : 's'} queued — ${[
            newFailures > 0 ? `fix ${newFailures} failure${newFailures === 1 ? '' : 's'}` : '',
            flaky > 0 ? `${flaky} flake` : '',
          ].filter(Boolean).join(', ')}`
        : 'No actions required',
      pillText: actionCount > 0 ? `${actionCount} to fix` : 'clear',
      pillTone: actionCount > 0 ? 'red' : 'accent',
      linkTo: '/failures',
    },
  ]
}

function ChevronArrow() {
  return (
    <svg width={18} height={28} viewBox="0 0 18 28" aria-hidden className="shrink-0">
      <path
        d="M3 4 L13 14 L3 24"
        stroke="var(--color-border-light)"
        strokeWidth={1.5}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function StageCard({ stage }: { stage: RibbonStage }) {
  const badge = stage.state === 'done'
    ? { bg: 'color-mix(in srgb, var(--status-passed) 18%, transparent)', fg: 'var(--status-passed)', glyph: '✓' }
    : stage.state === 'warn'
      ? { bg: 'color-mix(in srgb, var(--status-broken) 18%, transparent)', fg: 'var(--status-skipped)', glyph: '!' }
      : stage.state === 'running'
        ? { bg: 'color-mix(in srgb, var(--color-accent) 18%, transparent)', fg: 'var(--color-accent)', glyph: '·' }
        : { bg: 'rgba(255,255,255,0.04)', fg: 'var(--color-text-faint)', glyph: '◯' }

  const pill = stage.pillTone === 'red'
    ? { bg: 'color-mix(in srgb, var(--status-failed) 12%, transparent)', fg: 'var(--status-failed)' }
    : { bg: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', fg: 'var(--color-accent)' }

  return (
    <Link
      to={stage.linkTo}
      aria-label={`Open ${stage.name}`}
      className="flex flex-col gap-1.5 rounded-md px-3 py-2.5 transition-all duration-150 cursor-pointer hover:-translate-y-px focus:-translate-y-px focus:outline-none no-underline"
      style={{
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        minHeight: 92,
        minWidth: 168,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
    >
      <div className="flex items-center gap-1.5">
        <span
          className="h-4 w-4 rounded-full inline-flex items-center justify-center"
          style={{ background: badge.bg, color: badge.fg, fontSize: 10, fontWeight: 700, lineHeight: 1 }}
        >
          {badge.glyph}
        </span>
        <span className="text-[12.5px] font-semibold text-[var(--color-text)]">{stage.name}</span>
      </div>
      <p className="text-[11px] m-0 text-[var(--color-text-muted)]" style={{ lineHeight: 1.35 }}>
        {stage.desc}
      </p>
      <div className="flex items-center mt-auto text-[11px] text-[var(--color-text-muted)]">
        <span
          className="rounded-full px-1.5 py-px font-medium"
          style={{ background: pill.bg, color: pill.fg, fontSize: 10.5 }}
        >
          {stage.pillText}
        </span>
      </div>
    </Link>
  )
}

// ── Execution trend chart ────────────────────────────────────────────────
const CHART_COLORS = {
  passed:  'var(--status-passed)',
  failed:  'var(--status-failed)',
  skipped: 'var(--status-broken)',
}

interface ChartTooltipPayload {
  name?: string
  value?: number
  color?: string
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: ChartTooltipPayload[]; label?: string }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div
      className="rounded-md px-3 py-2 text-xs"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="text-[11px] uppercase text-[var(--color-text-muted)] mb-1"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        {label}
      </div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 tabular-nums">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-[var(--color-text-secondary)] capitalize">{p.name}</span>
          <span className="ml-auto text-[var(--color-text)] font-semibold">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

function ExecutionTrendChart({ trends, days }: { trends: TrendPoint[]; days: number }) {
  const data = useMemo(
    () =>
      trends.map((p) => ({
        // ``p.date`` is ISO ``yyyy-mm-dd`` from the backend; chart x-axis
        // wants the short ``mm-dd`` for compactness. Slice 5..10. The
        // legacy ``"May 16"`` format (length 6) is no longer produced;
        // the ``length >= 10`` guard keeps any stray short value usable
        // rather than crashing if a caller injects one.
        // ``day`` was emitted by an older metrics response. Keep the chart
        // honest (and crash-free) if that legacy shape is still in cache.
        date: (p.date ?? (p as TrendPoint & { day?: string }).day ?? '').length >= 10
          ? (p.date ?? (p as TrendPoint & { day?: string }).day ?? '').slice(5, 10)
          : (p.date ?? (p as TrendPoint & { day?: string }).day ?? ''),
        passed: p.passed,
        failed: p.failed,
        skipped: p.skipped,
      })),
    [trends],
  )

  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-md text-[var(--color-text-faint)] text-[12px]"
        style={{ height: 240, borderTop: '1px dashed var(--color-border)' }}
      >
        {data.length === 0
          ? `No executions in the last ${days} days.`
          : `1 of ${days} days has data — a trend line needs at least 2.`}
      </div>
    )
  }

  const totalPassed  = trends.reduce((s, p) => s + p.passed,  0)
  const totalFailed  = trends.reduce((s, p) => s + p.failed,  0)
  const totalSkipped = trends.reduce((s, p) => s + p.skipped, 0)
  const grand = totalPassed + totalFailed + totalSkipped
  const pct = (n: number) => (grand > 0 ? Math.round((n / grand) * 100) : 0)

  return (
    <>
      <div className="relative" style={{ height: 240 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 14, right: 12, left: 0, bottom: 6 }}>
            <defs>
              <linearGradient id="areaPassed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.passed} stopOpacity={0.5} />
                <stop offset="100%" stopColor={CHART_COLORS.passed} stopOpacity={0.08} />
              </linearGradient>
              <linearGradient id="areaFailed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.failed} stopOpacity={0.5} />
                <stop offset="100%" stopColor={CHART_COLORS.failed} stopOpacity={0.08} />
              </linearGradient>
              <linearGradient id="areaSkipped" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.skipped} stopOpacity={0.5} />
                <stop offset="100%" stopColor={CHART_COLORS.skipped} stopOpacity={0.08} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#656d76', fontSize: 10 }} dy={6} />
            <YAxis axisLine={false} tickLine={false} tick={{ fill: '#656d76', fontSize: 10 }} width={32} />
            <Tooltip content={<ChartTooltip />} />
            <Area type="monotone" dataKey="failed"  stackId="1" stroke={CHART_COLORS.failed}  strokeWidth={1.5} fill="url(#areaFailed)" />
            <Area type="monotone" dataKey="skipped" stackId="1" stroke={CHART_COLORS.skipped} strokeWidth={1.5} fill="url(#areaSkipped)" strokeDasharray="4 3" />
            <Area type="monotone" dataKey="passed"  stackId="1" stroke={CHART_COLORS.passed}  strokeWidth={1.6} fill="url(#areaPassed)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div
        className="grid grid-cols-4 gap-2.5 mt-3 pt-3"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <FootStat k="Passed"     v={`${totalPassed}`}  small={`${pct(totalPassed)}%`}  smallTone="muted" />
        <FootStat k="Failed"     v={`${totalFailed}`}  small={`${pct(totalFailed)}%`}  smallTone="bad" />
        <FootStat k="Skipped"    v={`${totalSkipped}`} small={`${pct(totalSkipped)}%`} smallTone="muted" />
        <FootStat k="Automation" v={`${grand}`}        small={`across ${data.length} day${data.length === 1 ? '' : 's'}`} smallTone="muted" />
      </div>
    </>
  )
}

function FootStat({ k, v, small, smallTone }: { k: string; v: string; small: string; smallTone: 'muted' | 'bad' | 'good' }) {
  const smallColor = smallTone === 'bad' ? 'var(--status-failed)'
    : smallTone === 'good' ? 'var(--status-passed)'
    : 'var(--color-text-muted)'
  return (
    <div className="flex flex-col gap-0.5">
      <div
        className="text-[11px] uppercase text-[var(--color-text-muted)]"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        {k}
      </div>
      <div className="text-[16px] font-semibold tabular-nums text-[var(--color-text)]">
        {v}{' '}
        <small className="text-[11px] font-medium" style={{ color: smallColor }}>{small}</small>
      </div>
    </div>
  )
}

// ── Blockers panel ───────────────────────────────────────────────────────
/**
 * ``newFailures`` is ``new_failures_24h`` — a FIXED 24-hour count computed in
 * ``metrics_service`` as ``status == FAILED AND created_at >= now - 24h``. It
 * deliberately ignores the page's time-window selector (verified live: the
 * value is identical at days=1/7/30/90).
 *
 * The copy here must therefore say 24 h and nothing else. It previously
 * described the same number as "in the window" and "since the last green
 * run" — the second naming a regression-since-green baseline that is not part
 * of the computation at all.
 */
function BlockersPanel({
  newFailures,
  hasData,
  verdict,
}: {
  newFailures: number
  hasData: boolean
  verdict: Verdict
}) {
  return (
    <div className="card overflow-hidden">
      <div
        className="flex items-center justify-between px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">What&apos;s blocking release</h3>
        {hasData && newFailures > 0 && (
          <span
            className="px-1.5 py-0.5 rounded-full text-[11px] font-semibold"
            style={{ background: 'color-mix(in srgb, var(--status-failed) 15%, transparent)', color: 'var(--status-failed)' }}
          >
            {newFailures} new · 24h
          </span>
        )}
      </div>

      {!hasData || newFailures <= 0 ? (
        <div className="px-4 py-8 flex flex-col items-center text-center">
          {verdict === 'NO_GO' ? (
            <AlertTriangle className="h-8 w-8 mb-2 text-[var(--status-failed)]" />
          ) : verdict === 'CONDITIONAL' ? (
            <AlertTriangle className="h-8 w-8 mb-2 text-[var(--status-broken)]" />
          ) : (
            <CheckCircle className="h-8 w-8 mb-2 text-[var(--status-passed)]" />
          )}
          <p className="text-[13px] text-[var(--color-text-secondary)] m-0">
            {!hasData
              ? 'No data yet — blockers will appear once failures land.'
              : verdict === 'NO_GO'
                ? 'Release remains blocked by unresolved failures.'
                : verdict === 'CONDITIONAL'
                  ? 'Release needs review before shipping.'
                  : 'Nothing is blocking release.'}
          </p>
          <p className="text-[12px] text-[var(--color-text-muted)] mt-1 m-0">
            {!hasData
              ? 'Run a workflow to populate this panel.'
              : verdict === 'NO_GO'
                ? 'No new failures in the last 24 h; existing failures still require resolution or an approved override.'
                : verdict === 'CONDITIONAL'
                  ? 'No new failures in the last 24 h; review the warning evidence before merging.'
                  : 'No failing tests in the last 24 h.'}
          </p>
        </div>
      ) : (
        <div className="px-4 py-6 text-[12px] text-[var(--color-text-muted)]">
          {newFailures} new failure{newFailures === 1 ? '' : 's'} in the last 24 h.{' '}
          Per-failure detail is sourced from the failures view —{' '}
          <Link to="/failures" className="text-[var(--color-accent)] hover:underline">open all failures →</Link>
        </div>
      )}

      {hasData && newFailures > 0 && (
        <div
          className="flex items-center justify-between px-4 py-2.5 text-[12px] text-[var(--color-text-muted)]"
          style={{ borderTop: '1px solid var(--color-border)' }}
        >
          <span>Showing the {newFailures} failure{newFailures === 1 ? '' : 's'} from the last 24 h</span>
          <Link to="/failures" className="text-[var(--color-accent)] hover:underline inline-flex items-center gap-1">
            Open all failures <ArrowRight className="h-3 w-3" />
          </Link>
        </div>
      )}
    </div>
  )
}

// ── Coverage micro-strip ─────────────────────────────────────────────────
function MicroStrip({ summary, days }: { summary: DashboardSummary | undefined; days: number }) {
  const total = (summary?.total_executions_7d?.value as number | undefined) ?? 0
  const totalDelta = summary?.total_executions_7d?.trend ?? 0
  const avgDuration = summary?.avg_duration_ms?.value as number | undefined
  const newFailures = (summary?.new_failures_24h?.value as number | undefined) ?? 0
  const lastGreen = total > 0 && newFailures === 0 ? '< 24h ago' : '—'

  const cards: Array<{ k: string; v: string; small: string }> = [
    // Two units were wrong here at once, both already fixed elsewhere on this
    // page and both missed in this strip:
    //   * `total_executions_7d` counts TEST EXECUTIONS, not runs — "702 runs"
    //     against 104 real runs on the live dashboard.
    //   * `trend` is a RELATIVE PERCENTAGE, so "+680 this period" read as 680
    //     more runs when it meant the count grew by 680%. Same defect
    //     `deltaFromMetric` carries a comment about ("▲ +400" next to "150").
    { k: 'Automation coverage', v: `${total}`,
      small: `test executions · ${typeof totalDelta === 'number' ? `${totalDelta > 0 ? '+' : ''}${totalDelta}%` : '0%'} this period` },
    { k: 'Last green run',      v: lastGreen,  small: total > 0 ? `over last ${days}d` : 'awaiting runs' },
    { k: 'Mean time to fix',    v: '—',        small: 'needs ≥ 3 fixes to compute' },
    { k: 'Avg run duration',    v: avgDuration ? formatDuration(avgDuration) : '—',
      small: avgDuration ? 'avg · all runs' : 'needs ≥ 3 timed runs' },
  ]

  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-2.5">
      {cards.map((c) => (
        <div
          key={c.k}
          className="flex flex-col gap-1 rounded-md px-3.5 py-2.5"
          style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
        >
          <div
            className="text-[11px] uppercase text-[var(--color-text-muted)]"
            style={{ letterSpacing: 'var(--tracking-wider)' }}
          >
            {c.k}
          </div>
          <div className="text-[18px] font-semibold tabular-nums text-[var(--color-text)]">
            {c.v}{' '}
            <small className="text-[11px] font-medium text-[var(--color-text-muted)]">{c.small}</small>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Helpers for KPI strip data ───────────────────────────────────────────
/** Compact hours: 1 decimal under 100h, whole hours above (US-12.2 KPI). */
function formatHoursSaved(h: number): string {
  return String(h >= 100 ? Math.round(h) : Math.round(h * 10) / 10)
}

function metricNumber(m: DashboardMetricValue | undefined): number {
  if (!m) return 0
  if (typeof m.value === 'number') return m.value
  const parsed = Number(m.value)
  return Number.isFinite(parsed) ? parsed : 0
}

function deltaFromMetric(m: DashboardMetricValue | undefined, badIfDown = false): KpiProps['delta'] | undefined {
  if (!m) return undefined
  const t = m.trend
  const dir = m.trend_direction ?? 'flat'
  // `trend` is a RELATIVE PERCENTAGE change vs the previous period —
  // ((cur - prev) / prev) * 100 in metrics_service — for every metric that
  // flows through here. Rendering it bare put "▲ +400" next to a value of
  // "150" on the dashboard, which reads as four hundred more executions when
  // it means the count quadrupled. A number whose unit is not shown is a
  // number the reader will assign the wrong unit to.
  const pct = (v: number) => `${v > 0 ? '+' : ''}${v}%`
  if (t == null && dir === 'flat') return { glyph: '▬', text: '0%', tone: 'neutral' }
  if (dir === 'up') {
    return { glyph: '▲', text: t != null ? pct(Math.abs(t)) : 'up', tone: badIfDown ? 'good' : 'bad' }
  }
  if (dir === 'down') {
    return { glyph: '▼', text: t != null ? pct(t) : 'down', tone: badIfDown ? 'bad' : 'good' }
  }
  return { glyph: '▬', text: '0%', tone: 'neutral' }
}

function collectSuiteOptions(runs: TestRun[]): string[] {
  const suites = new Map<string, string>()
  for (const run of runs) {
    const names = [run.primary_suite_name, ...(run.suite_names ?? [])]
    for (const raw of names) {
      const suite = raw?.trim()
      if (!suite) continue
      const key = suite.toLowerCase()
      if (!suites.has(key)) suites.set(key, suite)
    }
  }
  return [...suites.values()].sort((a, b) => a.localeCompare(b))
}

function runHasSuite(run: TestRun, suiteName: string): boolean {
  const target = suiteName.trim().toLowerCase()
  if (!target) return true
  const names = [run.primary_suite_name, ...(run.suite_names ?? [])]
  return names.some((suite) => suite?.trim().toLowerCase() === target)
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function OverviewPage() {
  // Default window is last 24h (days=1) across Overview/Runs/Live/Trends/
  // Coverage so users land on the freshest picture by default. They can
  // Window is a global user-level preference (shared with Runs / Trends
  // / Coverage / Failures / Live / Summary / My Failures). Picking 24h
  // here propagates everywhere and vice versa. Snapped to this page's
  // allowed set.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, TIME_OPTIONS)
  const setDays = setStoredDays
  const [showPicker, setShowPicker] = useState(false)
  const [selectedSuite, setSelectedSuite] = useState('')
  const project = useProjectStore((s) => s.activeProject)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // Dismissal is scoped to the active dashboard (a project id, or the
  // All-Projects sentinel) so dismissing the guide on one empty project does
  // not suppress it on a genuinely new, still-empty one. Read fresh each render
  // — keyed on activeProjectId so an in-session project switch re-evaluates (the
  // route does not remount on switch); the bump forces a re-render (and re-read)
  // the moment the user dismisses, without a reload.
  const [, bumpGuideDismissed] = useState(0)
  const guideDismissed = isFirstRunGuideDismissed(activeProjectId)
  const analyticsView = useAnalyticsView('dashboard')

  const suiteFilter = selectedSuite || null
  const { data: summary, isLoading: summaryLoading } = useDashboardSummary(days, suiteFilter)
  const { data: trends,  isLoading: trendsLoading  } = useTrendData(days, suiteFilter)
  // Failure-kind triad (US-9.2): the by-kind aggregation ships on the same
  // failure-categories payload /failures uses, so this KPI is one SWR-cached
  // fetch — no bespoke endpoint.
  const { data: failureCategories } = useFailureCategories(days, suiteFilter)
  // Eng-hours saved (US-12.2): rendered ONLY when the hours-saved model has
  // enough data (`available`). When it doesn't, the card is omitted entirely —
  // no dash-card, no week-one "0 hours" embarrassment.
  const { metrics: valueMetrics } = useValueMetricsKpi()
  const hoursSaved30d = valueMetrics?.available === true && valueMetrics.headline != null
    ? valueMetrics.headline.hours_saved_30d
    : null
  const { data: recentRuns } = useRuns({ page: 1, size: 100, days })
  // The newest run IGNORING the window. Without this the page cannot tell
  // "no runs in the last 7 days" from "no runs at all", and it rendered the
  // same silent 0/— for both. One row, and SWR keys on the params so it does
  // not collide with the windowed fetch above.
  const { data: newestRunPage } = useRuns({ page: 1, size: 1 })
  const recentRunItems = useMemo<TestRun[]>(() => recentRuns?.items ?? [], [recentRuns?.items])
  const suiteOptions = useMemo(() => collectSuiteOptions(recentRunItems), [recentRunItems])
  const latestRun = useMemo(
    () => recentRunItems.find((run) => runHasSuite(run, selectedSuite)),
    [recentRunItems, selectedSuite],
  )

  const emptyWindow = useMemo(
    () =>
      describeEmptyWindow({
        totalInWindow: summary?.total_executions_7d?.value as number | undefined,
        newestRunAt: newestRunPage?.items?.[0]?.created_at ?? null,
        days,
        options: TIME_OPTIONS,
      }),
    [summary?.total_executions_7d?.value, newestRunPage?.items, days],
  )

  const projectLabel = project?.name ?? 'All Projects'
  const scopeLabel = selectedSuite ? `${projectLabel} · ${selectedSuite}` : projectLabel
  // Older cached trend responses used ``day`` instead of ``date``.  Normalize
  // that legacy shape at the view boundary so every downstream chart/label can
  // safely assume a string date and a stale cache cannot crash the dashboard.
  const trendData: TrendPoint[] = (trends?.data ?? []).map((point) => {
    const legacyPoint = point as TrendPoint & { day?: string }
    return {
      ...point,
      date: point.date || legacyPoint.day || '',
    }
  })

  const totalExecutions = metricNumber(summary?.total_executions_7d)
  const passRate = metricNumber(summary?.avg_pass_rate_7d)
  const activeDefects = metricNumber(summary?.active_defects)
  const flaky = metricNumber(summary?.flaky_test_count)
  const newFailures = metricNumber(summary?.new_failures_24h)
  const newFailuresDelta = summary?.new_failures_24h?.trend ?? 0
  const avgDurationMs = summary?.avg_duration_ms?.value as number | undefined

  // Infra-caused failure share (AI-classified failure-kind triad, US-9.2).
  // null when the window has no analyzed failures — the KPI renders "—".
  const byKind = (failureCategories as { by_kind?: { kind: string; count: number }[] } | undefined)?.by_kind ?? []
  const kindTotal = byKind.reduce((s, k) => s + k.count, 0)
  const infraKindCount = byKind.find(k => k.kind === 'infrastructure')?.count ?? 0
  const infraPct = kindTotal > 0 ? Math.round((infraKindCount / kindTotal) * 100) : null

  const verdict = mapReadinessToVerdict(summary?.release_readiness_band, summary?.release_readiness, totalExecutions)
  const generatedLabel = totalExecutions > 0 ? 'just now' : `awaiting data · last ${days} days`
  const lastRunLabel = trendData.length > 0
    ? dayTimeAgo(trendData[trendData.length - 1].date)
    : '—'
  const verdictDotColor = verdict === 'GO' ? 'var(--status-passed)'
    : verdict === 'CONDITIONAL' ? 'var(--status-skipped)'
    : verdict === 'NO_GO' ? 'var(--gate-no-go)'
    : 'var(--color-text-faint)'

  const ribbonStages = useMemo(
    () => buildRibbonStages(summary, totalExecutions, days),
    [summary, totalExecutions, days],
  )

  // First-run: a project (or the whole instance) with no executions in the
  // window and no recent runs gets a getting-started guide instead of a
  // zeroed-out dashboard. Dismissible (persisted per browser).
  const isFreshInstall = !summaryLoading && totalExecutions === 0 && recentRunItems.length === 0
  const showFirstRunGuide = isFreshInstall && !guideDismissed

  // The caption under a KPI fills the slot the sparkline would have used, so it
  // has to explain the missing *trend line* — not deny the metric printed above
  // it. It used to read "no failures recorded" beneath a 23, "awaiting runs"
  // beneath 60 executions, and "need >= 2 runs" for a project with six of them
  // (the shortfall is days of history, not runs).
  const sparklineHint = (dayCount: number) =>
    dayCount === 0
      ? `${days}d · no executions recorded`
      : `1 of ${days} days has data · no trend line`

  const totalSeries = trendData.map((p) => p.passed + p.failed + p.skipped + (p.broken ?? 0))
  const passRateSeries = trendData.map((p) => Math.round(((p.pass_rate ?? 0)) * 100) / 100)
  const failedSeries = trendData.map((p) => p.failed)

  const activeWidgets = new Set(analyticsView.widgetIds)
  const kpiOrder = [
    'total_executions_kpi', 'avg_pass_rate_kpi', 'active_defects_kpi',
    'flaky_tests_kpi', 'new_failures_kpi', 'infra_failures_kpi', 'avg_duration_kpi',
  ]
  const kpiVisible = kpiOrder.filter((id) => activeWidgets.has(id))

  if (!project && !isAllProjects) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <TrendingUp className="h-12 w-12 text-[var(--color-text-faint)] mb-3" />
        <p className="text-[var(--color-text-muted)] font-medium">Select a project to view the dashboard</p>
        <p className="text-[var(--color-text-muted)] text-sm mt-1">Use the project selector in the top bar</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {showFirstRunGuide && (
        <FirstRunGuide
          projectName={project?.name}
          projectId={project?.id}
          onDismiss={() => {
            dismissFirstRunGuide(activeProjectId)
            bumpGuideDismissed((t) => t + 1)
          }}
        />
      )}
      {/* Header — custom layout (status dot + project + last-run) */}
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between mb-1">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]">Dashboard</h1>
          <div className="flex items-center gap-2 mt-1 text-[13px] text-[var(--color-text-muted)] flex-wrap">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: verdictDotColor }} aria-hidden />
            <span className="font-medium" style={{ color: 'var(--color-text-secondary)' }}>{scopeLabel}</span>
            <span aria-hidden>·</span>
            <span>Last run {lastRunLabel}</span>
            {latestRun && (latestRun.primary_suite_name || latestRun.suite_names?.length) && (
              <>
                <span aria-hidden>·</span>
                <SuiteBadge
                  primary={latestRun.primary_suite_name}
                  all={latestRun.suite_names}
                  linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
                />
              </>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <label className="inline-flex items-center gap-2 text-[13px] text-[var(--color-text-muted)]">
            <span>Suite</span>
            <select
              value={selectedSuite}
              onChange={(event) => setSelectedSuite(event.target.value)}
              className="h-8 min-w-[220px] rounded-md border bg-[var(--color-bg-secondary)] px-2 text-[13px] text-[var(--color-text)]"
              style={{ borderColor: 'var(--color-border)' }}
              title="Filter dashboard metrics by test suite"
            >
              <option value="">All suites</option>
              {suiteOptions.map((suite) => (
                <option key={suite} value={suite}>{suite}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => setShowPicker(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors"
            style={{ borderColor: 'var(--color-border)' }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
          >
            <LayoutGrid className="h-3.5 w-3.5" />
            Customize
          </button>
          <div
            className="flex items-center gap-0.5 p-0.5 rounded-md"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
          >
            {TIME_OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={clsx(
                  'px-2.5 py-1 text-[13px] font-medium tabular-nums rounded-sm transition-colors',
                  days === d
                    ? 'bg-[var(--color-bg-card)] text-[var(--color-text)] shadow-[var(--shadow-sm)]'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                )}
                onClick={() => setDays(d)}
              >
                {d === 1 ? '24h' : `${d}d`}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Why the dashboard is empty. Rendered ONLY when the window really is
          empty: a zeroed KPI row with no explanation is indistinguishable
          from a broken page, which is exactly how a 7-day window over
          16-day-old data was read as an outage. */}
      {!summaryLoading && emptyWindow.kind !== 'has-data' && (
        <div
          className="card flex flex-wrap items-center gap-x-3 gap-y-1.5 py-3 px-4"
          style={{ borderColor: 'var(--gate-conditional-border)', background: 'var(--gate-conditional-bg)' }}
          data-testid="overview-empty-window"
        >
          <Clock className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--gate-conditional)' }} />
          {emptyWindow.kind === 'no-runs-at-all' ? (
            <p className="text-sm m-0" style={{ color: 'var(--gate-conditional)' }}>
              <strong>No test runs yet</strong> for {scopeLabel}. Ingest a run and the
              dashboard fills in — widening the time window will not help.
            </p>
          ) : (
            <>
              <p className="text-sm m-0" style={{ color: 'var(--gate-conditional)' }}>
                <strong>No runs in the last {days === 1 ? '24 hours' : `${days} days`}</strong> for{' '}
                {scopeLabel}. The most recent run finished{' '}
                <strong>{formatAgeDays(emptyWindow.ageDays)}</strong>
                {emptyWindow.suggestedDays == null
                  ? ' — outside every available window.'
                  : ', outside this window.'}
              </p>
              {emptyWindow.suggestedDays != null && (
                <button
                  type="button"
                  onClick={() => setDays(emptyWindow.suggestedDays as number)}
                  className="px-2.5 py-1 text-[13px] font-medium rounded-md border transition-colors"
                  style={{ borderColor: 'var(--gate-conditional-border)', color: 'var(--gate-conditional)' }}
                >
                  Show last {emptyWindow.suggestedDays} days
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* Top row — Verdict + Workflow ribbon */}
      <div className="grid grid-cols-1 xl:[grid-template-columns:1fr_1.55fr] gap-4">
        {summaryLoading && !summary ? (
          <div className="card flex items-center justify-center min-h-[260px]">
            <LoadingSpinner />
          </div>
        ) : (
          <SectionErrorBoundary message="Failed to load release readiness">
            <VerdictCard
              verdict={verdict}
              newFailures24h={newFailures}
              newFailuresDelta={typeof newFailuresDelta === 'number' ? newFailuresDelta : 0}
              totalExecutions={totalExecutions}
              windowDays={days}
              generatedLabel={generatedLabel}
              passRate={passRate}
              passRateBasisLabel={summary?.avg_pass_rate_7d?.basis_label}
            />
          </SectionErrorBoundary>
        )}

        <SectionErrorBoundary message="Failed to load workflow ribbon">
          <div className="card" style={{ padding: '16px 18px 14px' }}>
            <div className="flex items-start justify-between gap-2.5">
              <div>
                <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Quality workflow</h3>
                <div className="flex flex-wrap items-center gap-2.5 text-[12px] text-[var(--color-text-muted)] mt-0.5">
                  <span
                    className="inline-flex items-center gap-1.5"
                    style={{ color: totalExecutions > 0 ? 'var(--status-passed)' : 'var(--color-text-muted)' }}
                  >
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ background: totalExecutions > 0 ? 'var(--status-passed)' : 'var(--color-text-faint)' }}
                      aria-hidden
                    />
                    {totalExecutions > 0 ? 'Completed' : 'Awaiting'}
                  </span>
                  <span>· {ribbonStages.length} stages</span>
                </div>
              </div>
            </div>
            <div className="mt-4 flex items-center gap-1.5 overflow-x-auto pb-1">
              {ribbonStages.map((stage, i) => (
                <div key={stage.name} className="flex items-center gap-1.5 shrink-0">
                  <StageCard stage={stage} />
                  {i < ribbonStages.length - 1 && <ChevronArrow />}
                </div>
              ))}
            </div>
          </div>
        </SectionErrorBoundary>
      </div>

      {/* KPI strip */}
      {(kpiVisible.length > 0 || hoursSaved30d != null) && (
        <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2.5">
            {activeWidgets.has('total_executions_kpi') && (
              <KpiCard
                label="Total executions"
                value={`${totalExecutions}`}
                tone="neutral"
                gradId="kpi-total"
                series={totalSeries.length >= 2 ? totalSeries : undefined}
                emptyMsg={sparklineHint(totalSeries.length)}
                delta={deltaFromMetric(summary?.total_executions_7d)}
                linkTo="/runs"
                linkLabel="View runs"
              />
            )}
            {activeWidgets.has('avg_pass_rate_kpi') && (
              <KpiCard
                label="Avg pass rate"
                value={`${Math.round(passRate)}`}
                unit="%"
                tone={passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad'}
                gradId="kpi-pass"
                series={passRateSeries.length >= 2 ? passRateSeries : undefined}
                emptyMsg={sparklineHint(passRateSeries.length)}
                delta={deltaFromMetric(summary?.avg_pass_rate_7d, true)}
              />
            )}
            {activeWidgets.has('active_defects_kpi') && (
              <KpiCard
                label="Active defects"
                value={`${activeDefects}`}
                tone={activeDefects === 0 ? 'good' : 'bad'}
                gradId="kpi-defects"
                emptyMsg={activeDefects === 0 ? `${days}d · no open defects` : `${days}d · count only`}
                delta={deltaFromMetric(summary?.active_defects)}
                linkTo="/defects"
                linkLabel="View defects"
              />
            )}
            {activeWidgets.has('flaky_tests_kpi') && (
              <KpiCard
                label="Flaky tests"
                value={`${flaky}`}
                tone={flaky === 0 ? 'warn' : 'bad'}
                gradId="kpi-flaky"
                emptyMsg={flaky === 0 ? `${days}d · no flake events captured` : `${days}d · count only`}
                delta={deltaFromMetric(summary?.flaky_test_count, true)}
                linkTo="/flaky-coach"
                linkLabel="Open flaky coach"
              />
            )}
            {activeWidgets.has('new_failures_kpi') && (
              <KpiCard
                label="New failures · 24h"
                value={`${newFailures}`}
                tone={newFailures === 0 ? 'good' : 'bad'}
                gradId="kpi-failures"
                series={failedSeries.length >= 2 ? failedSeries : undefined}
                emptyMsg={sparklineHint(failedSeries.length)}
                delta={deltaFromMetric(summary?.new_failures_24h)}
                linkTo="/failures"
                linkLabel="View failures"
              />
            )}
            {activeWidgets.has('infra_failures_kpi') && (
              <KpiCard
                label="Infra-caused failures"
                value={infraPct == null ? '—' : `${infraPct}`}
                unit={infraPct == null ? undefined : '%'}
                tone={infraPct == null ? 'neutral' : infraPct === 0 ? 'good' : infraPct >= 30 ? 'bad' : 'warn'}
                gradId="kpi-infra-kind"
                emptyMsg={infraPct == null
                  ? `${days}d · no analyzed failures`
                  : `AI-classified · ${infraKindCount} of ${kindTotal} failure${kindTotal === 1 ? '' : 's'}`}
                linkTo="/failures"
                linkLabel="View failures"
              />
            )}
            {activeWidgets.has('avg_duration_kpi') && (
              <KpiCard
                label="Avg run duration"
                value={avgDurationMs ? formatDuration(avgDurationMs) : '—'}
                tone="neutral"
                gradId="kpi-duration"
                emptyMsg={avgDurationMs ? `${days}d · avg only` : 'Needs ≥ 3 timed runs'}
              />
            )}
            {/* US-12.2 — Eng-hours saved. Only rendered when the hours-saved
                model reports available=true; otherwise omitted (no dash-card). */}
            {hoursSaved30d != null && valueMetrics && (
              <KpiCard
                label="Eng-hours saved"
                value={formatHoursSaved(hoursSaved30d)}
                unit="h"
                tone="good"
                gradId="kpi-hours-saved"
                emptyMsg={`30d · ≈ ${valueMetrics.headline.fte_equivalent_30d.toFixed(1)} FTE · estimated`}
                linkTo="/value-metrics"
                linkLabel="View value metrics"
              />
            )}
        </div>
      )}

      {/* Bottom row — Trend chart + Blockers */}
      <div className="grid grid-cols-1 xl:[grid-template-columns:1.6fr_1fr] gap-4">
        <SectionErrorBoundary message="Failed to load execution trend">
          <div className="card" style={{ padding: '14px 18px 18px' }}>
            <div className="flex items-start justify-between gap-2.5 flex-wrap">
              <div>
                <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Execution trend</h3>
                <p className="text-[11px] text-[var(--color-text-muted)] m-0 mt-0.5">
                  Pass / fail / skip over the last {days} days
                </p>
              </div>
              <div className="flex items-center gap-3.5 text-[11px] text-[var(--color-text-muted)]">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: CHART_COLORS.passed }} />Passed
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: CHART_COLORS.failed }} />Failed
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: CHART_COLORS.skipped }} />Skipped
                </span>
              </div>
            </div>
            <div className="mt-3">
              {trendsLoading ? (
                <div className="flex items-center justify-center" style={{ height: 240 }}>
                  <LoadingSpinner />
                </div>
              ) : (
                <ExecutionTrendChart trends={trendData} days={days} />
              )}
            </div>
          </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary message="Failed to load blockers">
          <BlockersPanel newFailures={newFailures} hasData={totalExecutions > 0} verdict={verdict} />
        </SectionErrorBoundary>
      </div>

      {/* Coverage micro-strip */}
      <SectionErrorBoundary message="Failed to load coverage strip">
        <MicroStrip summary={summary} days={days} />
      </SectionErrorBoundary>

      {/* Pending banner — kept for users on legacy widget layouts that disabled
          the new sections; mirrors the verdict-card empty state in plain text. */}
      {totalExecutions === 0 && (
        <div className="flex items-center gap-3 px-4 py-2.5 rounded-md text-[12px] text-[var(--color-text-muted)] border border-[var(--color-border)]">
          <HelpCircle className="h-4 w-4" />
          <span>
            No test executions{selectedSuite ? ` for ${selectedSuite}` : ''} in the last {days} days — readiness, KPIs, and blockers will assess once data lands.
          </span>
        </div>
      )}

      {showPicker && (
        <WidgetPicker
          page="dashboard"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
