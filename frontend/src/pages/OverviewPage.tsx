import type { ReactNode } from 'react'
import { Suspense, useCallback, useMemo, useState } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, CheckCircle, Clock, TrendingUp,
} from 'lucide-react'
import Sparkline from '@/components/charts/Sparkline'
import GaugeBar, { type GaugeTone } from '@/components/charts/GaugeBar'
import { dayWindow } from '@/components/charts/dayStrip.model'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ScopedLink from '@/components/ui/ScopedLink'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { useDashboardSummary, useFailureCategories, useTrendData } from '@/hooks/useMetrics'
import { useValueMetricsKpi } from '@/hooks/useValueMetrics'
import { useRuns } from '@/hooks/useRuns'
import SuiteBadge from '@/components/ui/SuiteBadge'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { scopeArg } from '@/lib/scopeParams'
import { suiteSelectOptions } from '@/lib/scopeControls'
import FirstRunGuide from '@/components/onboarding/FirstRunGuide'
import RecentActivityPanel from '@/components/activity/RecentActivityPanel'
import { isFirstRunGuideDismissed, dismissFirstRunGuide } from '@/components/onboarding/firstRunSteps'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { describeEmptyWindow, formatAgeDays } from '@/utils/emptyWindow'
import { formatDuration, dayTimeAgo } from '@/utils/formatters'
import { utcDayIso } from '@/utils/calendarDay'
import { clsx } from 'clsx'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { TrendPoint } from '@/types/metrics'
import type { DashboardMetricValue, DashboardSummary } from '@/types/analytics'
import type { TestRun } from '@/types/runs'

// Wave 2.6 (VIZ-408): the catalogue sections, in their own chunk, mounted on
// every render since the chart flags were retired (Phase D S1).
const OverviewCatalogue = lazyWithRetry(() => import('@/components/reports/catalogue/OverviewCatalogue'))

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
  /** The pass-rate meter's tone (`GaugeBar`): its fill, and its tinted track. */
  meterTone: GaugeTone
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
    meterTone: 'good',
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
    meterTone: 'watch',
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
    meterTone: 'warn',
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
    meterTone: 'bad',
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
    meterTone: 'neutral',
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


// ── KPI tone ─────────────────────────────────────────────────────────────
type SparkTone = 'good' | 'warn' | 'bad' | 'neutral'

/** The KPI label's dot. Tokens only: the neutral dot follows the theme. */
const SPARK_COLOR: Record<SparkTone, string> = {
  good: 'var(--status-passed)',
  warn: 'var(--status-broken)',
  bad:  'var(--status-failed)',
  neutral: 'var(--color-text-muted)',
}

/** A pass rate's line runs on the whole percentage scale. */
const PASS_RATE_DOMAIN = [0, 100] as const
const formatSparkPercent = (value: number) => `${value.toFixed(1)}%`

// ── KPI card ─────────────────────────────────────────────────────────────
interface KpiProps {
  label: string
  value: string
  unit?: string
  delta?: { glyph: '▲' | '▼' | '▬'; text: string; tone: SparkTone }
  tone: SparkTone
  /**
   * One value per day, OLDEST first; `null` is a day with nothing measured
   * (the line breaks there). Drawn only with at least two measured days —
   * otherwise `emptyMsg` explains the missing trend line.
   */
  series?: readonly (number | null)[]
  /** The line's value range. Default `[0, max]`: a count trends up from zero, as it always has here. */
  sparkDomain?: readonly [number, number]
  /** How the line's accessible name prints a value. */
  sparkFormat?: (value: number) => string
  /** The window, in days, the line covers: part of its accessible name. */
  days: number
  emptyMsg?: string
  /** When set, renders a "View all →" footer linking to this route. */
  linkTo?: string
  /** Footer link label override (default: "View all"). */
  linkLabel?: string
  /** Rendered beside the label. Used to mark a card the global release filter
   *  does NOT reach, so a reader cannot take it for release-scoped just
   *  because the cards around it are. */
  badge?: ReactNode
}

/**
 * `white-space: nowrap` only while presentation mode is on (`data-presentation`
 * on <html>): the room's sizes broke values like "8m 32s" across two lines
 * (R2-13). Scoped to the mode so no desk layout, and no committed baseline,
 * can change.
 */
const PRESENTING_NOWRAP = '[[data-presentation=on]_&]:whitespace-nowrap'

function KpiCard({ label, value, unit, delta, tone, series, sparkDomain, sparkFormat, days, emptyMsg, linkTo, linkLabel, badge }: KpiProps) {
  const isBad = tone === 'bad'
  const dotColor = SPARK_COLOR[tone]
  const valueIsDash = value === '—'
  const measured = series ? series.filter((v): v is number => v !== null && Number.isFinite(v)) : []
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
        {badge}
      </div>
      {/* Presentation mode (R2-13): at room size "8m 32s" broke at its space
          and "+4.2%" broke under its triangle. There the value and the change
          each stay whole, and the change wraps under the value as one piece
          when the card is too narrow for both. Desk mode: no rule applies. */}
      <div className="flex items-baseline justify-between gap-1.5 [[data-presentation=on]_&]:flex-wrap">
        {/* Sizes are the type tokens of the SAME value (26 px = display-sm,
            22 px = stat-md), so nothing moves here, and presentation mode
            (VIZ-106) raises them by swapping the tokens. */}
        <span
          className={clsx(
            'tabular-nums leading-none',
            PRESENTING_NOWRAP,
            valueIsDash
              ? 'font-medium text-[var(--color-text-muted)]'
              : 'font-bold text-[var(--color-text)]',
          )}
          style={{
            letterSpacing: '-0.02em',
            fontSize: valueIsDash ? 'var(--text-stat-md)' : 'var(--text-display-sm)',
          }}
        >
          {value}
          {unit && !valueIsDash && (
            <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-0.5">{unit}</span>
          )}
        </span>
        {delta && (
          <span
            className={`text-[11px] font-semibold tabular-nums ${PRESENTING_NOWRAP}`}
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
      {series && measured.length >= 2 ? (
        <Sparkline
          series={series}
          label={`${label} per day, last ${days} days`}
          tone={tone}
          // The old line ran from 0 to the series' top; a zoomed [min, max]
          // would turn a 94-97% week into a cliff.
          domain={sparkDomain ?? [0, Math.max(1, ...measured)]}
          format={sparkFormat}
          area
          className="w-full"
        />
      ) : (
        <div
          // `min-h-7`, not `h-7`: a caption that wraps to two lines in a
          // narrow card (DejaVu Sans on Linux) ran into the link under it.
          className="min-h-7 pt-1.5 text-[11px] text-[var(--color-text-faint)]"
          style={{ borderTop: '1px dashed var(--color-border)' }}
        >
          {emptyMsg ?? '—'}
        </div>
      )}
      {linkTo && (
        // ScopedLink, not Link: this card's number can be a CROSS-project
        // aggregate while the destination shows one project at a time. The
        // link still works — the destination now offers a picker — but it says
        // so before the click instead of after it.
        <ScopedLink
          to={linkTo}
          containerClassName="self-start -mt-0.5"
          className="text-[11px] text-[var(--color-accent)] hover:underline"
        >
          {linkLabel ?? 'View all'} →
        </ScopedLink>
      )}
    </div>
  )
}

// ── Verdict card ─────────────────────────────────────────────────────────
interface VerdictCardProps {
  verdict: Verdict
  newFailures24h: number
  totalExecutions: number
  windowDays: number
  generatedLabel: string
  passRate: number
  /** F-067: the population the rate is over, from the API. */
  passRateBasisLabel?: string | null
}

function VerdictCard({
  verdict, newFailures24h, totalExecutions, windowDays,
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

  // F-067: name the POPULATION, not just "weighted". /overview counts every
  // execution and the Summary Report counts each distinct test once — 81.0%
  // vs 83.3% on the same window. Both are right; showing which is which is
  // what stops them reading as a contradiction. Falls back to the old copy
  // when the API omits it (a cached pre-#588 payload). It used to sit in the
  // "Pass rate" reason card, which P2 removed with its two siblings (each
  // repeated a KPI card); the basis is the one fact only it carried, so it
  // now sits under the pass rate it qualifies.
  const passRateBasis = `${passRateBasisLabel || 'weighted'} · ${windowDays}d`

  return (
    <section
      aria-label="Release readiness"
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
            {/* `<wbr />`: three adjacent spans with no space between them are
                ONE unbreakable run, and in DejaVu Sans (the Linux runner) the
                narrow card could not fit "Conditional · review" — the run
                slid under the pass-rate column instead of wrapping. */}
            <span style={{ color: t.gateText }}>{gate}</span>
            <wbr />
            <span className="text-[var(--color-text-muted)] mx-2">·</span>
            <wbr />
            <span>{t.headlineSuffix}</span>
          </h2>
          <p className="text-[13px] m-0 max-w-[56ch]" style={{ color: 'var(--color-text-secondary)' }}>
            {lede}
          </p>
        </div>

        <div className="flex flex-col items-end gap-1 shrink-0">
          <div
            className="font-bold tabular-nums leading-none"
            // `--text-display-sm` is 26 px: the same size, on the token presentation mode raises.
            style={{ color: t.meterValue, letterSpacing: '-0.02em', fontSize: 'var(--text-display-sm)' }}
          >
            {verdict === 'PENDING' ? '—' : `${passRatePct}%`}
          </div>
          <div
            className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]"
            style={{ letterSpacing: 'var(--tracking-wider)' }}
          >
            Pass rate
          </div>
          {/* PENDING has no pass rate to draw: an empty track, never a bar at 0. */}
          <GaugeBar
            className="mt-1"
            value={verdict === 'PENDING' ? null : passRatePct}
            label="Pass rate"
            size="sm"
            width={110}
            track="tint"
            tone={t.meterTone}
            format={(v) => `${v}%`}
          />
          {verdict !== 'PENDING' && (
            <div className="text-[11px] text-[var(--color-text-muted)] tabular-nums" data-testid="verdict-pass-rate-basis">
              {passRateBasis}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
        <Clock className="h-3.5 w-3.5" />
        <span>
          Verdict {generatedLabel}, from the{' '}
          <span className="font-medium text-[var(--color-text-secondary)]">release-readiness band</span>
        </span>
      </div>
    </section>
  )
}

// ── Trend window (the catalogue's day series and the KPI sparklines) ────
/**
 * The catalogue's headline chart height, mirrored here so its loading row
 * holds the same box without importing the lazy chunk
 * (`OVERVIEW_HEADLINE_HEIGHT` in `OverviewCatalogue.tsx`).
 */
const TREND_HEIGHT = 260

/** One UTC day of the window, and the payload's point for it (`null`: no runs that day). */
interface TrendDay {
  iso: string
  point: TrendPoint | null
}

/**
 * The window as one entry per UTC day, oldest first, ending today: the same
 * days Trends builds from the same endpoint (`buildCadenceCells`).
 *
 * `/metrics/trends` sends only the days that HAD runs (it groups by day and
 * zero-fills nothing), so drawing its points directly collapsed a week with no
 * runs to nothing: ten days and three days took the same width under a
 * "Day (UTC)" axis, and "last 30 days" sat over 23 columns (R2 F1). The days
 * are keyed by UTC day because a payload date may carry a time.
 */
function trendWindow(trends: readonly TrendPoint[], days: number): TrendDay[] {
  const byDay = new Map<string, TrendPoint>()
  for (const p of trends) byDay.set(p.date.slice(0, 10), p)
  return dayWindow(days, utcDayIso()).map((iso) => ({ iso, point: byDay.get(iso) ?? null }))
}

/** The catalogue row while its chunk loads: the headline row's two cards, holding their height. */
function CataloguePending() {
  return (
    <div className="grid grid-cols-1 xl:[grid-template-columns:minmax(0,1.6fr)_minmax(0,1fr)] gap-4" aria-busy="true">
      {[0, 1].map((slot) => (
        <div key={slot} className="card flex items-center justify-center" style={{ minHeight: TREND_HEIGHT + 110 }}>
          <LoadingSpinner />
        </div>
      ))}
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
  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames, suiteLabel } = usePageSuiteFilter()
  // P1: this page's saved views (the top-bar release, the window, the suite).
  const viewsMenu = useReportViewsMenu({
    route: '/overview',
    windowDays: days,
    windowOptions: TIME_OPTIONS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
  })
  // Whether a filter narrowed the trend (a suite, or a release): an all-zero
  // window then keeps the frame's filter words; without one the frame states
  // the window neutrally, "No executions in this window" (R1 F3).
  const releaseScope = useReleaseScope()
  const trendFiltered = scopeArg(suiteFilter) !== null || scopeArg(releaseScope) !== null
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

  // `error` is read, not just `data`/`isLoading`: without it a failed fetch is
  // indistinguishable from an empty window, and the page below asserts the
  // latter in so many words ("widening the time window will not help").
  const { data: summary, isLoading: summaryLoading, error: summaryError, mutate: retrySummary } =
    useDashboardSummary(days, suiteFilter)
  const { data: trends,  isLoading: trendsLoading  } = useTrendData(days, suiteFilter)
  // Failure-kind triad (US-9.2): the by-kind aggregation ships on the same
  // failure-categories payload /failures uses, so this KPI is one SWR-cached
  // fetch — no bespoke endpoint. The catalogue's Failure categories chart
  // draws this same read (one request, one SWR entry), and its
  // `error`/`isValidating` are passed to it below.
  const categoriesRead = useFailureCategories(days, suiteFilter)
  const failureCategories = categoriesRead.data
  const mutateCategories = categoriesRead.mutate
  const retryCategories = useCallback(() => {
    void mutateCategories()
  }, [mutateCategories])
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
  //
  // M22: both probes below exist to answer questions the windowed list above
  // already answers whenever it has a row: the project has had a run, and the
  // newest one is in the window. Each used to poll every 15 s regardless, so
  // the page made three /runs requests per cycle. They now fetch only once the
  // windowed list has come back EMPTY, the one case where it cannot answer.
  // Not on a failed or pending windowed fetch: that is not "empty".
  const windowHasRuns = recentRuns ? recentRuns.items.length > 0 : undefined
  const needProbes = windowHasRuns === false
  const { data: newestRunPage } = useRuns({ page: 1, size: 1 }, { enabled: needProbes })
  // "Has this project EVER had a run?" — a question about the project, not
  // about what the reader is currently filtered to. Separate from the query
  // above because that one feeds the empty-window message, which SHOULD stay
  // release-scoped: under a release filter, "your newest run is from <date>"
  // has to mean the newest run in that release or the suggestion is useless.
  //
  // With no release selected both calls carry identical params and SWR serves
  // them from one request.
  const { data: everHadRunPage } = useRuns(
    { page: 1, size: 1 },
    { ignoreGlobalRelease: true, enabled: needProbes },
  )
  const recentRunItems = useMemo<TestRun[]>(() => recentRuns?.items ?? [], [recentRuns?.items])
  const suiteOptions = useMemo(() => collectSuiteOptions(recentRunItems), [recentRunItems])
  const latestRun = useMemo(
    // OR within the suite dimension: the newest run carrying ANY selected
    // suite. With none selected `runHasSuite(run, '')` is true, as before.
    () =>
      recentRunItems.find((run) =>
        suiteNames.length <= 1
          ? runHasSuite(run, suiteNames[0] ?? '')
          : suiteNames.some((name) => runHasSuite(run, name)),
      ),
    [recentRunItems, suiteNames],
  )

  const emptyWindow = useMemo(
    () =>
      describeEmptyWindow({
        totalInWindow: summary?.total_executions_7d?.value as number | undefined,
        // Same ordering as the probe's single row, so the first windowed row
        // is the answer whenever there is one.
        newestRunAt: windowHasRuns
          ? recentRunItems[0]?.created_at ?? null
          : newestRunPage?.items?.[0]?.created_at ?? null,
        days,
        options: TIME_OPTIONS,
      }),
    [summary?.total_executions_7d?.value, windowHasRuns, recentRunItems, newestRunPage?.items, days],
  )

  const projectLabel = project?.name ?? 'All Projects'
  const scopeLabel = suiteLabel ? `${projectLabel} · ${suiteLabel}` : projectLabel
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
  const avgDurationMs = summary?.avg_duration_ms?.value as number | undefined

  // Infra-caused failure share (AI-classified failure-kind triad, US-9.2).
  // null when the window has no analyzed failures — the KPI renders "—".
  const byKind = (failureCategories as { by_kind?: { kind: string; count: number }[] } | undefined)?.by_kind ?? []
  const kindTotal = byKind.reduce((s, k) => s + k.count, 0)
  const infraKindCount = byKind.find(k => k.kind === 'infrastructure')?.count ?? 0
  const infraPct = kindTotal > 0 ? Math.round((infraKindCount / kindTotal) * 100) : null

  const verdict = mapReadinessToVerdict(summary?.release_readiness_band, summary?.release_readiness, totalExecutions)
  // The window the verdict covers, not "just now": that was the render time,
  // not when the (cacheable) summary behind it was computed.
  const generatedLabel = totalExecutions > 0 ? `for the last ${days} days` : `awaiting data · last ${days} days`
  const lastRunLabel = trendData.length > 0
    ? dayTimeAgo(trendData[trendData.length - 1].date)
    : '—'
  const verdictDotColor = verdict === 'GO' ? 'var(--status-passed)'
    : verdict === 'CONDITIONAL' ? 'var(--status-skipped)'
    : verdict === 'NO_GO' ? 'var(--gate-no-go)'
    : 'var(--color-text-faint)'

  // First-run: a project (or the whole instance) that has NEVER had a run gets
  // a getting-started guide instead of a zeroed-out dashboard. Dismissible
  // (persisted per browser).
  //
  // Gated on the window-independent newest-run fetch, never on the windowed
  // summary. Those two disagree for any project whose last run predates the
  // selected window, and reading the windowed one told an established project
  // with months of history "Welcome to TestLookup — no test runs here yet"
  // because nobody had pushed in 24 hours. The window-empty case already has
  // its own banner (`overview-empty-window`), which names the age of the real
  // data and offers a wider window; this guide would render on top of it,
  // contradicting it.
  //
  // `undefined` means the fetch has not resolved, which is NOT "no runs" — so
  // require it to have loaded. A failed fetch also stays undefined, hiding the
  // guide rather than falsely welcoming someone to a project they have used
  // for months.
  // Reported: /overview?release=unattributed on a project that HAS runs but
  // none in that bucket rendered "Welcome to TestLookup — no test runs here
  // yet" with the full setup wizard. An empty FILTER read as an empty PROJECT.
  const everHadRun = windowHasRuns === true || (everHadRunPage?.items?.length ?? 0) > 0
  const newestRunLoaded = windowHasRuns === true || everHadRunPage !== undefined
  const isFreshInstall = !summaryLoading && newestRunLoaded && !everHadRun
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

  // The KPI sparklines read the same window as the execution trend: one value
  // per UTC day, so a week with no runs keeps its width instead of the line
  // bridging it (R2 F1). A day with no runs ran nothing, so its counts are a
  // measured 0; it evaluated nothing, so it has no pass rate: `null`, a break
  // in the line, never a 0% that reads as a collapse. So does a day of only
  // skips.
  const trendDays = trendWindow(trendData, days)
  const daysWithData = trendDays.filter((d) => d.point !== null).length
  const totalSeries = trendDays.map(({ point: p }) => (p ? p.passed + p.failed + p.skipped + (p.broken ?? 0) : 0))
  const passRateSeries = trendDays.map(({ point: p }) =>
    p && p.passed + p.failed + (p.broken ?? 0) > 0 && typeof p.pass_rate === 'number' ? Math.round(p.pass_rate * 100) / 100 : null,
  )
  const measuredPassDays = passRateSeries.filter((v) => v !== null).length
  const failedSeries = trendDays.map(({ point: p }) => (p ? p.failed : 0))
  // A count line needs two days that HAD runs: the zeros filled in for quiet
  // days are real, but a line of them around one day of data is not a trend.
  const countSeries = (series: number[]) => (daysWithData >= 2 ? series : undefined)

  if (!project && !isAllProjects) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <TrendingUp className="h-12 w-12 text-[var(--color-text-faint)] mb-3" />
        <p className="text-[var(--color-text-muted)] font-medium">Select a project to view the dashboard</p>
        <p className="text-[var(--color-text-muted)] text-sm mt-1">Use the project selector in the top bar</p>
      </div>
    )
  }

  // A failed summary fetch must not fall through to the empty-window notice
  // below, which would tell an operator mid-outage that they have never
  // ingested a run and that widening the window will not help.
  if (summaryError && !summary) {
    return (
      <DataUnavailable
        error={summaryError}
        onRetry={() => void retrySummary()}
        testId="overview-data-unavailable"
      />
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
          {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
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
              {/* The selected suite even when this page's options (recent
                  runs) do not list it — else "All suites" shows while the
                  page is filtered (m4). */}
              {suiteSelectOptions(suiteOptions, selectedSuite).map((suite) => (
                <option key={suite} value={suite}>{suite}</option>
              ))}
            </select>
          </label>
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

      {/* Top row — the release-readiness verdict, the full width of the page.
          (The "Quality workflow" ribbon beside it showed four invented stages
          re-stating the verdict's and the KPIs' numbers; P2 removed it.) */}
      {summaryLoading && !summary ? (
        <div className="card flex items-center justify-center min-h-[260px]">
          <LoadingSpinner />
        </div>
      ) : (
        <SectionErrorBoundary message="Failed to load release readiness">
          <VerdictCard
            verdict={verdict}
            newFailures24h={newFailures}
            totalExecutions={totalExecutions}
            windowDays={days}
            generatedLabel={generatedLabel}
            passRate={passRate}
            passRateBasisLabel={summary?.avg_pass_rate_7d?.basis_label}
          />
        </SectionErrorBoundary>
      )}

      {/* KPI strip: the seven dashboard KPIs, always (the widget picker that
          could hide them is gone). Presentation mode: four cards a row at xl,
          not six. A 40 px "8m 32s" and its change do not fit a sixth of the
          row at 1280 (R2-13). */}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 [[data-presentation=on]_&]:xl:grid-cols-4 gap-2.5">
        <KpiCard
          label="Total executions"
          value={`${totalExecutions}`}
          tone="neutral"
          days={days}
          series={countSeries(totalSeries)}
          emptyMsg={sparklineHint(daysWithData)}
          delta={deltaFromMetric(summary?.total_executions_7d)}
          linkTo="/runs"
          linkLabel="View runs"
        />
        <KpiCard
          label="Avg pass rate"
          value={`${Math.round(passRate)}`}
          unit="%"
          tone={passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad'}
          days={days}
          series={passRateSeries}
          sparkDomain={PASS_RATE_DOMAIN}
          sparkFormat={formatSparkPercent}
          emptyMsg={sparklineHint(measuredPassDays)}
          delta={deltaFromMetric(summary?.avg_pass_rate_7d, true)}
        />
        <KpiCard
          label="Active defects"
          value={`${activeDefects}`}
          tone={activeDefects === 0 ? 'good' : 'bad'}
          days={days}
          emptyMsg={activeDefects === 0 ? `${days}d · no open defects` : `${days}d · count only`}
          delta={deltaFromMetric(summary?.active_defects)}
          linkTo="/defects"
          linkLabel="View defects"
        />
        <KpiCard
          label="Flaky tests"
          value={`${flaky}`}
          tone={flaky === 0 ? 'warn' : 'bad'}
          days={days}
          emptyMsg={flaky === 0 ? `${days}d · no flake events captured` : `${days}d · count only`}
          delta={deltaFromMetric(summary?.flaky_test_count, true)}
          linkTo="/flaky-coach"
          linkLabel="Open flaky coach"
        />
        <KpiCard
          label="New failures · 24h"
          value={`${newFailures}`}
          tone={newFailures === 0 ? 'good' : 'bad'}
          days={days}
          series={countSeries(failedSeries)}
          emptyMsg={sparklineHint(daysWithData)}
          delta={deltaFromMetric(summary?.new_failures_24h)}
          linkTo="/failures"
          linkLabel="View failures"
        />
        <KpiCard
          label="Infra-caused failures"
          value={infraPct == null ? '—' : `${infraPct}`}
          unit={infraPct == null ? undefined : '%'}
          tone={infraPct == null ? 'neutral' : infraPct === 0 ? 'good' : infraPct >= 30 ? 'bad' : 'warn'}
          days={days}
          emptyMsg={infraPct == null
            ? `${days}d · no analyzed failures`
            : `AI-classified · ${infraKindCount} of ${kindTotal} failure${kindTotal === 1 ? '' : 's'}`}
          linkTo="/failures"
          linkLabel="View failures"
        />
        <KpiCard
          label="Avg run duration"
          value={avgDurationMs ? formatDuration(avgDurationMs) : '—'}
          tone="neutral"
          days={days}
          emptyMsg={avgDurationMs ? `${days}d · avg only` : 'Needs ≥ 3 timed runs'}
        />
        {/* US-12.2 — Eng-hours saved. Only rendered when the hours-saved
            model reports available=true; otherwise omitted (no dash-card). */}
        {hoursSaved30d != null && valueMetrics && (
          <KpiCard
            label="Eng-hours saved"
            value={formatHoursSaved(hoursSaved30d)}
            unit="h"
            tone="good"
            days={days}
            emptyMsg={`30d · ≈ ${valueMetrics.headline.fte_equivalent_30d.toFixed(1)} FTE · estimated`}
            // The last card on this page the release filter does not
            // reach, and deliberately so: the headline is an explicit
            // 30-day figure, so scoping it to a three-day hotfix would
            // produce a number its own label contradicts.
            badge={
              <AllReleasesBadge reason="Engineering hours saved is a rolling 30-day figure, which a single release does not divide cleanly." />
            }
            linkTo="/value-metrics"
            linkLabel="View value metrics"
          />
        )}
      </div>

      {/* VIZ-408: the pass-rate trend (which replaced the Execution trend
          card, OD-4) beside the status donut; Top failing and Failure
          categories sit below them; Blockers sits on its own row under them.
          Mounted unconditionally since the chart flags were retired
          (Phase D S1): no flag wait, no legacy branch. */}
      <SectionErrorBoundary message="Failed to load charts">
        <Suspense fallback={<CataloguePending />}>
          <OverviewCatalogue
            days={days}
            window={trendDays}
            trendsLoading={trendsLoading}
            categories={{
              data: failureCategories,
              error: categoriesRead.error,
              isValidating: categoriesRead.isValidating,
              retry: retryCategories,
            }}
            suiteFilter={suiteFilter}
            filtersApplied={trendFiltered}
            everHadRun={newestRunLoaded ? everHadRun : null}
          />
        </Suspense>
      </SectionErrorBoundary>
      <SectionErrorBoundary message="Failed to load blockers">
        <BlockersPanel newFailures={newFailures} hasData={totalExecutions > 0} verdict={verdict} />
      </SectionErrorBoundary>

      {/* Recent activity (epic ACT). Inside its own error boundary and on its
          own SWR key: the panel must never be able to take /overview down or
          hold up its first paint. */}
      <SectionErrorBoundary message="Failed to load recent activity">
        <RecentActivityPanel days={days} />
      </SectionErrorBoundary>
    </div>
  )
}
