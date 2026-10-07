/**
 * Trends — the UX redesign's page template (P3, `02-design-spec.md` §2/§5).
 *
 * Top to bottom (at 1440 x 900 the hero starts within 300 px of the content top):
 *   Header  → `PageHeader` (compact): title, the crumb as its one-line
 *             subtitle (project · suite · window · latest run suite ·
 *             refreshed), the help **?**, and on its right the page's one
 *             filter row: the suite filter, the shared `WindowPicker`
 *             (24h/7d/14d/30d/90d, the global window store) and the saved
 *             Views menu.
 *   KPIs    → `KpiStrip`, 5 compact tiles: Pass rate · Days with runs ·
 *             Executions · Suites · Last run. Pass rate and Executions carry
 *             a kit `Sparkline` of the window's real per-day series (a day
 *             without runs is a break in the line); the other three had only
 *             decoration and carry no glyph (VIZ-104, OD-1). The strip keeps
 *             its `Trend metrics` landmark.
 *   Hero    → the Pass rate trend (`data-primary`): kit
 *             `TimeSeriesChartFrame` with the 90 % target line, the trend
 *             overlays, the range brush and release markers.
 *   Score   → a collapsed `Disclosure` ("How this score is computed"): the
 *             verdict (HEALTHY / MIXED / INSUFFICIENT / DECLINING / PENDING)
 *             and its words, the 0-100 trend-confidence score on the kit's
 *             marker `GaugeBar` and the 2×2 weighted dimension grid (Data
 *             coverage 40 % / Sample size 25 % / Variance stability 20 % /
 *             Tag quality 15 %). Its header states the verdict and the score.
 *   Tabs    → `?tab=` (`useTabParam`):
 *     Volume    (default) Run cadence (a kit `DayStrip`, presence mode,
 *               failing days striped, today and the gap edge marked) → the
 *               schedule-paused callout (only when the gap is real) → Daily
 *               breakdown (kit `StackedColumnChartFrame`).
 *     By suite  Pass rate by suite and Compare (the catalogue) beside the
 *               Suite pass rates card.
 *     Durations Test duration p50 / p95 (the catalogue).
 *     Heatmap   Suite pass rate by day (the catalogue).
 *
 * P3 moved every chart; none was rebuilt. The verdict card is gone (its
 * score and dimensions are the Disclosure; its issue rows repeated the KPI
 * strip, the cadence card and the schedule callout; its "Widen window to
 * 90d" button repeated the window picker's 90d), and so are the page-local
 * window picker, KPI cell and buttons (the shared primitives replace them).
 *
 * P2 (UX redesign, "remove the noise"): the invented 3-stage workflow ribbon,
 * the provenance footer, the recommended-actions card, the widget picker and
 * every control that only raised a not-built toast (Export PDF, Email report,
 * Resume schedule, Compare runs) are gone: the page shows only what it does.
 *
 * Catalogue (VIZ-408, Wave 2.6): a lazy chunk with the suite series, Compare,
 * the duration band and the suite x day heatmap (each section asks for its
 * own data once near). Since Phase D (S3/S4) the page asks no chart flag. It
 * is mounted only in a tab that shows it, with that tab's sections
 * (`CATALOGUE_TAB_SECTIONS`, the catalogue's `sections` prop).
 *
 * Data: derives every section from existing useTrendData + useDashboardSummary
 * + useCoverage. The README proposes dedicated trends / cadence / suites
 * endpoints — none exist yet, so the page reads the existing trend tail and
 * derives the remaining signals (variance, gap detection) deterministically.
 */
import { suiteHrefByName } from '@/routing/suiteHref'
import { Link } from 'react-router-dom'
import { Suspense, useEffect, useMemo, useState } from 'react'
import {
  BarChart3, Calendar, Clock, Layers, Search, TrendingUp,
} from 'lucide-react'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import PageHeader from '@/components/ui/PageHeader'
import WindowPicker from '@/components/ui/WindowPicker'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Disclosure from '@/components/ui/Disclosure'
import Tabs from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useRuns } from '@/hooks/useRuns'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { shortAgo } from '@/utils/formatters'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { useCoverage, useDashboardSummary, useTrendData } from '@/hooks/useMetrics'
import {
  daysBetweenDayIso, formatDayIso, relativeDayLabel, shiftDayIso, utcDayIso,
} from '@/utils/calendarDay'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { PageSuiteTargetContext, usePageSuiteTarget } from '@/hooks/pageSuiteTarget'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { scopeArg, type ScopeValue } from '@/lib/scopeParams'
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
import type { TrendsSectionId } from '@/components/reports/catalogue/TrendsCatalogue'
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

/** The help drawer's topic for this page (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/trends')

// ── Tabs ───────────────────────────────────────────────────────────────────
/** The page's own tabs, in `?tab=` (Volume is the default and the clean URL). */
const TRENDS_TABS = [
  { id: 'volume', label: 'Volume' },
  { id: 'by-suite', label: 'By suite' },
  { id: 'durations', label: 'Durations' },
  { id: 'heatmap', label: 'Heatmap' },
] as const
type TrendsTab = (typeof TRENDS_TABS)[number]['id']
const TRENDS_TAB_IDS: readonly TrendsTab[] = TRENDS_TABS.map((t) => t.id)
type CatalogueTab = Exclude<TrendsTab, 'volume'>

/**
 * Which catalogue sections each tab renders (`TrendsCatalogue`'s `sections`).
 * A section another tab owns is not rendered at all, so it never mounts and
 * never asks for its data.
 */
const CATALOGUE_TAB_SECTIONS: Record<CatalogueTab, readonly TrendsSectionId[]> = {
  'by-suite': ['trends-multi-series', 'trends-compare'],
  durations: ['trends-duration'],
  heatmap: ['trends-heatmap'],
}

// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'HEALTHY' | 'MIXED' | 'INSUFFICIENT' | 'DECLINING' | 'PENDING'

/** The verdict's words and hues (the score's pill and number; the verdict line in the score disclosure). */
interface VerdictTheme {
  gateText: string
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  HEALTHY: {
    gateText: 'var(--status-passed)',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    label:  'Trend healthy',
  },
  MIXED: {
    gateText: 'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Trend mixed',
  },
  INSUFFICIENT: {
    gateText: 'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Insufficient data',
  },
  DECLINING: {
    gateText: 'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Trend declining',
  },
  PENDING: {
    gateText: 'var(--color-text-secondary)',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    label:  'Pending',
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

// ── Score disclosure ──────────────────────────────────────────────────────
/**
 * What the verdict card held, in the collapsed "How this score is computed"
 * disclosure below the hero (P3): the verdict and its one-line summary, the
 * words that say how to read it, the 0-100 confidence score on the kit's
 * meter and the four weighted dimensions it is computed from. The card's
 * issue rows are not here: each repeated a fact the page already shows (the
 * gap: the cadence card and the schedule callout; too few days: the pass-rate
 * frame's takeaway; the executions: the KPI strip).
 */
function ScoreDetails({
  model, verdict, summary, lede,
}: {
  model: ConfidenceModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Trend verdict"
      // One column below 1024 px (VIZ-106); from lg the card's 1.45fr | 1fr.
      className="grid gap-6 grid-cols-1 lg:grid-cols-[1.45fr_1fr]"
    >
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold m-0 mb-1.5 text-[var(--color-text)]">
          <span aria-label={`Verdict: ${t.label}`} style={{ color: t.gateText }}>{t.label}</span>
          <span className="text-[var(--color-text-muted)] mx-2">·</span>
          <span>{summary}</span>
        </h2>
        <p className="text-[13px] m-0 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
          {lede}
        </p>
      </div>
      <div className="flex flex-col gap-3.5 min-w-0">
        <ConfidenceMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
    </section>
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
/**
 * A sparkline's box in a compact KPI tile. The tile sets its sparkline beside
 * the value in a slot with no width of its own, and a sparkline is as wide as
 * its container: this gives it one.
 */
function KpiSpark({ children }: { children: React.ReactNode }) {
  return <div data-kpi-spark="" className="w-20">{children}</div>
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

const CADENCE_HINT =
  'Each cell is one day. Green: it had executions; a striped band: some of them failed. An empty cell: no runs landed that day.'

function CadenceHeatmap({ model }: { model: ConfidenceModel }) {
  const cells = useMemo(() => model.cadenceCells.map(cadenceStripCell), [model.cadenceCells])
  const counts = countTones(cells)
  const activeCount = cells.length - counts.none
  const emptyCount = counts.none
  const failingCount = counts.mixed

  // P3: the explainer paragraph that sat over the strip is the strip's
  // legend and its title's tooltip now (spec §2: explanatory text is never a
  // paragraph on the page).
  return (
    <CardShell
      title={`Run cadence — last ${model.windowDays} days`}
      titleHint={CADENCE_HINT}
      rightSlot={<span>{activeCount} day{activeCount === 1 ? '' : 's'} with runs · {emptyCount} empty</span>}
    >
      <div className="px-4 pt-3 pb-4">
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
/** The hero's plot height, px (spec §2: the primary chart; it was 240 in the two-column body). */
const HERO_HEIGHT = 280
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
  // P3: the page's one primary content (`data-primary`), the hero under the
  // KPI strip, a little taller than its 240 px in the old two-column body.
  return (
    <div data-catalogue-section="trends-pass-rate" data-primary="" className="min-w-0">
      <TimeSeriesChartFrame
        title="Pass rate trend"
        takeaway={takeaway}
        // h2: the first heading under the page title (the verdict card's h2
        // came first before P3). The frame styles every level alike.
        headingLevel={2}
        height={HERO_HEIGHT}
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
// The window's longest silence, stated as the dates it covers. The gap is the
// LONGEST one, not necessarily the trailing one: only a gap that runs up to
// today says the schedule "has not fired since"; an older gap that runs have
// since ended is a past silence, and is called that.
function SchedulePausedCallout({ model }: { model: ConfidenceModel }) {
  if (model.silentDays < 7 || !model.gapStart || !model.gapEnd) return null
  const ongoing = model.gapEnd === utcDayIso()
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
        <h3 id="schedpaused" className="text-[13px] font-semibold m-0 text-[var(--color-text)]">
          {ongoing ? 'Schedule appears paused' : 'Run gap in this window'}
        </h3>
        {ongoing && (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase"
            style={{ background: 'var(--gate-conditional-bg)', color: 'var(--status-broken)', letterSpacing: 'var(--tracking-wide)' }}
          >
            Action needed
          </span>
        )}
      </div>
      <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        {ongoing
          ? <>No executions have landed since <code className="font-mono text-[11.5px]">{shortDate(model.gapStart)}</code>.</>
          : <>No executions landed from <code className="font-mono text-[11.5px]">{shortDate(model.gapStart)}</code> to <code className="font-mono text-[11.5px]">{shortDate(model.gapEnd)}</code>.</>}
      </p>
      <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <CalloutStat label="Silent days" value={model.silentDays} />
        <CalloutStat label="Expected runs missed" value={`~${model.expectedRunsMissed}`} />
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
/**
 * Each suite's pass rate over the page's window (the coverage read is asked
 * for the same `days`), at most the first {@link SUITE_ROWS_SHOWN} suites with
 * executions. The card once said "· today" / "Single day" / "No delta
 * available — single day of data." whatever window was picked: it is the
 * window's rate, and it says so.
 */
const SUITE_ROWS_SHOWN = 6
function SuitePassRates({ suites, days }: { suites: CoverageSuite[]; days: number }) {
  const withRuns = suites.filter(s => (s.passed + s.failed + s.skipped) > 0)
  const filtered = withRuns.slice(0, SUITE_ROWS_SHOWN)
  return (
    <CardShell
      title={`Suite pass rates — last ${days === 1 ? '24 hours' : `${days} days`}`}
      titleHint="Passed of evaluated executions per suite across the window; skips are excluded."
      rightSlot={withRuns.length > filtered.length ? <span>{filtered.length} of {withRuns.length} suites</span> : undefined}
    >
      <div className="px-4 pt-2 pb-4">
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
      {/* The suite's own page (UX redesign P4): its tests, runs and charts. */}
      <Link
        to={suiteHrefByName(suite.suite_name)}
        className="font-mono text-[12.5px] truncate hover:underline"
        title={suite.suite_name}
        style={{ color: tone === 'bad' ? 'var(--status-failed)' : 'var(--color-text)' }}
      >
        {suite.suite_name}
      </Link>
      <span className="text-right">
        <span className="text-[12.5px] font-semibold tabular-nums" style={{ color: pctColor }}>{pct}%</span>
        <div className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums">{suite.passed} / {evaluated} evaluated</div>
      </span>
    </div>
  )
}

// ── Shared shell ──────────────────────────────────────────────────────────
function CardShell({
  title, titleHint, rightSlot, children,
}: {
  title: string
  /** How to read the card, as the title's tooltip (never a paragraph on the page, spec §2). */
  titleHint?: string
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
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]" title={titleHint}>{title}</h3>
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

/**
 * The latest run's suite in words, as `SuiteBadge` draws it ("<primary> +N"
 * when the run spans several suites); `null` when the run names none.
 */
function latestRunSuiteText(run: { primary_suite_name?: string | null; suite_names?: string[] | null } | undefined): string | null {
  if (!run) return null
  const list = (run.suite_names ?? []).filter(Boolean)
  const label = run.primary_suite_name ?? list[0] ?? null
  if (!label) return null
  const extra = list.filter((s) => s !== label).length
  return extra > 0 ? `${label} +${extra}` : label
}

// ── Page ──────────────────────────────────────────────────────────────────
export default function TrendsPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Global shared time-window preference — picking 24h here propagates
  // to every other window-filtered page (and vice versa). Snapped to
  // this page's allowed set.
  //
  // /trends has its own opinionated default of 14d (trend analysis
  // wants a meaningful baseline window; 1d or 7d hides the regression
  // signal). On every mount we reset the shared store to 14 so
  // navigating here always lands on the trend-friendly window
  // regardless of what the user last picked on /runs or /coverage.
  // The toolbar's window picker (the shared `WindowPicker`) still writes
  // the shared store so the user can pick 30d / 90d for a wider lens and
  // that propagates downstream. (User request 2026-05-19.)
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
    // Intentional one-shot on mount — the window picker still updates the
    // store reactively, so this doesn't re-fire on every render and
    // clobber the user's selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const days = (resetPending ? TRENDS_DEFAULT_WINDOW : snapToAllowed(storedDays, WINDOWS)) as Window
  // The page's own tabs (`?tab=`): Volume by default.
  const [tab, setTab] = useTabParam<TrendsTab>(TRENDS_TAB_IDS, 'volume')

  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames, suiteLabel } = usePageSuiteFilter()
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
  const { options: suiteOptions } = useSuiteOptions(days)
  // P2: the catalogue's "Filter page by this" writes a suite mark to the select above.
  const suiteTarget = usePageSuiteTarget(selectedSuite, suiteOptions, setSelectedSuite)

  // `error` is read alongside `data`: a failed fetch leaves `trend` empty,
  // and every band and verdict below is computed from that
  // empty array -- a full page of conclusions drawn from no measurement.
  const { data: trendsData,   isLoading: trendsLoading, error: trendsError, mutate: retryTrends } =
    useTrendData(days, suiteFilter)
  // Real arrival time of this view's payload (the header's "refreshed" age),
  // never a literal like '12m ago' for every project.
  const fetchedAt = useDataFreshness(trendsData)
  const { data: dashSummary }                              = useDashboardSummary(days, suiteFilter)
  const { data: coverageData }                             = useCoverage(days, suiteFilter)
  const { data: latestRuns }                               = useRuns({ page: 1, size: 1, days, ...(suiteFilter && { suite_name: suiteFilter }) })

  const trend: TrendPoint[] = useMemo(() => trendsData?.data ?? [], [trendsData])
  const suites: CoverageSuite[] = useMemo(() => coverageData?.suites ?? [], [coverageData])

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

  // The two KPI sparklines draw the window's real per-day series (OD-1); the
  // other three tiles had only decoration and carry none.
  const kpiSeries = kpiSparkSeries(model.cadenceCells)
  // Dashboard summary delta — used by KPI 1 when we have a baseline.
  const passRateDelta = (() => {
    const prev = (dashSummary?.avg_pass_rate_7d?.trend ?? null) as number | null
    if (prev == null) return null
    return prev
  })()
  const passRateTone = model.passRate >= 80 ? 'good' : model.passRate >= 50 ? 'warn' : 'bad'
  const passRateChange: { trend_direction: 'up' | 'down' | 'flat' | 'none'; trend_text: string } =
    model.daysWithRuns < 2
      ? { trend_direction: 'none', trend_text: 'single data point · no delta available' }
      : passRateDelta != null
        ? {
            trend_direction: passRateDelta > 0 ? 'up' : passRateDelta < 0 ? 'down' : 'flat',
            trend_text: `${Math.abs(Math.round(passRateDelta))}pp vs prev window`,
          }
        : { trend_direction: 'none', trend_text: `${model.daysWithRuns} active days` }
  const daysWithRunsPct = model.windowDays > 0 ? Math.round((model.daysWithRuns / model.windowDays) * 100) : 0

  const lastRunRel = model.lastRunIso ? relativeAgo(model.lastRunIso) : '—'
  const previousRunRel = model.previousRunIso ? relativeAgo(model.previousRunIso) : null
  const previousRunGapDays = (() => {
    if (!model.lastRunIso || !model.previousRunIso) return null
    return daysBetweenDayIso(model.lastRunIso, model.previousRunIso)
  })()

  // The crumb the old header drew as chips, as the compact header's one line.
  const latestSuite = latestRunSuiteText(latestRun)
  const subtitle = [
    `Project ${projectLabel}`,
    suiteLabel ? `Suite ${suiteLabel}` : null,
    days === 1 ? 'last 24 hours' : `last ${days} days`,
    latestSuite ? `Latest run suite ${latestSuite}` : null,
    `refreshed ${refreshedAt}`,
  ].filter(Boolean).join(' · ')

  return (
    <PageShell className="space-y-4">
      {/* The page's one filter row is the header's right side, where the old
          header had it (and as Home has it): the suite, the window, then the
          saved Views (the one secondary button). A row of its own put the
          hero 2 px past the fold budget at 1440 x 900. */}
      <PageHeader
        compact
        title="Trends"
        subtitle={subtitle}
        helpTopic={HELP_TOPIC}
        actions={
          <div data-page-toolbar="" className="flex flex-wrap items-center gap-2">
            <SuiteFilterSelect
              value={selectedSuite}
              onChange={setSelectedSuite}
              options={suiteOptions}
              allLabel="All suites"
            />
            <WindowPicker options={WINDOWS} />
            {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
          </div>
        }
      />

      {/* The strip keeps the `Trend metrics` landmark other specs wait on.
          It wraps to fewer columns when its tiles cannot fit (KpiStrip). */}
      <section aria-label="Trend metrics">
        <KpiStrip>
          <MetricCard
            compact
            title="Pass rate"
            icon={<TrendingUp className="h-4 w-4" />}
            metric={{ value: `${model.passRate.toFixed(1)}%`, ...passRateChange }}
            sparkline={
              <KpiSpark>
                <Sparkline
                  series={kpiSeries.passRate}
                  label="Pass rate per day"
                  domain={PERCENT_DOMAIN}
                  format={formatPercent}
                  tone={passRateTone}
                />
              </KpiSpark>
            }
          />
          <MetricCard
            compact
            title="Days with runs"
            icon={<Calendar className="h-4 w-4" />}
            metric={{
              value: `${model.daysWithRuns} / ${model.windowDays}`,
              trend_direction: 'none',
              trend_text: model.daysWithRuns < model.windowDays * 0.5
                ? `${daysWithRunsPct}% — schedule may be paused`
                : `${daysWithRunsPct}% of window`,
            }}
          />
          <MetricCard
            compact
            title="Executions"
            icon={<BarChart3 className="h-4 w-4" />}
            metric={{
              value: model.totalExecutions,
              trend_direction: 'none',
              trend_text: `${model.passedExecutions} passed · ${model.failedExecutions} failed · ${model.brokenExecutions} broken · ${model.skippedExecutions} skipped`,
            }}
            sparkline={
              <KpiSpark>
                <Sparkline
                  series={kpiSeries.executions}
                  label="Executions per day"
                  // A count starts at zero, as Overview draws the same measure:
                  // on its own [min, max] a 100-to-104 week filled the cell (R1 F1).
                  domain={countDomain(kpiSeries.executions)}
                  tone="accent"
                />
              </KpiSpark>
            }
          />
          <MetricCard
            compact
            title="Suites"
            icon={<Layers className="h-4 w-4" />}
            metric={{
              value: suites.length,
              trend_direction: 'none',
              trend_text: suites
                .filter(s => (s.passed + s.failed + s.skipped) > 0)
                .slice(0, 4)
                .map(s => s.suite_name)
                .join(', ') || '—',
            }}
          />
          <MetricCard
            compact
            title="Last run"
            icon={<Clock className="h-4 w-4" />}
            metric={{
              value: lastRunRel,
              trend_direction: 'none',
              trend_text: previousRunRel ? `previous run was ${previousRunGapDays}d prior` : 'no prior run in window',
            }}
          />
        </KpiStrip>
      </section>

      {/* The page's one primary content: the hero (`data-primary` is on its section). */}
      <PassRateTrend trend={trend} model={model} days={days} />

      <Disclosure
        title="How this score is computed"
        summary={verdict === 'PENDING' ? 'Pending · no score yet' : `${VERDICT_THEME[verdict].label} · confidence ${model.composite} / 100`}
      >
        <ScoreDetails model={model} verdict={verdict} summary={summaryNode} lede={lede} />
      </Disclosure>

      <div className="space-y-3">
        <Tabs ariaLabel="Trend views" items={TRENDS_TABS} value={tab} onChange={setTab} />
        <div data-tab-panel={tab}>
          {tab === 'volume' && (
            <div className="flex flex-col gap-3.5 min-w-0">
              <CadenceHeatmap model={model} />
              <SchedulePausedCallout model={model} />
              <DailyBreakdown trend={trend} days={days} model={model} filtersApplied={trendFiltered} />
            </div>
          )}
          {tab === 'by-suite' && (
            // One column below 1024 px (VIZ-106); from lg the suite series and
            // Compare beside the Suite pass rates card.
            <div className="grid gap-3.5 items-start grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]">
              <CatalogueTab scope="by-suite" days={days} suiteFilter={suiteFilter} suiteTarget={suiteTarget} />
              <SuitePassRates suites={suites} days={days} />
            </div>
          )}
          {(tab === 'durations' || tab === 'heatmap') && (
            // Keyed: a tab's sections mount when it opens and go when it
            // closes, as in every other tab (never left mounted but hidden).
            <CatalogueTab key={tab} scope={tab} days={days} suiteFilter={suiteFilter} suiteTarget={suiteTarget} />
          )}
        </div>
      </div>
    </PageShell>
  )
}

/**
 * The catalogue in a tab, rendering only that tab's sections
 * (`CATALOGUE_TAB_SECTIONS`). The catalogue block's own top margin is
 * dropped: the tab panel spaces it. The section's own boundary (VIZ-107): a
 * chunk that fails to load or a section that throws is one error card here,
 * never the route's "Something went wrong" over the whole page.
 */
function CatalogueTab({
  scope, days, suiteFilter, suiteTarget,
}: {
  scope: CatalogueTab
  days: number
  suiteFilter: ScopeValue
  suiteTarget: ReturnType<typeof usePageSuiteTarget>
}) {
  return (
    <div data-catalogue-scope={scope} className="min-w-0 [&>[data-trends-catalogue]]:mt-0">
      <SectionErrorBoundary message="Failed to load charts">
        <Suspense fallback={null}>
          {/* P2: the catalogue's "Filter page by this" writes a suite mark to the page's select. */}
          <PageSuiteTargetContext.Provider value={suiteTarget}>
            <TrendsCatalogue days={days} suiteFilter={suiteFilter} sections={CATALOGUE_TAB_SECTIONS[scope]} />
          </PageSuiteTargetContext.Provider>
        </Suspense>
      </SectionErrorBoundary>
    </div>
  )
}
