/**
 * Trends — the UX redesign's page template (P3, `02-design-spec.md` §2/§5).
 *
 * Top to bottom (at 1440 x 900 the hero starts within 300 px of the content top):
 *   Header  → `PageHeader` (compact): title, the crumb as its one-line
 *             subtitle (project · suite · window · latest run suite ·
 *             refreshed), the help **?**, and on its right the page's one
 *             filter row: the suite filter, the shared `WindowPicker`
 *             (24h/7d/14d/30d/90d, the global window store), the saved
 *             Views menu and the **⋯** (W08): Export PDF (every role; the
 *             page's window, suite and release, `GET /reports/trends/pdf`),
 *             Email report… (QA lead+, `EmailReportDialog`: members and up to
 *             5 typed addresses, the relay saved in Settings › Notifications)
 *             and Compare runs (the latest two runs of the page's suite, else
 *             the manual form; TRND-01, TRND-02). In All Projects each reads
 *             "(pick a project)" and is disabled.
 *   KPIs    → `KpiStrip`, 5 compact tiles: Pass rate · Days with runs ·
 *             Executions · Suites · Last run. Pass rate and Executions carry
 *             a kit `Sparkline` of the window's real per-day series (a day
 *             without runs is a break in the line); the other three had only
 *             decoration and carry no glyph (VIZ-104, OD-1). The strip keeps
 *             its `Trend metrics` landmark.
 *   Hero    → the Pass rate trend (`data-primary`): kit
 *             `TimeSeriesChartFrame` with the policy's GO line, the trend
 *             overlays, the range brush, release markers and the window's
 *             timeline notes (TRND-04: a band per range, a dashed line per
 *             day, the `Notes (n)` chip in the frame's toolbar when there are
 *             any; a suite note only under its suite).
 *   Score   → a collapsed `Disclosure` ("How this score is computed"): the
 *             verdict (HEALTHY / MIXED / INSUFFICIENT / DECLINING / PENDING)
 *             and its words, the 0-100 trend-confidence score on the kit's
 *             marker `GaugeBar` and the 2×2 weighted dimension grid (Data
 *             coverage 40 % / Sample size 25 % / Variance stability 20 % /
 *             Tag quality 15 %). Its header states the verdict and the score.
 *             All of it is the SERVER's trend summary (TRND-06,
 *             `GET /analytics/trend-summary`, `useTrendSummary`); missing or
 *             malformed, the disclosure reads "Score not available" and the
 *             rest of the page is unchanged.
 *   Tabs    → `?tab=` (`useTabParam`):
 *     Volume    (default) Run cadence (a kit `DayStrip`, presence mode,
 *               failing days striped, today and the gap edge marked; a QA
 *               engineer+ drags a range on it, or uses its `Pin a note`, to
 *               pin a note) → the run-gap callout (when the server's gap is
 *               real, or a CONFIGURED expected cadence (TRND-03) missed or
 *               paused; its status read is this tab's only;
 *               `Pin a note for these days`) → Daily breakdown (kit
 *               `StackedColumnChartFrame`).
 *     By suite  Pass rate by suite and Compare (the catalogue; each has
 *               Customise, TRND-10) beside the Suite pass rates card, whose
 *               rows carry a sparkline of the suite's daily pass rate (the
 *               SAME request as "Pass rate by suite", `useSuiteSparklines`)
 *               and a compare-the-latest-two-runs link (TRND-06, TRND-02).
 *     Execution time (`?tab=durations`, the id kept from when it was
 *               "Durations"; EXEC-04b) — the execution-metrics section (wall
 *               clock vs summed test time, the per-suite table, regressions,
 *               the 20 slowest tests, the duration distribution) above the
 *               test duration p50 / p95 band (the catalogue).
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
 * every control that only raised a not-built toast are gone: the page shows
 * only what it does. Export PDF, Email report, Compare runs and pinning a note
 * came back in W08 as built features; Resume schedule did not.
 *
 * Catalogue (VIZ-408, Wave 2.6): a lazy chunk with the suite series, Compare,
 * the duration band and the suite x day heatmap (each section asks for its
 * own data once near). Since Phase D (S3/S4) the page asks no chart flag. It
 * is mounted only in a tab that shows it, with that tab's sections
 * (`CATALOGUE_TAB_SECTIONS`, the catalogue's `sections` prop). The page reads
 * the suite series' parameters from `trendsCatalogueParams.ts`, never from the
 * catalogue module (a static import would make its chunk eager).
 *
 * Saved views (TRND-10): a view stores the page's customised charts in
 * `filters.charts` (`trends.suite-series`, `trends.compare`) — ONLY charts not
 * at their default; with none customised the saved filters are exactly what
 * they were (no `charts` key). Opening a view applies them
 * (`chartConfigStore`) and writes Compare's chosen suites / releases back into
 * the URL.
 *
 * Data: the KPI strip, the cadence strip, the hero and the daily breakdown
 * come from useTrendData + useDashboardSummary + useCoverage (the cadence
 * cells and totals are `computeCadenceModel`). The verdict, the confidence
 * score and its dimensions, the variance and the run gap are the server's
 * trend summary (TRND-06) — computed there over the same two reads, so the
 * page, the PDF and the email cannot disagree. The timeline notes of the
 * window are `useTimelineNotes` (one project only). Every secondary read is
 * parsed by a guard: missing or malformed, its section says so and the page
 * still renders.
 */
import { suiteHrefByName } from '@/routing/suiteHref'
import { compareRunsHref, latestTwoHref } from '@/routing/compareHref'
import { Link, useSearchParams } from 'react-router-dom'
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Download, GitCompare, Mail, Search, StickyNote,
} from 'lucide-react'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import PageHeader from '@/components/ui/PageHeader'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import WindowPicker from '@/components/ui/WindowPicker'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import { Panel } from '@/components/ui/PanelHeader'
import Disclosure from '@/components/ui/Disclosure'
import Tabs from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useRuns } from '@/hooks/useRuns'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { shortAgo } from '@/utils/formatters'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { useCoverage, useDashboardSummary, useTrendData } from '@/hooks/useMetrics'
import { usePermissions } from '@/hooks/usePermissions'
import { useTrendSummary } from '@/hooks/useTrendSummary'
import { useTimelineNotes } from '@/hooks/useTimelineNotes'
import { useRunCadenceStatus } from '@/hooks/useRunCadences'
import { ACTION_NEEDED, calloutModel } from '@/components/runCadence/cadenceStatusModel'
import ScopedLink from '@/components/ui/ScopedLink'
import { downloadTrendsPdf, refusalText, trendsPdfFilename } from '@/services/trendsReportService'
import type { TrendSummary, TrendSummaryDimension, TrendSummaryGap } from '@/types/trends'
import { noteInputsFor, type TimelineNote } from '@/types/timelineNotes'
import { downloadBlob } from '@/utils/download'
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
import type { TimeSeriesRateTarget } from '@/components/charts/TimeSeriesChart'
import TimelineNotesChip from '@/components/charts/TimelineNotesChip'
import {
  goLineLabel, noGoZoneLabel, readinessBands, versusGoLine, type ReadinessBands,
} from '@/utils/passRateBands'
import { buildTimeSeriesModel, timeSeriesFromTrends } from '@/components/charts/timeSeriesModel'
import { readyState, type ChartState } from '@/components/charts/chartStateCore'
import type { NoteInput, ReleaseInput, TimeSeriesPoint } from '@/components/charts/timeSeriesModel'
import { SERIES_CONFIG_MODEL } from '@/components/charts/chartCustomiseModel'
import { seedChartConfig } from '@/components/charts/useChartConfig'
import { useReleases } from '@/hooks/useReleases'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { SavedView } from '@/services/savedViewsService'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import type { TrendsSectionId } from '@/components/reports/catalogue/TrendsCatalogue'
import LazySection from '@/components/reports/catalogue/LazySection'
import {
  COMPARE_CHART_KEY,
  SUITE_SERIES_CHART_KEY,
  TRENDS_CHART_KEYS,
} from '@/components/reports/catalogue/trendsCatalogueParams'
import {
  COMPARE_CONFIG_MODEL,
  readCompareConfig,
  urlWithCompareConfig,
} from '@/components/reports/catalogue/compareChartConfig'
import { chartConfigStore, nonDefaultOf, useChartConfigStore } from '@/store/chartConfigStore'
import EmailReportDialog from '@/components/trends/EmailReportDialog'
import PinNoteDialog from '@/components/trends/PinNoteDialog'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'
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
  // EXEC-04b: the label is "Execution time"; the id stays `durations`, so every link to the tab still opens it.
  { id: 'durations', label: 'Execution time' },
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
  durations: ['trends-execution', 'trends-duration'],
  heatmap: ['trends-heatmap'],
}

// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'HEALTHY' | 'MIXED' | 'INSUFFICIENT' | 'DECLINING' | 'PENDING'
const VERDICTS: readonly Verdict[] = ['HEALTHY', 'MIXED', 'INSUFFICIENT', 'DECLINING', 'PENDING']

/** The server's verdict as one this page can draw; an unknown word is "not available". */
function asVerdict(value: string | null | undefined): Verdict | null {
  return (VERDICTS as readonly string[]).includes(value ?? '') ? (value as Verdict) : null
}

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

/** What the score disclosure says while the server's summary is loading, missing or malformed. */
export const SCORE_NOT_AVAILABLE = 'Score not available'

// ── Cadence model ──────────────────────────────────────────────────────────
// TRND-06: what the page draws that is NOT a verdict — the cadence cells, the
// window's totals and the KPI values — from the trend tail it already reads.
// The verdict, the confidence score and its four dimensions, the variance and
// the run gap are the server's (`TrendSummary`), computed there over the same
// two reads; this page no longer derives them.

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

interface CadenceModel {
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
  /** The window's pass rate on the evaluated basis; 0 when nothing was evaluated (the KPI prints it). */
  passRate: number
  cadenceCells: CadenceCell[]
  lastRunIso: string | null
  previousRunIso: string | null
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

function computeCadenceModel(trend: TrendPoint[], days: number): CadenceModel {
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

  const isos = activeCells.map(c => c.iso).sort()
  const lastRunIso     = isos.length > 0 ? isos[isos.length - 1] : null
  const previousRunIso = isos.length > 1 ? isos[isos.length - 2] : null

  return {
    daysWithRuns, windowDays: days, emptyDays,
    totalExecutions, passedExecutions, failedExecutions, skippedExecutions, brokenExecutions,
    evaluatedExecutions,
    passRate,
    cadenceCells,
    lastRunIso, previousRunIso,
  }
}

/** The server's gap when it is long enough to call out (a week or more), else `null`. */
function calloutGap(summary: TrendSummary | null): TrendSummaryGap | null {
  const gap = summary?.gap ?? null
  return gap && gap.silent_days >= 7 && gap.start && gap.end ? gap : null
}

// ── Score disclosure ──────────────────────────────────────────────────────
/**
 * What the verdict card held, in the collapsed "How this score is computed"
 * disclosure below the hero (P3): the verdict and its one-line summary, the
 * words that say how to read it, the 0-100 confidence score on the kit's
 * meter and the four weighted dimensions it is computed from — all from the
 * server's trend summary (TRND-06). The card's issue rows are not here: each
 * repeated a fact the page already shows (the gap: the cadence card and the
 * schedule callout; too few days: the pass-rate frame's takeaway; the
 * executions: the KPI strip).
 */
function ScoreDetails({
  summary, verdict, headline, lede,
}: {
  summary: TrendSummary
  verdict: Verdict
  headline: React.ReactNode
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
        <h2 className="text-md font-semibold m-0 mb-1.5 text-[var(--color-text)]">
          <span aria-label={`Verdict: ${t.label}`} style={{ color: t.gateText }}>{t.label}</span>
          <span className="text-[var(--color-text-muted)] mx-2">·</span>
          <span>{headline}</span>
        </h2>
        <p className="text-sm m-0 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
          {lede}
        </p>
      </div>
      <div className="flex flex-col gap-3.5 min-w-0">
        <ConfidenceMeter score={summary.confidence.score} verdict={verdict} />
        <DimensionGrid dimensions={summary.confidence.dimensions} />
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

function ConfidenceMeter({ score, verdict }: { score: number; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const pillLabel = score >= 66 ? 'High' : score >= 33 ? 'Moderate' : 'Low'
  return (
    <div>
      <div className="eyebrow uppercase mb-1.5 text-[var(--color-text-muted)]">
        Trend confidence
      </div>
      <div className="flex items-end justify-between">
        <div>
          <span className="font-bold tabular-nums leading-none" style={{ fontSize: 'var(--text-display-lg)', color: t.meter, letterSpacing: '-0.02em' }}>
            {verdict === 'PENDING' ? '—' : score}
          </span>
          <span className="text-sm text-[var(--color-text-muted)] ml-1">/ 100</span>
        </div>
        <span
          className="inline-flex items-center px-2.5 py-1 rounded-full text-2xs font-semibold"
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

function DimensionGrid({ dimensions }: { dimensions: readonly TrendSummaryDimension[] }) {
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
      {dimensions.map(d => <DimensionTile key={d.id} dim={d} />)}
    </div>
  )
}

/** A weighted dimension: the one KPI tile at `sm` (W29b UIX-09) — its weight, its score in its tone, the score's bar. */
function DimensionTile({ dim }: { dim: TrendSummaryDimension }) {
  return (
    <MetricCard
      size="sm"
      title={dim.label}
      note={`${Math.round(dim.weight * 100)}%`}
      metric={{ value: Math.round(dim.score) }}
      tone={toneFor(dim.score)}
      bar={dim.score}
    />
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

function CadenceHeatmap({
  model, gap, onPin, onSelectRange,
}: {
  model: CadenceModel
  /** The server's run gap, when it is a week or more (the gap row). */
  gap: TrendSummaryGap | null
  /** TRND-04: opens Pin a note (QA engineer+, one project); absent = no button. */
  onPin?: () => void
  /** TRND-04: a mouse range on the strip opens Pin a note on those days. */
  onSelectRange?: (fromIso: string, toIso: string) => void
}) {
  const cells = useMemo(() => model.cadenceCells.map(cadenceStripCell), [model.cadenceCells])
  const counts = countTones(cells)
  const activeCount = cells.length - counts.none
  const emptyCount = counts.none
  const failingCount = counts.mixed

  // P3: the explainer paragraph that sat over the strip is the strip's
  // legend and its title's tooltip now (spec §2: explanatory text is never a
  // paragraph on the page). TRND-04: `Pin a note` sits in the header's right
  // slot, so the card grows no taller.
  return (
    <Panel
      title={`Run cadence — last ${model.windowDays} days`}
      titleHint={onSelectRange ? `${CADENCE_HINT} Drag across days to pin a note on them.` : CADENCE_HINT}
      actions={
        <>
          <span>{activeCount} day{activeCount === 1 ? '' : 's'} with runs · {emptyCount} empty</span>
          {onPin ? (
            <button type="button" data-pin-note-open="cadence" onClick={onPin} className={PIN_BUTTON}>
              <StickyNote aria-hidden="true" className="h-3.5 w-3.5" />
              Pin a note
            </button>
          ) : null}
        </>
      }
    >
      <div className="px-4 pt-3 pb-4">
        <DayStrip
          mode="presence"
          cells={cells}
          gap={4}
          title="Run cadence"
          label={`Run cadence: ${emptyCount} empty days, ${activeCount} day${activeCount === 1 ? '' : 's'} with executions, ${failingCount} with failures`}
          onSelectRange={onSelectRange}
        />

        {gap && (
          <div
            data-cadence-gap=""
            className="mt-3 grid items-center gap-2.5 rounded-md text-xs"
            style={{
              gridTemplateColumns: 'auto 1fr',
              padding: '10px 12px',
              background: 'var(--gate-conditional-bg-soft)',
              border: '1px solid var(--gate-conditional-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <span
              className="eyebrow uppercase inline-flex items-center px-1.5 py-0.5 rounded-sm"
              style={{ background: 'var(--gate-conditional-bg)', color: 'var(--ui-warning-ink)' }}
            >
              Gap
            </span>
            <span>
              {gap.silent_days}-day silence between <code className="font-mono text-2xs">{shortDate(gap.start)}</code> and <code className="font-mono text-2xs">{shortDate(gap.end)}</code>.
            </span>
          </div>
        )}
      </div>
    </Panel>
  )
}

/** W27b: where a note form was opened from, so it renders right after that card. */
type PinOrigin = 'cadence' | 'gap' | 'hero'
interface PinRequest {
  at: PinOrigin
  range: { from: string; to: string } | null
  note: TimelineNote | null
}
/** One slot key per place and days (or note): another place or other days is another form. */
function pinRequestKey(request: PinRequest): string {
  return request.note
    ? `${request.at}:note:${request.note.id}`
    : `${request.at}:${request.range?.from ?? ''}:${request.range?.to ?? ''}`
}

/**
 * A small header button (the cadence card's `Pin a note`, the callout's). 18 px
 * tall, the height of the header's own text line (the panel title's 16.8 px
 * line, the callout heading's 18.6 px), so the card grows no taller
 * (`fold-budget`: /trends has 35 px of headroom).
 */
const PIN_BUTTON =
  'inline-flex h-[18px] items-center gap-1 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-1.5 text-xs font-medium leading-none text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]'

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
}: { trend: TrendPoint[]; days: number; model: CadenceModel; filtersApplied: boolean }) {
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
/**
 * W11 GATE-04: the dashed line is the policy's GO line (`yellow_min`) and the
 * zone below `orange_min` is shaded — from the summary's `readiness_policy`,
 * or the default bands (95 / 90), labelled. The legend carries only the two
 * short labels (it must not wrap); the policy's NAME is in the takeaway.
 */
function goLineTarget(bands: ReadinessBands): TimeSeriesRateTarget {
  return {
    value: bands.goMin,
    label: goLineLabel(bands),
    floor: { value: bands.noGoBelow, label: noGoZoneLabel(bands) },
  }
}
/** The hero's plot height, px (spec §2: the primary chart; it was 240 in the two-column body). */
const HERO_HEIGHT = 280
const NO_EVALUATED_DAY = 'no day in this window has an evaluated execution'
const NO_NOTES: readonly NoteInput[] = []

/**
 * The window's pass rate per day, on the kit's time-series chart: the
 * endpoint's own `pass_rate` (gated on evaluated executions, so a day of only
 * skips is a gap), a gap for a day with no runs, the execution count as bars
 * and the GO line as a dashed line with a legend entry (W11 GATE-04: the
 * No-Go zone shaded under it). TRND-04: the window's timeline notes travel in
 * the model.
 *
 * The old card's header block — a big number labelled "today", a target pill
 * and a delta — becomes the frame's one-line takeaway: the number was the
 * WINDOW's rate, not today's, and the pill is now the target line's legend.
 */
function passRateChart(points: TimeSeriesPoint[], releases?: readonly ReleaseInput[], notes: readonly NoteInput[] = NO_NOTES) {
  const built = buildTimeSeriesModel({ points, releases, notes })
  const measured = built.points.some((point) => point.rate !== null)
  const frameState: ChartState<unknown> = measured
    ? readyState(built)
    : { status: 'not-measured', reason: NO_EVALUATED_DAY, meta: null }
  return { chart: built, state: frameState }
}

function PassRateTrend({
  trend, model, days, notes, toolbar, bands,
}: {
  trend: TrendPoint[]
  model: CadenceModel
  days: number
  /** TRND-04: the window's notes, already filtered to the page's suite. */
  notes: readonly NoteInput[]
  /** The frame's toolbar content (the `Notes (n)` chip). */
  toolbar?: React.ReactNode
  /** W11 GATE-04: the GO line and the No-Go zone (the summary's policy, or the default bands). */
  bands: ReadinessBands
}) {
  const points = useMemo(() => {
    const cells = model.cadenceCells
    return timeSeriesFromTrends(
      trend.map((p) => ({ ...p, date: p.date.slice(0, 10) })),
      { from: cells[0]?.iso, to: cells[cells.length - 1]?.iso },
    )
  }, [trend, model.cadenceCells])

  const activeDays = model.daysWithRuns
  let takeaway: string | undefined
  if (model.evaluatedExecutions > 0) {
    const versus = versusGoLine(model.passRate, bands)
    const sparse = activeDays < 3 ? ` · ${activeDays} day${activeDays === 1 ? '' : 's'} with data, too few for a trend` : ''
    takeaway = `${model.passRate.toFixed(1)}% over the last ${days} days, ${versus}${sparse}`
  }
  const rateTarget = useMemo(() => goLineTarget(bands), [bands])

  return <PassRateTrendAnalysis points={points} takeaway={takeaway} notes={notes} toolbar={toolbar} rateTarget={rateTarget} />
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
function PassRateTrendAnalysis({
  points, takeaway, notes, toolbar, rateTarget,
}: {
  points: TimeSeriesPoint[]
  takeaway: string | undefined
  notes: readonly NoteInput[]
  toolbar?: React.ReactNode
  /** W11 GATE-04: the GO line with its No-Go floor. */
  rateTarget: TimeSeriesRateTarget
}) {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const { data: releaseList } = useReleases(undefined, { cached: true })
  const releases = useMemo(
    () => (activeProjectId === ALL_PROJECTS_ID ? [] : releaseMarkerInputs(releaseList?.items ?? [])),
    [activeProjectId, releaseList],
  )
  const { chart, state } = useMemo(() => passRateChart(points, releases, notes), [points, releases, notes])
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
        rateTarget={rateTarget}
        trendAnalysis
        zoom
        toolbar={toolbar}
      />
    </div>
  )
}

// ── Schedule-paused callout ───────────────────────────────────────────────
// The window's longest silence, stated as the dates it covers — the SERVER's
// gap (TRND-06). The gap is the LONGEST one, not necessarily the trailing one:
// only a gap that runs up to today says "since"; an older gap that runs have
// since ended is a past silence. TRND-04: a QA engineer+ can pin a note on
// those days.
//
// W23a TRND-03: no schedule is assumed. The server's `expected_runs_missed`
// (= silent days) is NOT rendered, and "Expected runs missed" / "Action
// needed" appear only when a CONFIGURED cadence reports misses (the exact
// count, no `~`). Without one the callout says so and links to Settings ›
// Expected runs. The status is read here, i.e. on the Volume tab only.
function SchedulePausedCallout({
  gap, onPin, projectId, days, canConfigure,
}: {
  gap: TrendSummaryGap | null
  onPin?: (from: string, to: string) => void
  /** The active project, or null in All Projects (no status read). */
  projectId: string | null
  days: number
  /** Settings › Expected runs is a QA lead's (canAccessManagement). */
  canConfigure: boolean
}) {
  // The hook's `error` covers a failed read AND a malformed answer.
  const { data: status, error: statusError } = useRunCadenceStatus(projectId, days)
  const model = calloutModel(gap, status, canConfigure, Boolean(projectId && statusError && !status))
  if (!model.show) return null
  const ongoing = gap?.ongoing ?? false
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
        <h3 id="schedpaused" className="text-sm font-semibold m-0 text-[var(--color-text)]">
          {model.title}
        </h3>
        <div className="flex items-center gap-2">
          {gap && onPin ? (
            <button type="button" data-pin-note-open="gap" onClick={() => onPin(gap.start, gap.end)} className={PIN_BUTTON}>
              <StickyNote aria-hidden="true" className="h-3.5 w-3.5" />
              Pin a note for these days
            </button>
          ) : null}
          {model.actionNeeded && (
            <span
              data-callout-action-needed=""
              className="eyebrow uppercase inline-flex items-center px-1.5 py-0.5 rounded-full"
              style={{ background: 'var(--gate-conditional-bg)', color: 'var(--ui-warning-ink)' }}
            >
              {ACTION_NEEDED}
            </span>
          )}
        </div>
      </div>
      {gap && (
        <p className="text-sm m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
          {ongoing
            ? <>No executions have landed since <code className="font-mono text-xs">{shortDate(gap.start)}</code>.</>
            : <>No executions landed from <code className="font-mono text-xs">{shortDate(gap.start)}</code> to <code className="font-mono text-xs">{shortDate(gap.end)}</code>.</>}
        </p>
      )}
      {model.tiles.length > 0 && (
        <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
          {model.tiles.map((tile) => <CalloutStat key={tile.label} label={tile.label} value={tile.value} />)}
        </div>
      )}
      {model.lines.length > 0 && (
        <ul data-callout-cadence="" className="m-0 mt-3 list-none space-y-1 p-0">
          {model.lines.map((line) => (
            <li key={line.kind === 'configure' ? line.to : line.text} className="text-sm text-[var(--color-text-secondary)] break-words">
              {line.kind === 'configure'
                // Settings › Expected runs is single-project (config/routeScope.ts): the scoped link.
                ? <>{line.text}<ScopedLink to={line.to} className="text-[var(--color-accent)] hover:underline" hintInTitleOnly>{line.linkLabel}</ScopedLink></>
                : line.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** A callout's stat: the one KPI tile at `sm` (W29b UIX-09), in the danger ink (a silence is the bad news here). */
function CalloutStat({ label, value }: { label: string; value: string | number }) {
  return <MetricCard size="sm" title={label} metric={{ value }} tone="bad" />
}

// ── Suite pass rates ──────────────────────────────────────────────────────
/**
 * Each suite's pass rate over the page's window (the coverage read is asked
 * for the same `days`), at most the first {@link SUITE_ROWS_SHOWN} suites with
 * executions. The card once said "· today" / "Single day" / "No delta
 * available — single day of data." whatever window was picked: it is the
 * window's rate, and it says so.
 *
 * TRND-06: each row draws the suite's daily pass rate as a sparkline — the
 * SAME line "Pass rate by suite" draws, from the SAME request, asked once the
 * card is near (`LazySection`). The sparkline is a LAZY module
 * (`SuiteRateSparkline`): the request pipeline it needs is section-only code
 * that must stay out of this page's static closure
 * (`sectionOnlyModules.test.ts`). A suite outside that chart's 7 busiest reads
 * "—" with the reason. TRND-02: each row links to the latest two runs of its
 * suite compared (one project only).
 */
const SUITE_ROWS_SHOWN = 6
/** The card's placeholder before it is near: its header and six rows, px. */
const SUITE_RATES_MIN_HEIGHT = 360
/** The row sparkline's box: the full row width, 18 px tall (reserved, so nothing moves when it draws). */
const ROW_SPARK_HEIGHT = 18
const SuiteRateSparkline = lazyWithRetry(() => import('@/components/trends/SuiteRateSparkline'))

function SuitePassRates({
  suites, days, suiteFilter, allProjects,
}: {
  suites: CoverageSuite[]
  days: number
  suiteFilter: ScopeValue
  allProjects: boolean
}) {
  return (
    <LazySection label="trends-suite-rates" minHeight={SUITE_RATES_MIN_HEIGHT}>
      <SuitePassRatesCard suites={suites} days={days} suiteFilter={suiteFilter} allProjects={allProjects} />
    </LazySection>
  )
}

function SuitePassRatesCard({
  suites, days, suiteFilter, allProjects,
}: {
  suites: CoverageSuite[]
  days: number
  suiteFilter: ScopeValue
  allProjects: boolean
}) {
  const withRuns = Array.isArray(suites) ? suites.filter(s => (s.passed + s.failed + s.skipped) > 0) : []
  const filtered = withRuns.slice(0, SUITE_ROWS_SHOWN)
  return (
    <div data-trends-suite-rates="">
      <Panel
        title={`Suite pass rates — last ${days === 1 ? '24 hours' : `${days} days`}`}
        titleHint="Passed of evaluated executions per suite across the window; skips are excluded. The line is the suite's daily pass rate."
        actions={withRuns.length > filtered.length ? <span>{filtered.length} of {withRuns.length} suites</span> : undefined}
      >
        <div className="px-4 pt-2 pb-4">
          {filtered.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)] py-2 text-center m-0">No suite data for the selected window.</p>
          ) : (
            <div className="flex flex-col">
              {filtered.map((s, i) => (
                <SuiteRow
                  key={s.suite_name}
                  suite={s}
                  isLast={i === filtered.length - 1}
                  days={days}
                  suiteFilter={suiteFilter}
                  compareHref={allProjects ? null : latestTwoHref(s.suite_name)}
                />
              ))}
            </div>
          )}
        </div>
      </Panel>
    </div>
  )
}

function SuiteRow({
  suite, isLast, days, suiteFilter, compareHref,
}: {
  suite: CoverageSuite
  isLast: boolean
  days: number
  suiteFilter: ScopeValue
  compareHref: string | null
}) {
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
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          {/* The suite's own page (UX redesign P4): its tests, runs and charts. */}
          <Link
            to={suiteHrefByName(suite.suite_name)}
            className="font-mono text-sm truncate hover:underline"
            title={suite.suite_name}
            style={{ color: tone === 'bad' ? 'var(--status-failed)' : 'var(--color-text)' }}
          >
            {suite.suite_name}
          </Link>
          {compareHref ? (
            <Link
              to={compareHref}
              data-suite-compare=""
              aria-label={`Compare the latest two runs of ${suite.suite_name}`}
              title={`Compare the latest two runs of ${suite.suite_name}`}
              className="inline-flex shrink-0 items-center text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              <GitCompare aria-hidden="true" className="h-3.5 w-3.5" />
            </Link>
          ) : null}
        </div>
        <div className="mt-1" style={{ height: ROW_SPARK_HEIGHT }}>
          <Suspense fallback={null}>
            <SuiteRateSparkline days={days} suiteFilter={suiteFilter} suiteName={suite.suite_name} tone={tone} height={ROW_SPARK_HEIGHT} />
          </Suspense>
        </div>
      </div>
      <span className="text-right">
        <span className="text-sm font-semibold tabular-nums" style={{ color: pctColor }}>{pct}%</span>
        <div className="text-2xs text-[var(--color-text-muted)] tabular-nums">{suite.passed} / {evaluated} evaluated</div>
      </span>
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
  const [, setSearchParams] = useSearchParams()

  // TRND-10: the page's customised charts ride in its saved views
  // (`filters.charts`), ONLY when one is not at its default. This browser's
  // own customisations are seeded into the store first, so a view saved from
  // the Volume tab (neither chart mounted) still carries them.
  useEffect(() => {
    seedChartConfig(SUITE_SERIES_CHART_KEY, SERIES_CONFIG_MODEL)
    seedChartConfig(COMPARE_CHART_KEY, COMPARE_CONFIG_MODEL)
  }, [])
  const appliedCharts = useChartConfigStore((s) => s.applied)
  const chartsKey = JSON.stringify(nonDefaultOf(appliedCharts, TRENDS_CHART_KEYS))
  const extraFilters = useMemo(() => {
    const charts = JSON.parse(chartsKey) as Record<string, unknown> | null
    return charts ? { charts } : undefined
  }, [chartsKey])
  const applyExtra = useCallback(
    (view: SavedView) => {
      const charts = (view.filters as { charts?: unknown } | null | undefined)?.charts ?? null
      chartConfigStore.applyView(charts, TRENDS_CHART_KEYS)
      // Compare's chosen suites / releases live in the URL: the view's go there.
      const compareRaw = charts && typeof charts === 'object' ? (charts as Record<string, unknown>)[COMPARE_CHART_KEY] : null
      const compare = readCompareConfig(compareRaw).config
      setSearchParams((current) => urlWithCompareConfig(current, compare), { replace: true })
    },
    [setSearchParams],
  )
  // P1: this page's saved views (the top-bar release, the window, the suite; TRND-10 the charts).
  const viewsMenu = useReportViewsMenu({
    route: '/trends',
    windowDays: days,
    windowOptions: WINDOWS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
    extraFilters,
    applyExtra,
  })
  // Whether a filter narrowed the trend (a suite, or a release): an all-zero
  // window then keeps the frame's filter words; without one the frame states
  // the window neutrally, "No executions in this window" (R1 F3).
  const releaseScope = useReleaseScope()
  const trendFiltered = scopeArg(suiteFilter) !== null || scopeArg(releaseScope) !== null
  const { options: suiteOptions } = useSuiteOptions(days)
  // P2: the catalogue's "Filter page by this" writes a suite mark to the select above.
  const suiteTarget = usePageSuiteTarget(selectedSuite, suiteOptions, setSelectedSuite)
  const { isQaLead, isQaEngineer } = usePermissions()

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
  // TRND-06: the verdict, the confidence score, the variance and the run gap
  // are the server's, over the same two reads; `null` = not available.
  const { summary } = useTrendSummary(days, suiteFilter)

  const trend: TrendPoint[] = useMemo(() => (Array.isArray(trendsData?.data) ? trendsData.data : []), [trendsData])
  const suites: CoverageSuite[] = useMemo(() => (Array.isArray(coverageData?.suites) ? coverageData.suites : []), [coverageData])

  const model = useMemo(() => computeCadenceModel(trend, days), [trend, days])
  const verdict = asVerdict(summary?.confidence.verdict)
  const gap = calloutGap(summary)
  // W11 GATE-04: the GO line rides the page's existing summary read (no new
  // request); no `readiness_policy` (no evidence, an old cache entry) = the
  // default bands, labelled as such.
  const readinessPolicy = dashSummary?.readiness_policy
  const bands = useMemo(() => readinessBands(readinessPolicy), [readinessPolicy])

  // TRND-04: the window's timeline notes (UTC days, one project only), and
  // only the ones for this page's suite (a project-wide note always shows).
  const pageSuite = typeof suiteFilter === 'string' && suiteFilter.trim() ? suiteFilter.trim() : null
  const notesRange = useMemo(() => {
    const to = utcDayIso()
    return { from: shiftDayIso(to, -(days - 1)), to }
  }, [days])
  const { notes: windowNotes, truncated: notesTruncated } = useTimelineNotes(isAllProjects ? null : notesRange, pageSuite)
  const shownNotes = useMemo(
    () => (Array.isArray(windowNotes) ? windowNotes : []).filter((note) => noteInputsFor([note], pageSuite).length > 0),
    [windowNotes, pageSuite],
  )
  const noteInputs = useMemo(() => noteInputsFor(shownNotes, pageSuite), [shownNotes, pageSuite])
  const canPin = isQaEngineer && !isAllProjects
  // W27b PAN-05b: the note form opens IN THE PAGE, right after the card whose
  // control opened it (`at`): the cadence card (its Pin a note, a drag on its
  // strip), the run-gap callout, or the hero (the Notes chip's Edit). One note
  // form at a time (the page's note slot): opening it elsewhere, or on other
  // days, moves it there — asking first when its text was typed.
  const noteSlot = useInlineSlot<string>()
  const closeNote = useSlotCloser(noteSlot)
  const openNoteSlot = noteSlot.open
  const [pinRequests, setPinRequests] = useState<Record<string, PinRequest>>({})
  const pinAt = useCallback((request: PinRequest) => {
    const key = pinRequestKey(request)
    setPinRequests((current) => ({ ...current, [key]: request }))
    openNoteSlot(key)
  }, [openNoteSlot])
  const pinning = noteSlot.openKey !== null ? (pinRequests[noteSlot.openKey] ?? null) : null
  const setPinning = useCallback(
    (at: PinOrigin, request: { range: { from: string; to: string } | null; note: TimelineNote | null }) => pinAt({ at, ...request }),
    [pinAt],
  )
  const pinCadenceRange = useCallback((from: string, to: string) => setPinning('cadence', { range: { from, to }, note: null }), [setPinning])
  const pinGapRange = useCallback((from: string, to: string) => setPinning('gap', { range: { from, to }, note: null }), [setPinning])
  // The cadence and gap forms live in the Volume tab: another tab takes them away (as Cancel would).
  const noteInVolume = pinning !== null && pinning.at !== 'hero'
  const closeAnyNote = noteSlot.close
  useEffect(() => {
    if (tab !== 'volume' && noteInVolume) closeAnyNote()
  }, [tab, noteInVolume, closeAnyNote])
  const guardNote = noteSlot.guard
  const changeTab = useCallback(
    (next: TrendsTab) => (noteInVolume && next !== 'volume' ? guardNote(() => setTab(next)) : setTab(next)),
    [noteInVolume, guardNote, setTab],
  )
  const noteForm = (at: PinOrigin) => {
    const key = noteSlot.openKey
    if (!pinning || pinning.at !== at || key === null) return null
    return (
      <PinNoteDialog
        key={key}
        range={pinning.range}
        note={pinning.note}
        suite={pageSuite}
        slot={noteSlot}
        onClose={() => closeNote(key)}
      />
    )
  }

  // TRND-01: the header ⋯ — Export PDF (every role), Email report (QA lead+).
  const [emailOpen, setEmailOpen] = useState(false)
  const [pdfBusy, setPdfBusy] = useState(false)

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

  // The score disclosure's words, from the server's summary.
  const summaryDays = summary?.days_with_runs ?? 0
  const summaryWindow = summary?.window_days ?? days
  const headline: React.ReactNode = (() => {
    if (verdict === 'PENDING')      return <>awaiting executions</>
    if (verdict === 'INSUFFICIENT') return <>only {summaryDays} of {summaryWindow} days {summaryDays === 1 ? 'has' : 'have'} executions</>
    if (verdict === 'HEALTHY')      return <>{summaryDays} active days · pass rate steady</>
    if (verdict === 'DECLINING')    return <>pass rate declined over the window</>
    return <>{summaryDays} active days · mixed signal</>
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      return <>No executions in the last {days} days. Run a workflow or extend the window to populate trend data.</>
    if (verdict === 'INSUFFICIENT')
      return <>The headline {(summary?.pass_rate ?? 0).toFixed(0)}% pass rate comes from {summaryDays === 1 ? 'a single day' : `${summaryDays} days`}. With {summary?.empty_days ?? 0} of the last {days} days empty, the page can't show a real trend — it shows {summaryDays === 1 ? 'one data point' : 'a few points'}. Resume the schedule or widen the window before reading anything into the numbers.</>
    if (verdict === 'HEALTHY')
      return <>Pass rate sits above target with low variance across {summaryDays} active days. Trend is stable.</>
    if (verdict === 'DECLINING')
      return <>Pass rate has dropped meaningfully from the start of the window to today. Investigate before treating the headline as the new normal.</>
    return <>Mixed signal across {summaryDays} active days — pass rate variance is moderate. Verify the recent failures before declaring a regression.</>
  })()
  const scoreSummary = !summary || !verdict
    ? SCORE_NOT_AVAILABLE
    : verdict === 'PENDING'
      ? 'Pending · no score yet'
      : `${VERDICT_THEME[verdict].label} · confidence ${summary.confidence.score} / 100`

  // The two KPI sparklines draw the window's real per-day series (OD-1); the
  // other three tiles had only decoration and carry none.
  const kpiSeries = kpiSparkSeries(model.cadenceCells)
  // Dashboard summary delta — used by KPI 1 when we have a baseline. TRND-05:
  // `pass_rate_delta_pp` is the move in percentage points against the previous
  // window of the same length. `trend` beside it is the RELATIVE change, which
  // this tile used to print as "pp" (80 % -> 88 % read "10pp", not 8); a
  // payload without the points shows no delta rather than that one.
  const passRateDelta = (() => {
    const pp = dashSummary?.avg_pass_rate_7d?.pass_rate_delta_pp
    return typeof pp === 'number' && Number.isFinite(pp) ? pp : null
  })()
  const previousWindowLabel = days === 1 ? 'previous 24 hours' : `previous ${days} days`
  const passRateTone = model.passRate >= 80 ? 'good' : model.passRate >= 50 ? 'warn' : 'bad'
  const passRateChange: { trend_direction: 'up' | 'down' | 'flat' | 'none'; trend_text: string } =
    model.daysWithRuns < 2
      ? { trend_direction: 'none', trend_text: 'single data point · no delta available' }
      : passRateDelta != null
        ? {
            trend_direction: passRateDelta > 0 ? 'up' : passRateDelta < 0 ? 'down' : 'flat',
            trend_text: `${Math.round(Math.abs(passRateDelta) * 10) / 10} pp vs ${previousWindowLabel}`,
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

  // TRND-01 / TRND-02: the header's ⋯ (one 32 px trigger in the existing action row).
  const exportPdf = async () => {
    if (isAllProjects || !activeProjectId) return
    setPdfBusy(true)
    try {
      const blob = await downloadTrendsPdf({ projectId: activeProjectId, days, suite: suiteFilter, release: releaseScope })
      downloadBlob(blob, trendsPdfFilename(project?.name, days))
      toast.success('Trends PDF downloaded')
    } catch (error) {
      toast.error(await refusalText(error, 'The Trends PDF could not be generated.'))
    } finally {
      setPdfBusy(false)
    }
  }
  const compare = compareRunsHref({ suite: pageSuite, allProjects: isAllProjects })
  const overflow: OverflowItem[] = [
    {
      label: isAllProjects ? 'Export PDF (pick a project)' : pdfBusy ? 'Generating PDF…' : 'Export PDF',
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void exportPdf(),
      disabled: isAllProjects || pdfBusy,
    },
    ...(isQaLead
      ? [{
          label: isAllProjects ? 'Email report… (pick a project)' : 'Email report…',
          icon: <Mail className="h-3.5 w-3.5" />,
          onClick: () => setEmailOpen(true),
          disabled: isAllProjects,
        }]
      : []),
    {
      label: compare.href ? 'Compare runs' : `Compare runs (${compare.reason})`,
      icon: <GitCompare className="h-3.5 w-3.5" />,
      href: compare.href ?? undefined,
      disabled: compare.href === null,
    },
  ]

  return (
    <PageShell className="space-y-4">
      {/* The page's one filter row is the header's right side, where the old
          header had it (and as Home has it): the suite, the window, then the
          saved Views (the one secondary button), then the ⋯. A row of its own
          put the hero 2 px past the fold budget at 1440 x 900. */}
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
        overflow={overflow}
      />

      {/* W27b: Email report opens IN THE PAGE, right under the header (its
          trigger is the header's ⋯); it reads the relay and the members only
          now, as the dialog did. */}
      {emailOpen && activeProjectId && !isAllProjects ? (
        <EmailReportDialog
          projectId={activeProjectId}
          days={days}
          suiteFilter={suiteFilter}
          release={releaseScope}
          suiteLabel={suiteLabel || null}
          onClose={() => setEmailOpen(false)}
        />
      ) : null}

      {/* The strip keeps the `Trend metrics` landmark other specs wait on.
          It wraps to fewer columns when its tiles cannot fit (KpiStrip). */}
      <section aria-label="Trend metrics">
        <KpiStrip>
          <MetricCard
            title="Pass rate"
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
            title="Days with runs"
            metric={{
              value: `${model.daysWithRuns} / ${model.windowDays}`,
              trend_direction: 'none',
              trend_text: model.daysWithRuns < model.windowDays * 0.5
                ? `${daysWithRunsPct}% — schedule may be paused`
                : `${daysWithRunsPct}% of window`,
            }}
          />
          <MetricCard
            title="Executions"
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
            title="Suites"
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
            title="Last run"
            metric={{
              value: lastRunRel,
              trend_direction: 'none',
              // "previous run was 1d prior" lost its last letters in the 12 px change line at 1280 px on CI's fonts
              // (W29b fix-kpi): the same words without "was" fit.
              trend_text: previousRunRel ? `previous run ${previousRunGapDays}d prior` : 'no prior run in window',
            }}
          />
        </KpiStrip>
      </section>

      {/* The page's one primary content: the hero (`data-primary` is on its section).
          TRND-04: the `Notes (n)` chip sits in the frame's existing toolbar. */}
      <PassRateTrend
        trend={trend}
        model={model}
        days={days}
        bands={bands}
        notes={noteInputs}
        toolbar={
          shownNotes.length > 0 ? (
            <TimelineNotesChip
              notes={shownNotes}
              truncated={notesTruncated}
              onEdit={(note) => setPinning('hero', { range: null, note })}
            />
          ) : undefined
        }
      />
      {noteForm('hero')}

      <Disclosure title="How this score is computed" summary={scoreSummary}>
        {summary && verdict ? (
          <ScoreDetails summary={summary} verdict={verdict} headline={headline} lede={lede} />
        ) : (
          <p data-score-unavailable="" className="m-0 text-sm text-[var(--color-text-secondary)]">
            {SCORE_NOT_AVAILABLE}: the trend summary could not be read. The charts and the KPIs above are unaffected.
          </p>
        )}
      </Disclosure>

      <div className="space-y-3">
        <Tabs ariaLabel="Trend views" items={TRENDS_TABS} value={tab} onChange={changeTab} />
        <div data-tab-panel={tab}>
          {tab === 'volume' && (
            <div className="flex flex-col gap-3.5 min-w-0">
              <CadenceHeatmap
                model={model}
                gap={gap}
                onPin={canPin ? () => setPinning('cadence', { range: { from: utcDayIso(), to: utcDayIso() }, note: null }) : undefined}
                onSelectRange={canPin ? pinCadenceRange : undefined}
              />
              {noteForm('cadence')}
              <SchedulePausedCallout
                gap={gap}
                onPin={canPin ? pinGapRange : undefined}
                projectId={isAllProjects ? null : activeProjectId}
                days={days}
                // Settings › Expected runs is a QA lead's (isQaLead = canAccessManagement).
                canConfigure={isQaLead}
              />
              {noteForm('gap')}
              <DailyBreakdown trend={trend} days={days} model={model} filtersApplied={trendFiltered} />
            </div>
          )}
          {tab === 'by-suite' && (
            // One column below 1024 px (VIZ-106); from lg the suite series and
            // Compare beside the Suite pass rates card.
            <div className="grid gap-3.5 items-start grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]">
              <CatalogueTab scope="by-suite" days={days} suiteFilter={suiteFilter} suiteTarget={suiteTarget} />
              <SuitePassRates suites={suites} days={days} suiteFilter={suiteFilter} allProjects={isAllProjects} />
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
      <SectionErrorBoundary message="Could not load charts">
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
