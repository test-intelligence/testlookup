/**
 * Trends — verdict-led redesign per design_handoff_trends/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb (project · window · refreshed) + Customize +
 *             7d/14d/30d/90d window picker + Export PDF + "Email report" CTA.
 *   Verdict → 1.45fr | 1fr split. Variants HEALTHY / MIXED / INSUFFICIENT
 *             / DECLINING / PENDING. Left: pulsing eyebrow → 26 px headline
 *             "<verdict> · <summary>" → lede → 3 issue rows → CTAs. Right:
 *             44 px trend-confidence score with the kit's marker `GaugeBar`
 *             (red→amber→green track, ticks at 33 / 66) + 2×2 weighted
 *             dimension grid (Data coverage 40 % / Sample size 25 % /
 *             Variance stability 20 % / Tag quality 15 %).
 *   Ribbon  → slim 3-stage workflow (Trend capture / Signal comparison /
 *             Report delivery). Click → drawer (Phase 2).
 *   KPIs    → 5 cells: Pass rate · Days with runs · Executions · Suites ·
 *             Last run. Pass rate and Executions carry a kit `Sparkline` of
 *             the window's real per-day series (a day without runs is a
 *             break in the line); the other three had only decoration and
 *             carry no glyph (VIZ-104, OD-1).
 *   Body    → 1.65fr | 1fr.
 *     Left  → Run cadence (a kit `DayStrip`, presence mode: one cell per day,
 *             failing days striped, today and the gap edge marked, with an
 *             amber gap-annotation strip) → Daily breakdown (kit
 *             `StackedColumnChartFrame`: one column per day by status, a day
 *             without runs a stated gap) → Pass-rate trend (kit
 *             `TimeSeriesChartFrame` with the 90 % target line, the trend
 *             overlays, the range brush and release markers).
 *     Right → Schedule-paused callout (only when the gap is real) → Suite
 *             pass rates → Recommended actions (role-routed; Idle chip when a
 *             role has no work).
 *   Footer → Provenance line + decision-trail link.
 *
 * Narrow viewports (VIZ-106, Wave 2.6): below 1024 px the verdict card and
 * the body grid are one column, so the page needs no "wider screen" notice.
 *
 * Catalogue (VIZ-408, Wave 2.6): a lazy section below the body grid adds the
 * suite comparison, the duration band and the suite x day heatmap (a read the
 * section makes itself). Since Phase D (S3/S4) the page asks no chart flag:
 * the catalogue, the heatmap and the pass-rate trend's analysis variant
 * always render.
 *
 * Out of scope (Phase 2 — README §"Out of Scope"):
 *   - Workflow stage drawer body
 *   - Decision-trail modal body
 *   - Email-report compose modal
 *   - 90-day heatmap wrap rules
 *   - Cumulative-volume chart (intentionally removed — see README)
 *   - Print styles for Export PDF
 *
 * Data: derives every section from existing useTrendData + useDashboardSummary
 * + useCoverage + useFlakyTests. The README proposes new dedicated trends /
 * cadence / suites endpoints — none exist yet, so v1 reads from the existing
 * trend tail and synthesises the remaining signals (variance, gap detection,
 * suite micro-history) deterministically. Schedule-resume / email-report /
 * export-PDF emit toasts pending the new endpoints.
 */
import { Suspense, useEffect, useMemo, useState } from 'react'
import { useNow } from '@/hooks/useNow'
import { Link } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, ArrowRight, BarChart3, Calendar, ChevronRight,
  Clock, Download, Layers, LayoutGrid, Mail, Search, ShieldCheck, TrendingUp,
  XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useRuns } from '@/hooks/useRuns'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { shortAgo } from '@/utils/formatters'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import {
  useCoverage, useDashboardSummary, useFlakyTests, useTrendData,
} from '@/hooks/useMetrics'
import {
  daysBetweenDayIso, formatDayIso, relativeDayLabel, shiftDayIso, utcDayIso,
} from '@/utils/calendarDay'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/useSuiteScope'
import { PageSuiteTargetContext, usePageSuiteTarget } from '@/hooks/pageSuiteTarget'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { scopeArg } from '@/lib/scopeParams'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import DayStrip from '@/components/charts/DayStrip'
import { countTones, type DayStripCell } from '@/components/charts/dayStrip.model'
import GaugeBar, { type GaugeTone } from '@/components/charts/GaugeBar'
import Sparkline from '@/components/charts/Sparkline'
import StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import { buildStackedColumnModel, STATUS_STACK_SERIES, utcDayLabel } from '@/components/charts/stackedColumnModel'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import { buildTimeSeriesModel, timeSeriesFromTrends } from '@/components/charts/timeSeriesModel'
import { readyState, type ChartState } from '@/components/charts/chartStateCore'
import type { ReleaseInput, TimeSeriesPoint } from '@/components/charts/timeSeriesModel'
import { useReleases } from '@/hooks/useReleases'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import type { CoverageSuite } from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours (rendered as "24h"); the rest are day counts.
const WINDOWS = [1, 7, 14, 30, 90] as const
type Window = (typeof WINDOWS)[number]
/** The window /trends opens on, whatever was last picked elsewhere (see the page). */
const TRENDS_DEFAULT_WINDOW: Window = 14

/**
 * The catalogue sections (VIZ-408): a lazy chunk, requested when the page
 * draws its body. A stale chunk (a tab opened before a deploy) reloads the
 * page once, like a route's.
 */
const TrendsCatalogue = lazyWithRetry(() => import('@/components/reports/catalogue/TrendsCatalogue'))

// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'HEALTHY' | 'MIXED' | 'INSUFFICIENT' | 'DECLINING' | 'PENDING'

interface VerdictTheme {
  border: string
  glow: string
  bar: string
  eyebrowText: string
  gateText: string
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
  pulse: boolean
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  HEALTHY: {
    border: 'color-mix(in srgb, var(--status-passed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrowText: 'var(--status-passed)',
    gateText:    'var(--status-passed)',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    label:  'Trend healthy',
    pulse:  false,
  },
  MIXED: {
    border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText:    'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Trend mixed',
    pulse:  true,
  },
  INSUFFICIENT: {
    border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText:    'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Insufficient data',
    pulse:  true,
  },
  DECLINING: {
    border: 'color-mix(in srgb, var(--status-failed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: 'var(--status-failed)',
    gateText:    'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Trend declining',
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
    meter:  'var(--color-text-muted)',
    label:  'Pending',
    pulse:  false,
  },
}

// ── Confidence model ───────────────────────────────────────────────────────
// Weights from README §5.4: Data coverage 40 / Sample size 25 / Variance 20 /
// Tag quality 15.
type DimensionId = 'data_coverage' | 'sample_size' | 'variance_stability' | 'tag_quality'
const WEIGHTS: Record<DimensionId, number> = {
  data_coverage:      0.40,
  sample_size:        0.25,
  variance_stability: 0.20,
  tag_quality:        0.15,
}

interface DimensionScore {
  id: DimensionId
  label: string
  score: number
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

interface CadenceCell {
  iso: string
  /** Test executions that landed on this day — not the number of runs. */
  executions: number
  passed: number
  failed: number
  broken: number
  isToday: boolean
  isLastBeforeGap: boolean
}

interface ConfidenceModel {
  composite: number
  dimensions: DimensionScore[]
  daysWithRuns: number
  windowDays: number
  emptyDays: number
  totalExecutions: number
  passedExecutions: number
  failedExecutions: number
  skippedExecutions: number
  brokenExecutions: number
  /** passed + failed + broken — skips are never evaluated, so they are in
   *  neither numerator nor denominator. Matches the backend definition. */
  evaluatedExecutions: number
  passRate: number
  passRatePerDay: number[]   // only days with runs
  cadenceCells: CadenceCell[]
  lastRunIso: string | null
  previousRunIso: string | null
  silentDays: number
  expectedRunsMissed: number
  gapStart: string | null
  gapEnd: string | null
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 33) return 'warn'
  return 'bad'
}

function buildCadenceCells(trend: TrendPoint[], days: number): CadenceCell[] {
  const byDate = new Map<string, TrendPoint>()
  for (const p of trend) byDate.set(p.date.slice(0, 10), p)
  const todayIso = utcDayIso()
  const cells: CadenceCell[] = []
  for (let i = days - 1; i >= 0; i--) {
    const iso = shiftDayIso(todayIso, -i)
    const p = byDate.get(iso)
    const executions = p ? (p.passed + p.failed + p.skipped + (p.broken ?? 0)) : 0
    cells.push({
      iso,
      executions,
      passed: p?.passed ?? 0,
      failed: p?.failed ?? 0,
      broken: p?.broken ?? 0,
      isToday: i === 0,
      isLastBeforeGap: false,
    })
  }
  // Mark the last active cell that has at least one empty cell *after* it.
  let lastWithRunsIdx = -1
  for (let i = 0; i < cells.length; i++) {
    if (cells[i].executions > 0) lastWithRunsIdx = i
  }
  if (lastWithRunsIdx >= 0 && lastWithRunsIdx < cells.length - 1) {
    // Find the most recent active cell that is followed by a stretch of empty
    // cells before the next active cell — that's the "last green before gap".
    for (let i = cells.length - 1; i >= 0; i--) {
      if (cells[i].executions > 0 && !cells[i].isToday) {
        cells[i].isLastBeforeGap = true
        break
      }
    }
  }
  return cells
}

function computeConfidenceModel(trend: TrendPoint[], days: number, untaggedShare: number): ConfidenceModel {
  const cadenceCells = buildCadenceCells(trend, days)
  const activeCells = cadenceCells.filter(c => c.executions > 0)
  const daysWithRuns = activeCells.length
  const emptyDays = cadenceCells.length - daysWithRuns

  const totalExecutions   = trend.reduce((s, p) => s + p.passed + p.failed + p.skipped + (p.broken ?? 0), 0)
  const passedExecutions  = trend.reduce((s, p) => s + p.passed,  0)
  const failedExecutions  = trend.reduce((s, p) => s + p.failed,  0)
  const skippedExecutions = trend.reduce((s, p) => s + p.skipped, 0)
  const brokenExecutions  = trend.reduce((s, p) => s + (p.broken ?? 0), 0)
  // Skips are excluded from both halves of the ratio — a skipped test was never
  // evaluated. This is the definition the backend already publishes as
  // TrendPoint.pass_rate and that /overview renders; computing a *different*
  // one here is what made this page read 51.7% for the window /overview called
  // 57.4%. See backend/app/services/metrics_service.py for the canonical note.
  const evaluatedExecutions = passedExecutions + failedExecutions + brokenExecutions
  const passRate = evaluatedExecutions > 0 ? (passedExecutions / evaluatedExecutions) * 100 : 0

  const passRatePerDay = activeCells.map(c => {
    // Same denominator as the headline — broken counts against the day.
    const evaluated = c.passed + c.failed + c.broken
    return evaluated > 0 ? (c.passed / evaluated) * 100 : 0
  })

  // Variance stability — stddev of per-day pass rate over active days.
  // 0 active days → 0 (undefined). 1 day → 45 (warn proxy per the demo).
  // ≥ 2 days → 100 - normalised stddev (cap at 100, floor at 0).
  let varianceStability: number
  if (activeCells.length === 0)      varianceStability = 0
  else if (activeCells.length === 1) varianceStability = 45
  else {
    const mean = passRatePerDay.reduce((s, x) => s + x, 0) / passRatePerDay.length
    const variance = passRatePerDay.reduce((s, x) => s + (x - mean) ** 2, 0) / passRatePerDay.length
    const sd = Math.sqrt(variance)
    // Map 0pp std → 100, 30pp std → 0 (linear)
    varianceStability = Math.max(0, Math.min(100, 100 - (sd / 30) * 100))
  }

  const dataCoverageScore = days > 0 ? Math.min(100, (daysWithRuns / days) * 100) : 0
  const sampleSizeScore   = Math.min(100, (totalExecutions / 100) * 100)  // 100 executions ≈ full
  const tagQualityScore   = Math.max(0, 100 - untaggedShare * 100)

  const dimensions: DimensionScore[] = [
    { id: 'data_coverage',      label: 'Data coverage',     score: dataCoverageScore,   weight: WEIGHTS.data_coverage,      tone: toneFor(dataCoverageScore) },
    { id: 'sample_size',        label: 'Sample size',       score: sampleSizeScore,     weight: WEIGHTS.sample_size,        tone: toneFor(sampleSizeScore) },
    { id: 'variance_stability', label: 'Variance stability', score: varianceStability,  weight: WEIGHTS.variance_stability, tone: toneFor(varianceStability) },
    { id: 'tag_quality',        label: 'Tag quality',        score: tagQualityScore,    weight: WEIGHTS.tag_quality,        tone: toneFor(tagQualityScore) },
  ]
  const composite = Math.round(dimensions.reduce((sum, d) => sum + d.score * d.weight, 0))

  // Last/previous run + gap stats
  const isos = activeCells.map(c => c.iso).sort()
  const lastRunIso     = isos.length > 0 ? isos[isos.length - 1] : null
  const previousRunIso = isos.length > 1 ? isos[isos.length - 2] : null

  let silentDays = 0
  let expectedRunsMissed = 0
  let gapStart: string | null = null
  let gapEnd:   string | null = null
  if (daysWithRuns < days && daysWithRuns > 0) {
    // Look for the longest gap in the window.
    let maxGap = 0
    let curGapStartIdx = -1
    let curGapLen = 0
    for (let i = 0; i < cadenceCells.length; i++) {
      if (cadenceCells[i].executions === 0) {
        if (curGapStartIdx === -1) curGapStartIdx = i
        curGapLen++
      } else {
        if (curGapLen > maxGap) {
          maxGap = curGapLen
          gapStart = curGapStartIdx >= 0 ? cadenceCells[curGapStartIdx].iso : null
          gapEnd   = i > 0 ? cadenceCells[i - 1].iso : null
        }
        curGapStartIdx = -1
        curGapLen = 0
      }
    }
    if (curGapLen > maxGap) {
      maxGap = curGapLen
      gapStart = curGapStartIdx >= 0 ? cadenceCells[curGapStartIdx].iso : null
      gapEnd   = cadenceCells[cadenceCells.length - 1].iso
    }
    silentDays = maxGap
    expectedRunsMissed = silentDays  // assume nightly schedule (1/day)
  }

  return {
    composite, dimensions,
    daysWithRuns, windowDays: days, emptyDays,
    totalExecutions, passedExecutions, failedExecutions, skippedExecutions, brokenExecutions,
    evaluatedExecutions,
    passRate, passRatePerDay,
    cadenceCells,
    lastRunIso, previousRunIso,
    silentDays, expectedRunsMissed,
    gapStart, gapEnd,
  }
}

function pickVerdict(model: ConfidenceModel): Verdict {
  if (model.totalExecutions === 0)        return 'PENDING'
  if (model.composite < 33)               return 'INSUFFICIENT'
  // We need ≥ 3 active days to compute trend direction (per README issue 2).
  if (model.daysWithRuns < 3)             return 'INSUFFICIENT'
  // Compare first half pass rate to second half — declining if drop > 10 pp.
  const half = Math.max(1, Math.floor(model.passRatePerDay.length / 2))
  const recent = model.passRatePerDay.slice(-half).reduce((s, x) => s + x, 0) / half
  const prior  = model.passRatePerDay.slice(0, half).reduce((s, x) => s + x, 0) / half
  if (recent < prior - 10) return 'DECLINING'
  if (model.composite >= 66) return 'HEALTHY'
  return 'MIXED'
}

// ── Atoms ──────────────────────────────────────────────────────────────────
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
  children, onClick, title, asChildLink,
}: { children: React.ReactNode; onClick?: () => void; title?: string; asChildLink?: string }) {
  const cls = 'inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors'
  const style: React.CSSProperties = { background: 'var(--color-btn-primary-bg)', color: 'white' }
  const hoverIn  = (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')
  const hoverOut = (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')
  if (asChildLink) {
    return <Link to={asChildLink} className={cls} style={style} title={title} onMouseEnter={hoverIn} onMouseLeave={hoverOut}>{children}</Link>
  }
  return <button type="button" onClick={onClick} title={title} className={cls} style={style} onMouseEnter={hoverIn} onMouseLeave={hoverOut}>{children}</button>
}

function WindowPicker({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Time window"
      className="flex items-center gap-0 p-0.5 rounded-md"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      {WINDOWS.map((w) => {
        const active = value === w
        return (
          <button
            key={w}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(w)}
            className={clsx(
              'px-3 py-1 text-[13px] font-medium tabular-nums rounded-sm transition-colors',
              active
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text)] shadow-[var(--shadow-sm)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            {w === 1 ? '24h' : `${w}d`}
          </button>
        )
      })}
    </div>
  )
}

// ── Verdict card ──────────────────────────────────────────────────────────
interface IssueRowSpec {
  tone: 'bad' | 'warn' | 'info'
  Icon: typeof XCircle
  body: React.ReactNode
  cta?: { label: string; onClick?: () => void; to?: string }
}

function VerdictCard({
  model, verdict, summary, lede, issues, ctas,
}: {
  model: ConfidenceModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
  issues: IssueRowSpec[]
  ctas: { primary?: IssueRowSpec['cta']; secondary: IssueRowSpec['cta'][] }
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Trend verdict"
      aria-live="polite"
      // One column below 1024 px (VIZ-106); from lg the design's 1.45fr | 1fr.
      className="relative rounded-xl border overflow-hidden grid gap-6 grid-cols-1 lg:grid-cols-[1.45fr_1fr]"
      style={{
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
          Trend signal
        </span>
        <h2 className="font-bold m-0" style={{ fontSize: 'var(--text-display-sm)', lineHeight: 1.15, letterSpacing: '-0.02em', margin: '6px 0 6px' }}>
          <span aria-label={`Verdict: ${t.label}`} style={{ color: t.gateText }}>{t.label}</span>
          <span className="text-[var(--color-text-muted)] mx-2">·</span>
          <span>{summary}</span>
        </h2>
        <p className="text-[13px] m-0 mb-3.5 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
          {lede}
        </p>

        <div className="flex flex-col gap-2">
          {issues.length === 0
            ? <p className="text-[12.5px] text-[var(--color-text-muted)] m-0">No outstanding issues for this window.</p>
            : issues.map((iss, i) => <IssueRow key={i} issue={iss} />)
          }
        </div>

        <div className="flex flex-wrap gap-2 mt-3.5">
          {ctas.primary && <CtaBtn cta={ctas.primary} primary />}
          {ctas.secondary.filter((x): x is IssueRowSpec['cta'] => Boolean(x)).map((c, i) => <CtaBtn key={i} cta={c} />)}
        </div>
      </div>

      <div className="flex flex-col gap-3.5 pt-0.5 min-w-0">
        <ConfidenceMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
    </section>
  )
}

function CtaBtn({ cta, primary }: { cta: IssueRowSpec['cta']; primary?: boolean }) {
  if (!cta) return null
  if (primary) {
    return cta.to
      ? <PrimaryBtn asChildLink={cta.to}>{cta.label}</PrimaryBtn>
      : <PrimaryBtn onClick={cta.onClick}>{cta.label}</PrimaryBtn>
  }
  return cta.to
    ? <GhostBtn asChildLink={cta.to}>{cta.label}</GhostBtn>
    : <GhostBtn onClick={cta.onClick}>{cta.label}</GhostBtn>
}

function IssueRow({ issue }: { issue: IssueRowSpec }) {
  const palette = {
    bad:  { bg: 'color-mix(in srgb, var(--status-failed) 8%, transparent)',          bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',  icBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',  icFg: 'var(--status-failed)' },
    warn: { bg: 'var(--gate-conditional-bg-soft)', bd: 'var(--gate-conditional-border)', icBg: 'var(--gate-conditional-bg)', icFg: 'var(--status-broken)' },
    info: { bg: 'var(--color-accent-bg-soft)',   bd: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', icBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', icFg: 'var(--color-accent)' },
  }[issue.tone]
  const Icon = issue.Icon
  return (
    <div
      className="grid gap-2.5 items-center rounded-md border"
      style={{ gridTemplateColumns: '22px 1fr auto', padding: '10px 12px', background: palette.bg, borderColor: palette.bd }}
    >
      <span className="inline-flex items-center justify-center rounded-md" style={{ width: 22, height: 22, background: palette.icBg, color: palette.icFg }}>
        <Icon className="h-3 w-3" />
      </span>
      <div className="text-[13px] text-[var(--color-text)] leading-[1.4] issue-body">{issue.body}</div>
      {issue.cta && (
        issue.cta.to
          ? <Link to={issue.cta.to} className="text-[11.5px] font-medium px-2 py-0.5 rounded-full border whitespace-nowrap"
              style={{ color: 'var(--color-accent)', borderColor: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', background: 'var(--color-accent-bg-soft)' }}>
              {issue.cta.label} →
            </Link>
          : <button type="button" onClick={issue.cta.onClick} className="text-[11.5px] font-medium px-2 py-0.5 rounded-full border whitespace-nowrap transition-colors"
              style={{ color: 'var(--color-accent)', borderColor: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', background: 'var(--color-accent-bg-soft)' }}>
              {issue.cta.label} →
            </button>
      )}
    </div>
  )
}

// The verdict's colour for the meter's ring; the pill beside it says the band in words.
const VERDICT_GAUGE_TONE: Record<Verdict, GaugeTone> = {
  HEALTHY: 'good',
  MIXED: 'warn',
  INSUFFICIENT: 'warn',
  DECLINING: 'bad',
  PENDING: 'neutral',
}

// The same band edges the pill reads (33 / 66), as real text under the track.
const CONFIDENCE_TICKS = [
  { value: 0, label: 'Low' },
  { value: 33, label: 'Moderate' },
  { value: 66, label: 'High' },
  { value: 100 },
] as const

function ConfidenceMeter({ model, verdict }: { model: ConfidenceModel; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  const pillLabel = score >= 66 ? 'High' : score >= 33 ? 'Moderate' : 'Low'
  return (
    <div>
      <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Trend confidence
      </div>
      <div className="flex items-end justify-between">
        <div>
          <span className="font-bold tabular-nums leading-none" style={{ fontSize: 'var(--text-display-lg)', color: t.meter, letterSpacing: '-0.02em' }}>
            {verdict === 'PENDING' ? '—' : score}
          </span>
          <span className="text-[13px] text-[var(--color-text-muted)] ml-1">/ 100</span>
        </div>
        <span
          className="inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold"
          style={{ background: t.pillBg, border: `1px solid ${t.pillBd}`, color: t.pillFg }}
        >
          {pillLabel}
        </span>
      </div>
      {/* The kit's marker meter (VIZ-104 G1): the whole track is the
          red-to-green scale and a ring marks the score. A PENDING verdict has
          no score, so the track is empty rather than marked at 0. */}
      <GaugeBar
        className="mt-3"
        variant="marker"
        gradient="health"
        tone={VERDICT_GAUGE_TONE[verdict]}
        value={verdict === 'PENDING' ? null : score}
        label="Trend confidence"
        valueText={verdict === 'PENDING' ? undefined : `${score} of 100, ${pillLabel}`}
        ticks={CONFIDENCE_TICKS}
      />
    </div>
  )
}

function DimensionGrid({ dimensions }: { dimensions: DimensionScore[] }) {
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
      {dimensions.map(d => <DimensionTile key={d.id} dim={d} />)}
    </div>
  )
}

function DimensionTile({ dim }: { dim: DimensionScore }) {
  const valueColor = dim.tone === 'bad' ? 'var(--status-failed)' : dim.tone === 'warn' ? 'var(--status-broken)' : 'var(--status-passed)'
  const barColor   = dim.tone === 'bad' ? 'var(--status-failed)' : dim.tone === 'warn' ? 'var(--status-broken)' : 'var(--status-passed)'
  return (
    <div
      className="rounded-sm px-2.5 py-2 border"
      style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex justify-between"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        <span>{dim.label}</span>
        <span className="text-[var(--color-text-faint)] font-medium">{Math.round(dim.weight * 100)}%</span>
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <span className="text-[14px] font-semibold tabular-nums min-w-[40px]" style={{ color: valueColor }}>
          {Math.round(dim.score)}
        </span>
        <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
          <i className="block h-full rounded-full" style={{ width: `${dim.score}%`, background: barColor }} />
        </div>
      </div>
    </div>
  )
}

// ── Workflow ribbon (slim, 3-stage) ───────────────────────────────────────
interface RibbonStage { num: number; name: string; evidence: number; confidencePct: number }

const TRENDS_STAGES: RibbonStage[] = [
  { num: 1, name: 'Trend capture',     evidence: 1, confidencePct: 95 },
  { num: 2, name: 'Signal comparison', evidence: 5, confidencePct: 88 },
  { num: 3, name: 'Report delivery',   evidence: 1, confidencePct: 90 },
]

function TrendsRibbon({ totalEvidence, confidencePct }: { totalEvidence: number; confidencePct: number }) {
  return (
    <section
      aria-label="Trends workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Trends workflow · last analysis</h3>
        <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
          <span className="h-1.5 w-1.5 rounded-full inline-block" style={{ background: 'var(--status-passed)' }} />
          Completed · {TRENDS_STAGES.length} stages · {totalEvidence} evidence items · {confidencePct}% confidence
        </div>
      </div>
      {/* One step per row below lg (VIZ-106, R2-4): three to a 375 px row drew
          the ordinals over the titles and cut the counts to "1 e…", and at 640
          a third of the row still cut every count. From lg (the desktop shell)
          up, the three columns it always was. */}
      <div className="grid grid-cols-1 lg:grid-cols-3">
        {TRENDS_STAGES.map((s, i) => <TrendStageCell key={s.num} stage={s} isLast={i === TRENDS_STAGES.length - 1} />)}
      </div>
    </section>
  )
}

function TrendStageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}, done, ${stage.evidence} evidence, ${stage.confidencePct}% confidence`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className={clsx(
        'relative flex items-center gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]',
        // The column divider, only where there are columns.
        !isLast && 'lg:border-r lg:border-solid lg:border-[var(--color-border)]',
      )}
      style={{ padding: '8px 12px', textAlign: 'left' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none"
        style={{ width: 18, height: 18, background: 'var(--status-passed-soft)', color: 'var(--status-passed)' }}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 12l5 5L20 7" />
        </svg>
      </span>
      <span className="flex flex-col gap-px min-w-0">
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] leading-[1.2]">{stage.name}</span>
        <span className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums truncate">
          {stage.evidence} evidence
          <span className="mx-1 text-[var(--color-text-faint)]">·</span>
          <span className="font-semibold" style={{ color: 'var(--status-passed)' }}>{stage.confidencePct}% confidence</span>
        </span>
      </span>
      <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-faint)] self-start pt-0.5">
        {String(stage.num).padStart(2, '0')}
      </span>
      <span className="absolute left-0 right-0 bottom-0" style={{ height: 2, background: 'var(--status-passed)', opacity: 0.7 }} />
    </button>
  )
}

// ── KPI sparklines ────────────────────────────────────────────────────────
const PERCENT_DOMAIN = [0, 100] as const
const formatPercent = (v: number) => `${v.toFixed(1)}%`
/** A count's sparkline scale: from zero to the tallest day (at least 1, so all-zero is not a 0/0 scale). */
function countDomain(series: readonly (number | null)[]): readonly [number, number] {
  return [0, Math.max(1, ...series.filter((v): v is number => v !== null))]
}

/**
 * The per-day series behind the Pass rate and Executions sparklines, oldest
 * first, one entry per day of the window.
 *
 * A day with no runs has no pass rate: `null`, a break in the line, never 0,
 * because a quiet day is not a 0 % day. The rate uses the headline's
 * denominator (passed + failed + broken; skips are outside it), so a day of
 * only skips is a break too.
 *
 * Executions are a COUNT, and a day with no runs ran 0 of them: a measured
 * zero, the one meaning a quiet day has on Overview, SuiteDetail and the
 * daily breakdown below (R2 F3). A line of those zeros around a single day
 * that ran is not a trend, so below two such days the series stays
 * unmeasured and the sparkline draws nothing, as before.
 */
function kpiSparkSeries(cells: readonly CadenceCell[]): { passRate: (number | null)[]; executions: (number | null)[] } {
  const daysWithRuns = cells.filter((c) => c.executions > 0).length
  return {
    passRate: cells.map((c) => {
      const evaluated = c.passed + c.failed + c.broken
      return evaluated > 0 ? (c.passed / evaluated) * 100 : null
    }),
    executions: cells.map((c) => (daysWithRuns >= 2 ? c.executions : null)),
  }
}

// ── KPI strip ─────────────────────────────────────────────────────────────
type KpiTone = 'good' | 'warn' | 'bad' | 'accent' | 'neutral'

function KpiCell({
  Icon, label, value, meta, tone = 'neutral', spark, isFirst, isLast,
}: {
  Icon?: typeof TrendingUp
  label: string
  value: React.ReactNode
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
      </div>
      {meta && <div className="text-[10.5px] text-[var(--color-text-muted)]">{meta}</div>}
      {/* Pinned to the cell's bottom: when a meta line wraps (DejaVu on
          Linux is wider), the sparklines in one row still line up. */}
      {spark && <div className="mt-auto pt-1.5">{spark}</div>}
    </div>
  )
}

// ── Run cadence heatmap ───────────────────────────────────────────────────
/**
 * One cadence day as a `DayStrip` cell (presence mode). A day whose runs
 * include a failed OR broken execution is `mixed` — broken counts against the
 * day exactly as it counts against the headline pass rate — and keeps the
 * 80/20 mixed-day fill, now with the strip's striped failure band beside the
 * colour. This is the deliberate difference from Coverage's cadence strip,
 * which shows volume only and never marks a failure.
 */
function cadenceStripCell(c: CadenceCell): DayStripCell {
  const hasRuns = c.executions > 0
  const failing = [c.failed > 0 ? `${c.failed} failed` : null, c.broken > 0 ? `${c.broken} broken` : null].filter(Boolean)
  return {
    key: c.iso,
    label: `${c.iso} · ${c.executions} execution${c.executions === 1 ? '' : 's'}${failing.length > 0 ? ` (${failing.join(', ')})` : ''}`,
    tone: !hasRuns ? 'none' : failing.length > 0 ? 'mixed' : 'pass',
    marker: c.isToday ? 'today' : c.isLastBeforeGap ? 'gap-edge' : undefined,
  }
}

function CadenceHeatmap({ model }: { model: ConfidenceModel }) {
  const cells = useMemo(() => model.cadenceCells.map(cadenceStripCell), [model.cadenceCells])
  const counts = countTones(cells)
  const activeCount = cells.length - counts.none
  const emptyCount = counts.none
  const failingCount = counts.mixed

  return (
    <CardShell title={`Run cadence — last ${model.windowDays} days`} rightSlot={<span>{activeCount} day{activeCount === 1 ? '' : 's'} with runs · {emptyCount} empty</span>}>
      <div className="px-4 pt-3 pb-4">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
          Each cell is one day. Green = had executions; a striped band = some of them failed. Empty cells mean no runs landed — the schedule, the runner, or someone with a manual trigger has been quiet.
        </p>
        <DayStrip
          mode="presence"
          cells={cells}
          gap={4}
          title="Run cadence"
          label={`Run cadence: ${emptyCount} empty days, ${activeCount} day${activeCount === 1 ? '' : 's'} with executions, ${failingCount} with failures`}
        />

        {model.silentDays >= 7 && model.gapStart && model.gapEnd && (
          <div
            className="mt-3 grid items-center gap-2.5 rounded-md text-[12px]"
            style={{
              gridTemplateColumns: 'auto 1fr',
              padding: '10px 12px',
              background: 'var(--gate-conditional-bg-soft)',
              border: '1px solid var(--gate-conditional-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <span
              className="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10.5px] font-semibold uppercase"
              style={{ background: 'var(--gate-conditional-bg)', color: 'var(--status-broken)', letterSpacing: 'var(--tracking-wide)' }}
            >
              Gap
            </span>
            <span>
              {model.silentDays}-day silence between <code className="font-mono text-[11px]">{shortDate(model.gapStart)}</code> and <code className="font-mono text-[11px]">{shortDate(model.gapEnd)}</code>.
              {' '}If you intended this (e.g. release freeze), pin a note. If not, the scheduler is paused.
            </span>
          </div>
        )}
      </div>
    </CardShell>
  )
}

function shortDate(iso: string): string {
  return formatDayIso(iso)
}

// ── Daily breakdown ───────────────────────────────────────────────────────
// The kit's four statuses in its one stack order (Passed, Failed, Broken,
// Skipped), as Overview and SuiteDetail stack them (R2 F4). Skipped and Broken
// are two statuses with two colours and two decals — the hand-drawn version
// painted both amber.

/** The window's days as trend points keyed by UTC day (the payload's dates may carry a time). */
function trendByDay(trend: readonly TrendPoint[]): Map<string, TrendPoint> {
  const byDate = new Map<string, TrendPoint>()
  for (const p of trend) byDate.set(p.date.slice(0, 10), p)
  return byDate
}

function DailyBreakdown({
  trend, days, model, filtersApplied,
}: { trend: TrendPoint[]; days: number; model: ConfidenceModel; filtersApplied: boolean }) {
  const { chart, state, takeaway } = useMemo(() => {
    const byDate = trendByDay(trend)
    const totals = { passed: 0, failed: 0, skipped: 0, broken: 0 }
    // One column per day of the window, aligned with the cadence strip. A day
    // the payload never sent had no runs: it ran nothing, a MEASURED zero (a
    // tick on the baseline), which is what the cadence strip beside it calls
    // "No runs" and what Overview and SuiteDetail draw for the same day. It
    // is never "no measured value" (R2 F3).
    const buckets = model.cadenceCells.map((c) => {
      const p = byDate.get(c.iso)
      if (p) {
        totals.passed += p.passed
        totals.failed += p.failed
        totals.skipped += p.skipped
        totals.broken += p.broken ?? 0
      }
      return {
        key: c.iso,
        // The kit's UTC day label: one day format for every chart on the page (R2 F5).
        label: utcDayLabel(c.iso),
        values: p
          ? { passed: p.passed, failed: p.failed, skipped: p.skipped, broken: p.broken ?? 0 }
          : { passed: 0, failed: 0, skipped: 0, broken: 0 },
      }
    })
    const built = buildStackedColumnModel({
      buckets,
      series: STATUS_STACK_SERIES,
      valueTitle: 'Executions',
      bucketTitle: 'Day (UTC)',
      xType: 'time',
    })
    return {
      chart: built,
      state: readyState(built),
      takeaway: `${totals.passed} passed · ${totals.failed} failed · ${totals.broken} broken · ${totals.skipped} skipped over the last ${days} days`,
    }
  }, [trend, days, model.cadenceCells])

  return (
    <StackedColumnChartFrame
      title="Daily breakdown"
      takeaway={takeaway}
      headingLevel={3}
      height={240}
      model={chart}
      state={state}
      bucketNoun="day"
      filtersApplied={filtersApplied}
    />
  )
}

// ── Pass-rate trend ───────────────────────────────────────────────────────
const PASS_RATE_TARGET = { value: 90, label: 'Target 90%' } as const
const NO_EVALUATED_DAY = 'no day in this window has an evaluated execution'

/**
 * The window's pass rate per day, on the kit's time-series chart: the
 * endpoint's own `pass_rate` (gated on evaluated executions, so a day of only
 * skips is a gap), a gap for a day with no runs, the execution count as bars
 * and the 90 % target as a dashed line with a legend entry.
 *
 * The old card's header block — a big number labelled "today", a target pill
 * and a delta — becomes the frame's one-line takeaway: the number was the
 * WINDOW's rate, not today's, and the pill is now the target line's legend.
 */
function passRateChart(points: TimeSeriesPoint[], releases?: readonly ReleaseInput[]) {
  const built = buildTimeSeriesModel({ points, releases })
  const measured = built.points.some((point) => point.rate !== null)
  const frameState: ChartState<unknown> = measured
    ? readyState(built)
    : { status: 'not-measured', reason: NO_EVALUATED_DAY, meta: null }
  return { chart: built, state: frameState }
}

function PassRateTrend({
  trend, model, days,
}: { trend: TrendPoint[]; model: ConfidenceModel; days: number }) {
  const points = useMemo(() => {
    const cells = model.cadenceCells
    return timeSeriesFromTrends(
      trend.map((p) => ({ ...p, date: p.date.slice(0, 10) })),
      { from: cells[0]?.iso, to: cells[cells.length - 1]?.iso },
    )
  }, [trend, model.cadenceCells])

  const activeDays = model.passRatePerDay.length
  let takeaway: string | undefined
  if (model.evaluatedExecutions > 0) {
    const delta = Math.round(model.passRate - PASS_RATE_TARGET.value)
    const versus = delta === 0 ? 'at the 90% target' : `${Math.abs(delta)} pp ${delta < 0 ? 'below' : 'above'} the 90% target`
    const sparse = activeDays < 3 ? ` · ${activeDays} day${activeDays === 1 ? '' : 's'} with data, too few for a trend` : ''
    takeaway = `${model.passRate.toFixed(1)}% over the last ${days} days, ${versus}${sparse}`
  }

  return <PassRateTrendAnalysis points={points} takeaway={takeaway} />
}

type ReleaseRow = { id: string; name: string; released_at: string | null; planned_date: string | null }

/** A release's marker day: when it shipped, else when it is planned. */
function releaseMarkerInputs(items: readonly ReleaseRow[]): ReleaseInput[] {
  return items.map((r) => ({ id: r.id, name: r.name, date: r.released_at ?? r.planned_date }))
}

/**
 * The card's chart (VIZ-408): the VIZ-405 trend overlays, the VIZ-407 range
 * brush and the release markers over the window's points — no new request
 * (the release list is the top bar's release picker's own SWR entry). With
 * fewer than 7 days with runs the kit offers the overlays disabled, its
 * reason beside them. All Projects draws no marker: a release belongs to one
 * project. Since Phase D (S3) this is the only variant: the plain card the
 * flag-off page drew is gone.
 */
function PassRateTrendAnalysis({ points, takeaway }: { points: TimeSeriesPoint[]; takeaway: string | undefined }) {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const { data: releaseList } = useReleases(undefined, { cached: true })
  const releases = useMemo(
    () => (activeProjectId === ALL_PROJECTS_ID ? [] : releaseMarkerInputs(releaseList?.items ?? [])),
    [activeProjectId, releaseList],
  )
  const { chart, state } = useMemo(() => passRateChart(points, releases), [points, releases])
  return (
    <div data-catalogue-section="trends-pass-rate" className="min-w-0">
      <TimeSeriesChartFrame
        title="Pass rate trend"
        takeaway={takeaway}
        headingLevel={3}
        height={240}
        model={chart}
        state={state}
        rateTarget={PASS_RATE_TARGET}
        trendAnalysis
        zoom
      />
    </div>
  )
}

// ── Schedule-paused callout ───────────────────────────────────────────────
function SchedulePausedCallout({ model }: { model: ConfidenceModel }) {
  if (model.silentDays < 7 || !model.gapStart) return null
  return (
    <section
      aria-labelledby="schedpaused"
      className="rounded-xl"
      style={{
        padding: '14px 16px',
        background: 'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%), var(--color-bg-card)',
        border: '1px solid var(--gate-conditional-border)',
        borderLeft: '3px solid var(--status-broken)',
      }}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <h3 id="schedpaused" className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Schedule appears paused</h3>
        <span
          className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase"
          style={{ background: 'var(--gate-conditional-bg)', color: 'var(--status-broken)', letterSpacing: 'var(--tracking-wide)' }}
        >
          Action needed
        </span>
      </div>
      <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        The scheduled nightly run hasn't fired since <code className="font-mono text-[11.5px]">{shortDate(model.gapStart)}</code>.
        {' '}Most other widgets on this page degrade quietly when data is sparse — this one shouldn't.
      </p>
      <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <CalloutStat label="Silent days" value={model.silentDays} />
        <CalloutStat label="Expected runs missed" value={`~${model.expectedRunsMissed}`} />
      </div>
      <div className="flex flex-wrap gap-2 mt-3">
        <PrimaryBtn onClick={() => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' })}>
          Resume nightly
        </PrimaryBtn>
        <GhostBtn onClick={() => toast('Schedule view — coming in Phase 2', { icon: '👁️' })}>
          View schedule
        </GhostBtn>
      </div>
    </section>
  )
}

function CalloutStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      className="rounded-md border px-3 py-2.5"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="text-[20px] font-bold tabular-nums mt-0.5" style={{ color: 'var(--status-failed)' }}>{value}</div>
    </div>
  )
}

// ── Suite pass rates ──────────────────────────────────────────────────────
function SuitePassRates({ suites }: { suites: CoverageSuite[] }) {
  const filtered = suites
    .filter(s => (s.passed + s.failed + s.skipped) > 0)
    .slice(0, 6)
  return (
    <CardShell title="Suite pass rates · today" rightSlot={<span>Single day</span>}>
      <div className="px-4 pt-2 pb-4">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-2.5">No delta available — single day of data.</p>
        {filtered.length === 0 ? (
          <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">No suite data for the selected window.</p>
        ) : (
          <div className="flex flex-col">
            {filtered.map((s, i) => (
              <SuiteRow key={s.suite_name} suite={s} isLast={i === filtered.length - 1} />
            ))}
          </div>
        )}
      </div>
    </CardShell>
  )
}

// There is no per-suite history to draw, so the row carries no glyph: the
// "6-tick micro-bar" it once had was five constant ticks and one coloured by
// tone, the same for every suite whatever its rate (OD-2).
function SuiteRow({ suite, isLast }: { suite: CoverageSuite; isLast: boolean }) {
  // Take the rate the API already computed rather than deriving a second
  // one. `suite.failed` already folds broken in, so passed + failed is the
  // evaluated count — skips are excluded from both halves.
  const evaluated = suite.passed + suite.failed
  const pct = Math.round(suite.pass_rate)
  const tone = pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'bad'
  const pctColor = tone === 'good' ? 'var(--status-passed)' : tone === 'warn' ? 'var(--status-broken)' : 'var(--status-failed)'
  return (
    <div
      className={clsx('grid items-center gap-3', !isLast && 'pb-2.5 mb-2.5')}
      style={{ gridTemplateColumns: '1fr auto', borderBottom: !isLast ? '1px dashed var(--color-border)' : '0', paddingTop: 8 }}
    >
      <span
        className="font-mono text-[12.5px] truncate"
        style={{ color: tone === 'bad' ? 'var(--status-failed)' : 'var(--color-text)' }}
      >
        {suite.suite_name}
      </span>
      <span className="text-right">
        <span className="text-[12.5px] font-semibold tabular-nums" style={{ color: pctColor }}>{pct}%</span>
        <div className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums">{suite.passed} / {evaluated} evaluated</div>
      </span>
    </div>
  )
}

// ── Recommended actions ───────────────────────────────────────────────────
interface RecRow {
  role: 'dev' | 'qa' | 'rm'
  Icon: typeof BarChart3
  label: string
  body: React.ReactNode
  dim?: boolean
  cta?: { label: string; onClick: () => void; idle?: boolean }
}

function buildRecActions(model: ConfidenceModel, suites: CoverageSuite[]): RecRow[] {
  const recs: RecRow[] = []

  // Release manager — always relevant when there's a gap.
  if (model.silentDays >= 7 && model.gapStart && model.gapEnd) {
    recs.push({
      role: 'rm',
      Icon: Clock,
      label: 'Release manager',
      body: (
        <>
          Restart the nightly schedule and backfill <code>{shortDate(model.gapStart)}</code> → <code>{shortDate(model.gapEnd)}</code> if signal is needed for the release window.
        </>
      ),
      cta: { label: 'Open', onClick: () => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' }) },
    })
  }

  // Developer — only when there's a recent failing suite.
  const failingSuite = suites
    .filter(s => (s.passed + s.failed + s.skipped) > 0 && s.failed > 0)
    .sort((a, b) => a.pass_rate - b.pass_rate)[0]
  if (failingSuite) {
    const evaluated = failingSuite.passed + failingSuite.failed
    recs.push({
      role: 'dev',
      Icon: BarChart3,
      label: 'Developer',
      body: (
        <>
          <code>{failingSuite.suite_name}</code> failed {failingSuite.failed} of {evaluated} evaluated executions — investigate before assuming the trend is just sparse data.
        </>
      ),
      cta: { label: 'Open', onClick: () => toast('Failure detail — coming in Phase 2', { icon: '🔍' }) },
    })
  }

  // QA — Idle row when sample size too small.
  if (model.daysWithRuns < 5) {
    recs.push({
      role: 'qa',
      Icon: ShieldCheck,
      label: 'QA',
      dim: true,
      body: <>No QA action — wait for at least 5 days of data before reviewing trend deltas.</>,
      cta: { label: 'Idle', onClick: () => undefined, idle: true },
    })
  }

  return recs
}

function RecommendedActions({ recs }: { recs: RecRow[] }) {
  return (
    <CardShell title="Recommended actions" rightSlot={<span>routed by role</span>}>
      <div className="px-4 py-3.5 flex flex-col gap-2">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-1">Generated from the data gap and today's failures.</p>
        {recs.length === 0 ? (
          <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">
            No recommendations — trend data is consistent.
          </p>
        ) : (
          recs.map((r, i) => <RecActionRow key={i} rec={r} />)
        )}
      </div>
    </CardShell>
  )
}

function RecActionRow({ rec }: { rec: RecRow }) {
  const palette = {
    dev: { bg: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', fg: 'var(--status-flaky)' },
    qa:  { bg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', fg: 'var(--color-accent)' },
    rm:  { bg: 'color-mix(in srgb, var(--status-passed) 16%, transparent)',  fg: 'var(--status-passed)' },
  }[rec.role]
  const Icon = rec.Icon
  return (
    <div
      className={clsx('grid items-center gap-2.5 rounded-md border', rec.dim && 'opacity-60')}
      style={{ gridTemplateColumns: '24px 1fr auto', padding: '10px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <span className="inline-flex items-center justify-center rounded-full" style={{ width: 24, height: 24, background: palette.bg, color: palette.fg }}>
        <Icon className="h-3 w-3" />
      </span>
      <div className="min-w-0">
        <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex items-center gap-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
          {rec.label}
        </div>
        <p className="text-[12.5px] text-[var(--color-text-secondary)] m-0 mt-0.5" style={{ lineHeight: 1.45 }}>
          {rec.body}
        </p>
      </div>
      {rec.cta && (
        rec.cta.idle ? (
          <span
            className="text-[11px] px-2 py-1 rounded-md cursor-default"
            style={{ color: 'var(--color-text-faint)', border: '1px solid var(--color-border)', background: 'transparent' }}
          >
            {rec.cta.label}
          </span>
        ) : (
          <button
            type="button"
            onClick={rec.cta.onClick}
            className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md px-2 py-1 transition-colors"
            style={{ borderColor: 'var(--color-border)' }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
          >
            {rec.cta.label}
          </button>
        )
      )}
    </div>
  )
}

// ── Provenance footer ─────────────────────────────────────────────────────
function ProvenanceFooter({ totalEvidence, refreshedAt }: { totalEvidence: number; refreshedAt: string }) {
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>trends analyzer v1</span>
        <span aria-hidden>·</span>
        <span>{totalEvidence} evidence items · 2 tools</span>
        <span aria-hidden>·</span>
        <span>refreshed {refreshedAt}</span>
      </span>
      <button
        type="button"
        className="hover:underline inline-flex items-center gap-1"
        style={{ color: 'var(--color-accent)' }}
        onClick={() => toast('Decision-trail modal — coming in Phase 2', { icon: '🪪' })}
      >
        Decision trail <ArrowRight className="h-3 w-3" />
      </button>
    </div>
  )
}

// ── Shared shell ──────────────────────────────────────────────────────────
function CardShell({
  title, rightSlot, children,
}: { title: string; rightSlot?: React.ReactNode; children?: React.ReactNode }) {
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

// ── Helpers ───────────────────────────────────────────────────────────────
function relativeAgo(iso: string | null): string {
  return relativeDayLabel(iso)
}

function normaliseList<T>(raw: unknown): T[] {
  if (Array.isArray(raw)) return raw as T[]
  if (raw && typeof raw === 'object' && Array.isArray((raw as { items?: unknown }).items)) {
    return (raw as { items: T[] }).items
  }
  return []
}

// ── Page ──────────────────────────────────────────────────────────────────
export default function TrendsPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const now = useNow()  // captured at mount — avoids impure Date.now() in render

  // Global shared time-window preference — picking 24h here propagates
  // to every other window-filtered page (and vice versa). Snapped to
  // this page's allowed set.
  //
  // /trends has its own opinionated default of 14d (trend analysis
  // wants a meaningful baseline window; 1d or 7d hides the regression
  // signal). On every mount we reset the shared store to 14 so
  // navigating here always lands on the trend-friendly window
  // regardless of what the user last picked on /runs or /coverage.
  // In-page chips still update the shared store so the user can pick
  // 30d / 90d for a wider lens and that propagates downstream. (User
  // request 2026-05-19.)
  //
  // The reset is an effect, so it lands AFTER the first render, whose data
  // hooks have already asked for the stored window: a cold load from any
  // other window made every page read twice (30 days, then 14; pinned by
  // Wave 2.6 C0). So until the store holds the reset, the page reads the
  // window it is about to set, and the first request is the only one. The
  // hand-over is adjusted during render (as RunsPage does), not in the
  // effect: this repo forbids a synchronous setState inside useEffect.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const [resetPending, setResetPending] = useState(true)
  if (resetPending && storedDays === TRENDS_DEFAULT_WINDOW) setResetPending(false)
  useEffect(() => {
    setStoredDays(TRENDS_DEFAULT_WINDOW)
    // Intentional one-shot on mount — the in-page chip handler still
    // updates ``setStoredDays`` reactively, so this doesn't re-fire on
    // every render and clobber the user's chip selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const days = (resetPending ? TRENDS_DEFAULT_WINDOW : snapToAllowed(storedDays, WINDOWS)) as Window
  const setDays = setStoredDays as (w: Window) => void

  const [showPicker, setShowPicker] = useState(false)
  // VIZ-303: page-local with viz_multi_filters off (unchanged), the global
  // suite store with it on — see usePageSuiteFilter.
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames, suiteLabel, multiLabel } = usePageSuiteFilter()
  // P1: this page's saved views (the top-bar release, the window, the suite).
  const viewsMenu = useReportViewsMenu({
    route: '/trends',
    windowDays: days,
    windowOptions: WINDOWS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
  })
  // Whether a filter narrowed the trend (a suite, or a release): an all-zero
  // window then keeps the frame's filter words; without one the frame states
  // the window neutrally, "No executions in this window" (R1 F3).
  const releaseScope = useReleaseScope()
  const trendFiltered = scopeArg(suiteFilter) !== null || scopeArg(releaseScope) !== null
  const analyticsView = useAnalyticsView('trends')
  const { options: suiteOptions } = useSuiteOptions(days)
  // P2: the catalogue's "Filter page by this" writes a suite mark to the select above.
  const suiteTarget = usePageSuiteTarget(selectedSuite, suiteOptions, setSelectedSuite)

  // `error` is read alongside `data`: a failed fetch leaves `trend` empty,
  // and every band, verdict and recommendation below is computed from that
  // empty array -- a full page of conclusions drawn from no measurement.
  const { data: trendsData,   isLoading: trendsLoading, error: trendsError, mutate: retryTrends } =
    useTrendData(days, suiteFilter)
  // Real arrival time of this view's payload. This provenance age used to be
  // the literal '12m ago' for every project, however fresh the data was.
  const fetchedAt = useDataFreshness(trendsData)
  const { data: dashSummary }                              = useDashboardSummary(days, suiteFilter)
  const { data: coverageData }                             = useCoverage(days, suiteFilter)
  const { data: flakyData }                                = useFlakyTests(days, suiteFilter)
  const { data: latestRuns }                               = useRuns({ page: 1, size: 1, days, ...(suiteFilter && { suite_name: suiteFilter }) })

  const trend: TrendPoint[] = useMemo(() => trendsData?.data ?? [], [trendsData])
  const suites: CoverageSuite[] = useMemo(() => coverageData?.suites ?? [], [coverageData])
  const flakyCount = useMemo(() => normaliseList<{ test_fingerprint: string }>(flakyData).length, [flakyData])

  // Untagged share — count of runs where suite name is "Unknown Suite" / empty.
  const untaggedShare = useMemo(() => {
    const totalSuiteRuns = suites.reduce((s, x) => s + x.passed + x.failed + x.skipped, 0)
    if (totalSuiteRuns === 0) return 0
    const untaggedRuns = suites
      .filter(s => {
        const n = (s.suite_name ?? '').trim().toLowerCase()
        return !n || n === 'unknown suite' || n === 'unknown' || n === 'untagged'
      })
      .reduce((s, x) => s + x.passed + x.failed + x.skipped, 0)
    return untaggedRuns / totalSuiteRuns
  }, [suites])

  const model = useMemo(() => computeConfidenceModel(trend, days, untaggedShare), [trend, days, untaggedShare])
  const verdict = pickVerdict(model)
  const recs = useMemo(() => buildRecActions(model, suites), [model, suites])

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Search className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view trends."
      />
    )
  }

  if (trendsLoading && trend.length === 0) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }

  if (trendsError && !trendsData) {
    return (
      <DataUnavailable
        error={trendsError}
        onRetry={() => void retryTrends()}
        testId="trends-data-unavailable"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  // Surface the most-recent run's suite in the header so users see which
  // suite the trend bars belong to without having to drill into a run.
  const latestRun = latestRuns?.items?.[0]

  // Summary
  const summaryNode: React.ReactNode = (() => {
    if (verdict === 'PENDING')      return <>awaiting executions</>
    if (verdict === 'INSUFFICIENT') return <>only {model.daysWithRuns} of {model.windowDays} days {model.daysWithRuns === 1 ? 'has' : 'have'} executions</>
    if (verdict === 'HEALTHY')      return <>{model.daysWithRuns} active days · pass rate steady</>
    if (verdict === 'DECLINING')    return <>pass rate declined over the window</>
    return <>{model.daysWithRuns} active days · mixed signal</>
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      return <>No executions in the last {days} days. Run a workflow or extend the window to populate trend data.</>
    if (verdict === 'INSUFFICIENT')
      return <>The headline {model.passRate.toFixed(0)}% pass rate comes from {model.daysWithRuns === 1 ? 'a single day' : `${model.daysWithRuns} days`}. With {model.emptyDays} of the last {days} days empty, the page can't show a real trend — it shows {model.daysWithRuns === 1 ? 'one data point' : 'a few points'}. Resume the schedule or widen the window before reading anything into the numbers.</>
    if (verdict === 'HEALTHY')
      return <>Pass rate sits above target with low variance across {model.daysWithRuns} active days. Trend is stable.</>
    if (verdict === 'DECLINING')
      return <>Pass rate has dropped meaningfully from the start of the window to today. Investigate before treating the headline as the new normal.</>
    return <>Mixed signal across {model.daysWithRuns} active days — pass rate variance is moderate. Verify the recent failures before declaring a regression.</>
  })()

  // Issues
  const issues: IssueRowSpec[] = []
  if (model.silentDays >= 7) {
    issues.push({
      tone: 'bad',
      Icon: AlertCircle,
      body: (
        <>
          <strong>{model.emptyDays} of {model.windowDays} days</strong> have no executions
          {model.gapStart ? <> — the scheduler appears paused since <span className="text-[var(--color-text-muted)]">{shortDate(model.gapStart)}</span>.</> : '.'}
        </>
      ),
      cta: { label: 'Resume schedule', onClick: () => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' }) },
    })
  }
  if (model.daysWithRuns < 3 && model.totalExecutions > 0) {
    issues.push({
      tone: 'warn',
      Icon: TrendingUp,
      body: (
        <>
          Cannot compute trend direction — need at least <strong>3 data points</strong>, have {model.daysWithRuns}.
          {' '}<span className="text-[var(--color-text-muted)]">Pass rate, volume, and MTTF tiles show a single value, not a delta.</span>
        </>
      ),
      cta: { label: 'Widen to 90d', onClick: () => setDays(90) },
    })
  }
  if (model.totalExecutions > 0 && model.daysWithRuns >= 1) {
    issues.push({
      tone: 'info',
      Icon: AlertCircle,
      body: (
        <>
          The latest active day had <strong>{model.totalExecutions} test execution{model.totalExecutions === 1 ? '' : 's'}</strong>: {model.passedExecutions} passed, {model.failedExecutions} failed, {model.brokenExecutions} broken
          {model.evaluatedExecutions > 0 ? <> ({Math.round(model.passRate)}% of {model.evaluatedExecutions} evaluated)</> : null}.
          {' '}<span className="text-[var(--color-text-muted)]">No regression vs. the prior in-window run.</span>
        </>
      ),
      cta: { label: 'Compare runs', onClick: () => toast('Run comparison — coming in Phase 2', { icon: '⇆' }) },
    })
  }

  const verdictCtas = {
    primary:
      verdict === 'INSUFFICIENT' || verdict === 'PENDING'
        ? { label: 'Resume schedule', onClick: () => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' }) } as IssueRowSpec['cta']
        : { label: 'Email this view', onClick: () => toast('Email compose modal — coming in Phase 2', { icon: '✉️' }) } as IssueRowSpec['cta'],
    secondary: [
      days < 90
        ? { label: 'Widen window to 90d', onClick: () => setDays(90) } as IssueRowSpec['cta']
        : null,
      { label: 'Email this view', onClick: () => toast('Email compose modal — coming in Phase 2', { icon: '✉️' }) } as IssueRowSpec['cta'],
    ].filter((c): c is IssueRowSpec['cta'] => c !== null),
  }

  // The two KPI sparklines draw the window's real per-day series (OD-1); the
  // other three cells had only decoration and now carry none.
  const kpiSeries = kpiSparkSeries(model.cadenceCells)
  const totalEvidence = TRENDS_STAGES.reduce((s, x) => s + x.evidence, 0)
  // Dashboard summary delta — used by KPI 1 when we have a baseline.
  const passRateDelta = (() => {
    const prev = (dashSummary?.avg_pass_rate_7d?.trend ?? null) as number | null
    if (prev == null) return null
    return prev
  })()

  const lastRunRel = model.lastRunIso ? relativeAgo(model.lastRunIso) : '—'
  const previousRunRel = model.previousRunIso ? relativeAgo(model.previousRunIso) : null
  const previousRunGapDays = (() => {
    if (!model.lastRunIso || !model.previousRunIso) return null
    return daysBetweenDayIso(model.lastRunIso, model.previousRunIso)
  })()

  return (
    <PageShell>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Trends
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Project</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            {suiteLabel && (
              <>
                <span aria-hidden>·</span>
                <span>Suite</span>
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{suiteLabel}</code>
              </>
            )}
            <span aria-hidden>·</span>
            <span>Window</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">last {days} days</code>
            {latestRun && (latestRun.primary_suite_name || latestRun.suite_names?.length) && (
              <>
                <span aria-hidden>·</span>
                <span>Latest run suite</span>
                <SuiteBadge primary={latestRun.primary_suite_name} all={latestRun.suite_names} />
              </>
            )}
            <span aria-hidden>·</span>
            <span>refreshed {refreshedAt}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
          <GhostBtn onClick={() => setShowPicker(true)} title="Customize widgets">
            <LayoutGrid className="h-3.5 w-3.5" />
            Customize
          </GhostBtn>
          <WindowPicker value={days} onChange={setDays} />
          <SuiteFilterSelect
            value={selectedSuite}
            multiLabel={multiLabel}
            onChange={setSelectedSuite}
            options={suiteOptions}
            allLabel="All suites"
          />
          <GhostBtn
            onClick={() => toast('Export PDF — coming in Phase 2', { icon: '📄' })}
            title="Export this trends view as a PDF"
          >
            <Download className="h-3.5 w-3.5" />
            Export PDF
          </GhostBtn>
          <PrimaryBtn
            onClick={() => toast('Email compose modal — coming in Phase 2', { icon: '✉️' })}
            title="Email this trends view to the project recipients"
          >
            <Mail className="h-3.5 w-3.5" />
            Email report
          </PrimaryBtn>
        </div>
      </header>

      <VerdictCard
        model={model}
        verdict={verdict}
        summary={summaryNode}
        lede={lede}
        issues={issues}
        ctas={verdictCtas}
      />

      <TrendsRibbon totalEvidence={totalEvidence} confidencePct={91} />

      {analyticsView.widgetIds.includes('trends_kpis') && (
        <section aria-label="Trend metrics" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
          <KpiCell
            Icon={TrendingUp}
            label="Pass rate"
            value={`${model.passRate.toFixed(1)}%`}
            tone={model.passRate >= 80 ? 'good' : model.passRate >= 50 ? 'warn' : 'bad'}
            meta={model.daysWithRuns < 2
              ? <>single data point · no delta available</>
              : passRateDelta != null
                ? <>{passRateDelta > 0 ? '↑' : passRateDelta < 0 ? '↓' : '·'} {Math.abs(Math.round(passRateDelta))}pp vs prev window</>
                : <>{model.daysWithRuns} active days</>
            }
            spark={
              <Sparkline
                series={kpiSeries.passRate}
                label="Pass rate per day"
                domain={PERCENT_DOMAIN}
                format={formatPercent}
                tone={model.passRate >= 80 ? 'good' : model.passRate >= 50 ? 'warn' : 'bad'}
              />
            }
            isFirst
          />
          <KpiCell
            Icon={Calendar}
            label="Days with runs"
            value={
              <>
                {model.daysWithRuns}
                <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-1">/ {model.windowDays}</span>
              </>
            }
            tone={model.daysWithRuns < model.windowDays * 0.5 ? 'bad' : model.daysWithRuns < model.windowDays * 0.7 ? 'warn' : 'good'}
            meta={
              model.daysWithRuns < model.windowDays * 0.5
                ? <>{Math.round((model.daysWithRuns / model.windowDays) * 100)}% — schedule may be paused</>
                : <>{Math.round((model.daysWithRuns / model.windowDays) * 100)}% of window</>
            }
          />
          <KpiCell
            Icon={BarChart3}
            label="Executions"
            value={model.totalExecutions}
            meta={<>{model.passedExecutions} passed · {model.failedExecutions} failed · {model.brokenExecutions} broken · {model.skippedExecutions} skipped</>}
            spark={
              <Sparkline
                series={kpiSeries.executions}
                label="Executions per day"
                // A count starts at zero, as Overview draws the same measure:
                // on its own [min, max] a 100-to-104 week filled the cell (R1 F1).
                domain={countDomain(kpiSeries.executions)}
                tone="accent"
              />
            }
          />
          <KpiCell
            Icon={Layers}
            label="Suites"
            value={suites.length}
            tone="accent"
            meta={
              suites
                .filter(s => (s.passed + s.failed + s.skipped) > 0)
                .slice(0, 4)
                .map(s => s.suite_name)
                .join(', ') || '—'
            }
          />
          <KpiCell
            Icon={Clock}
            label="Last run"
            value={lastRunRel}
            tone={
              !model.lastRunIso ? 'neutral'
              : model.lastRunIso === utcDayIso() ? 'good'
              : (now - new Date(model.lastRunIso).getTime()) > 7 * 86400000 ? 'bad'
              : 'warn'
            }
            meta={
              previousRunRel
                ? <>previous run was {previousRunGapDays}d prior</>
                : <>no prior run in window</>
            }
            isLast
          />
        </section>
      )}

      {/* One column below 1024 px (VIZ-106); from lg the design's 1.65fr | 1fr. */}
      <div className="grid gap-3.5 trends-body-grid grid-cols-1 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-3.5 min-w-0">
          <CadenceHeatmap model={model} />
          {analyticsView.widgetIds.includes('daily_breakdown') && (
            <DailyBreakdown trend={trend} days={days} model={model} filtersApplied={trendFiltered} />
          )}
          {analyticsView.widgetIds.includes('pass_rate_trend') && (
            <PassRateTrend trend={trend} model={model} days={days} />
          )}
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <SchedulePausedCallout model={model} />
          <SuitePassRates suites={suites} />
          <RecommendedActions recs={recs} />
        </div>
      </div>

      {/* The section's own boundary (VIZ-107): a chunk that fails to load or
          a section that throws is one error card here, never the route's
          "Something went wrong" over the whole page. */}
      <SectionErrorBoundary message="Failed to load charts">
        <Suspense fallback={null}>
          <PageSuiteTargetContext.Provider value={suiteTarget}>
            <TrendsCatalogue days={days} suiteFilter={suiteFilter} />
          </PageSuiteTargetContext.Provider>
        </Suspense>
      </SectionErrorBoundary>

      <ProvenanceFooter totalEvidence={totalEvidence + (flakyCount > 0 ? 1 : 0)} refreshedAt={refreshedAt} />

      {showPicker && (
        <WidgetPicker
          page="trends"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { void analyticsView.setWidgets(ids) }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </PageShell>
  )
}

// Phase-2 imports kept referenced.
void ChevronRight; void AlertTriangle
