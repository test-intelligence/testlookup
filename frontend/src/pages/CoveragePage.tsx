/**
 * Test Coverage — the UX redesign's page template (P3, `02-design-spec.md`
 * §2/§5): "which suites are thin, failing, stale, untagged?"
 *
 * Top to bottom (at 1440 x 900 the suite table starts within 300 px of the
 * content top):
 *   Header  → `PageHeader` (compact): title, the crumb as its one-line
 *             subtitle (project · suite · window · latest run suite ·
 *             updated), the help **?**, and on its right the page's one
 *             filter row (the suite filter, the shared `WindowPicker`, the
 *             saved Views menu) and the **⋯** menu: Compare to previous
 *             window, Export CSV, Open triage queue, View all suites.
 *   KPIs    → `KpiStrip`, 5 compact tiles: Unique tests · Test suites (+
 *             untagged) · Total executions (+ Δ, later vs earlier half of the
 *             window) · Pass rate breakdown · Run cadence (days_with_runs /
 *             window). The comparison strip, when asked for, sits under it.
 *   Body    → 1.65fr | 1fr from 768 px.
 *     Left  → the suite coverage table (`data-primary`): one row per suite,
 *             a stacked bar, its executions, its pass rate and, where the
 *             window shows one, its gap ("No passing run", "Too few runs");
 *             then the Untagged-executions callout (data quality).
 *     Right → Run cadence (the window-day grid).
 *   Score   → a collapsed `Disclosure` ("How this score is computed"): the
 *             verdict and its words, the health score on its threshold meter
 *             (red→amber→green), the 2×2 weighted dimension grid (Pass Rate
 *             35 % / Tag Quality 25 % / Run Cadence 25 % / Suite Breadth 15 %)
 *             and the coverage gaps that move it. Its header states the
 *             verdict and the score.
 *   Tabs    → `?tab=`: Coverage map (the treemap, default) · Env × release
 *             heatmap — the Wave 3 sections, each in its own tab.
 *
 * P3 moved every section; none was rebuilt. The verdict card is gone: its
 * score and dimensions are the Disclosure, its issue rows repeated the table,
 * the untagged callout and the cadence tile, and its two buttons are in the
 * ⋯ menu. So are the page-local window picker, KPI cell and buttons (the
 * shared primitives replace them).
 *
 * P2 (UX redesign, "remove the noise"): the invented 4-stage workflow ribbon
 * and its evidence counts, the provenance footer, the recommended-actions
 * card, the widget picker and every control that only raised a not-built
 * toast (Fix tagging, Schedule, Configure schedule, Apply suite labels, Open
 * runner config) are gone: the page shows only what it does.
 *
 * Data: derives everything from the existing useCoverage(days) +
 * useTrendData(days). Tag-quality is computed from any suite named
 * "Unknown Suite" / empty / null in the suites response.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { suiteHrefByName } from '@/routing/suiteHref'
import {
  BarChart3, Calendar, Download, GitCompare, Layers, ListChecks, ShieldCheck, TestTube, TrendingUp,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import PageHeader from '@/components/ui/PageHeader'
import WindowPicker from '@/components/ui/WindowPicker'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Disclosure from '@/components/ui/Disclosure'
import Tabs from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { useCoverage, useTrendData } from '@/hooks/useMetrics'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { shortAgo } from '@/utils/formatters'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { PageSuiteTargetContext, usePageSuiteTarget } from '@/hooks/pageSuiteTarget'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { CoverageSuite, CoverageSummary } from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'
import { shiftDayIso, utcDayIso } from '@/utils/calendarDay'
import GaugeBar from '@/components/charts/GaugeBar'
import DayStrip from '@/components/charts/DayStrip'
import { useContainerWidth } from '@/components/charts/chartLayout'
import { BODY_GRID_MIN_WIDTH, BODY_GRID_ONE_COLUMN, BODY_GRID_TWO_COLUMNS, useMinWidth } from '@/hooks/useMinWidth'
import { countTones, dayWindow, intensityLevel, type DayStripCell } from '@/components/charts/dayStrip.model'
import { csvBlob, csvCell } from '@/lib/viz/csv'
import { downloadBlob } from '@/utils/download'
import CoverageAdvanced, { type CoverageSectionId } from '@/components/reports/catalogue/CoverageAdvanced'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours (rendered as "24h"); the rest are day counts.
const WINDOWS = [1, 7, 14, 30, 90] as const
type Window = (typeof WINDOWS)[number]

/** The help drawer's topic for this page (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/coverage')

// ── Tabs ───────────────────────────────────────────────────────────────────
/** The page's own tabs, in `?tab=` (the map is the default and the clean URL). */
const COVERAGE_TABS = [
  { id: 'map', label: 'Coverage map' },
  { id: 'heatmap', label: 'Env × release heatmap' },
] as const
type CoverageTab = (typeof COVERAGE_TABS)[number]['id']
const COVERAGE_TAB_IDS: readonly CoverageTab[] = COVERAGE_TABS.map((t) => t.id)

/**
 * Which Wave 3 section each tab renders (`CoverageAdvanced`'s `sections`, by
 * the sections' own ids). The other tab's section is not rendered at all, so
 * it never mounts and never asks for its data.
 */
const COVERAGE_TAB_SECTIONS: Record<CoverageTab, readonly CoverageSectionId[]> = {
  map: ['coverage-map'],
  heatmap: ['heatmap-suite_environment'],
}

// ── Verdict thresholds ────────────────────────────────────────────────────
type Verdict = 'HEALTHY' | 'AT_RISK' | 'BLOCKED' | 'PENDING'

/** The verdict's words and hues (the score's pill and number; the verdict line in the score disclosure). */
const VERDICT_THEME: Record<Verdict, {
  gate: string
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
}> = {
  HEALTHY: {
    gate:   'var(--status-passed)',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 25%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    label:  'Healthy',
  },
  AT_RISK: {
    gate:   'var(--status-broken)',
    pillBg: 'color-mix(in srgb, var(--status-broken) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 25%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'At risk',
  },
  BLOCKED: {
    gate:   'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 12%, transparent)',
    pillBd: 'var(--alert-border-soft)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Blocked',
  },
  PENDING: {
    gate:   'var(--color-text-secondary)',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    label:  'Pending',
  },
}

function verdictForScore(score: number): Verdict {
  if (score >= 66) return 'HEALTHY'
  if (score >= 33) return 'AT_RISK'
  if (score > 0)   return 'BLOCKED'
  return 'PENDING'
}

// ── Health-score model ────────────────────────────────────────────────────
// Weights from README §"Layout & Components". Each dimension is normalised
// to 0-100; the composite is a simple weighted average so the score range
// stays 0-100 and reads cleanly against the threshold marks (33 / 66).
type DimensionId = 'pass_rate' | 'tag_quality' | 'run_cadence' | 'suite_breadth'
const DIMENSION_WEIGHTS: Record<DimensionId, number> = {
  pass_rate:     0.35,
  tag_quality:   0.25,
  run_cadence:   0.25,
  suite_breadth: 0.15,
}

interface DimensionScore {
  id: DimensionId
  label: string
  score: number
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

function isUntaggedRow(s: CoverageSuite): boolean {
  const name = (s.suite_name ?? '').trim().toLowerCase()
  return !name || name === 'unknown suite' || name === 'unknown' || name === 'untagged'
}

// ── CSV export ────────────────────────────────────────────────────────────
// Cells go through the shared ``csvCell`` (lib/viz/csv.ts, VIZ-606) — the
// same RFC 4180 quoting this page always used, shared with the Failure
// analysis export — which ALSO neutralises formula injection: a suite named
// ``=HYPERLINK(…)`` arrives in an ingested CI report, and the spreadsheet
// that opens this file must not evaluate it, including after a ``;`` in the
// locales that split on one (csv.ts says how). Plain numbers are untouched.

interface CoverageExportSources {
  summary: Partial<CoverageSummary>
  suites: CoverageSuite[]
  trend: TrendPoint[]
  meta: {
    projectName: string
    windowLabel: string
    suiteFilter: string | null
    generatedAt: string
    healthScore: number
    verdict: string
  }
}

/** Build the multi-section CSV that the Coverage Export button produces.
 *
 * Four sections, separated by a blank line and a ``# Section`` marker
 * (the same pattern Excel / Sheets users navigate to via Ctrl+G or
 * filter-pivot):
 *
 *   1. Metadata header — project, window, suite filter, generated_at,
 *      health score + verdict. Pasted into a Slack channel, this block
 *      still answers "what window / project / health" without context.
 *   2. Summary KPIs — single row of the headline numbers
 *      (``unique_tests``, ``suite_count``, ``total_executions``,
 *      ``avg_pass_rate``, ``days_with_runs``).
 *   3. Per-suite breakdown — one row per suite with the same columns
 *      the on-page table renders. Suite name is first so tools like
 *      ``sort`` work without column flags.
 *   4. Daily cadence — one row per trend day so consumers can build
 *      their own charts off the same data the cadence heatmap reads.
 */
export function buildCoverageCsv({
  summary, suites, trend, meta,
}: CoverageExportSources): string {
  const lines: string[] = []

  lines.push('# TestLookup — Coverage export')
  lines.push(`# Project,${csvCell(meta.projectName)}`)
  lines.push(`# Window,${csvCell(meta.windowLabel)}`)
  lines.push(`# Suite filter,${csvCell(meta.suiteFilter ?? 'All suites')}`)
  lines.push(`# Generated,${csvCell(meta.generatedAt)}`)
  lines.push(`# Health score,${csvCell(meta.healthScore)}`)
  lines.push(`# Verdict,${csvCell(meta.verdict)}`)
  lines.push('')

  lines.push('# Summary')
  lines.push(
    ['unique_tests', 'suite_count', 'total_executions', 'avg_pass_rate', 'days_with_runs'].join(','),
  )
  lines.push([
    csvCell(summary.unique_tests ?? 0),
    csvCell(summary.suite_count ?? 0),
    csvCell(summary.total_executions ?? 0),
    csvCell(summary.avg_pass_rate ?? 0),
    csvCell(summary.days_with_runs ?? 0),
  ].join(','))
  lines.push('')

  lines.push('# Per-suite breakdown')
  lines.push(['suite_name', 'unique_tests', 'passed', 'failed', 'skipped', 'pass_rate'].join(','))
  for (const s of suites) {
    lines.push([
      csvCell(s.suite_name),
      csvCell(s.unique_tests),
      csvCell(s.passed),
      csvCell(s.failed),
      csvCell(s.skipped),
      csvCell(s.pass_rate),
    ].join(','))
  }
  lines.push('')

  lines.push('# Daily cadence')
  lines.push(['date', 'passed', 'failed', 'skipped', 'broken', 'total', 'pass_rate'].join(','))
  for (const p of trend) {
    lines.push([
      csvCell(p.date),
      csvCell(p.passed),
      csvCell(p.failed),
      csvCell(p.skipped),
      csvCell(p.broken ?? 0),
      csvCell(p.total ?? (p.passed + p.failed + p.skipped + (p.broken ?? 0))),
      csvCell(p.pass_rate),
    ].join(','))
  }
  // Trailing newline so POSIX tooling (wc -l, awk) counts the last row.
  return lines.join('\r\n') + '\r\n'
}

/** Build a filename slug from a project name. */
function slugifyProjectName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'project'
}

/** Triggers a CSV download of the in-memory coverage data. Pure DOM —
 *  no backend round-trip — because the data the user wants is already
 *  on the page; a server-side ``GET /coverage/export`` would just
 *  re-serialise what we already have. Mirrors
 *  ``FailureAnalysisPage.handleExportCsv``. */
function handleCoverageExportCsv({
  summary, suites, trend, project, days, suiteFilter, healthScore, verdict,
}: {
  summary: Partial<CoverageSummary>
  suites: CoverageSuite[]
  trend: TrendPoint[]
  project: { id: string; name: string } | null
  days: number
  suiteFilter: string | null
  healthScore: number
  verdict: string
}): void {
  const hasData = (summary.suite_count ?? 0) > 0 || suites.length > 0 || trend.length > 0
  if (!hasData) {
    toast('No coverage data to export in this window', { icon: '📭' })
    return
  }
  const windowLabel = days === 1 ? '24h' : `${days}d`
  const csv = buildCoverageCsv({
    summary, suites, trend,
    meta: {
      projectName: project?.name ?? 'All projects',
      windowLabel,
      suiteFilter,
      generatedAt: new Date().toISOString(),
      healthScore,
      verdict,
    },
  })
  // ``csvBlob`` prepends the BOM so Excel opens the file in UTF-8.
  const blob = csvBlob(csv)
  const projectSlug = project ? slugifyProjectName(project.name) : 'all-projects'
  const suiteSlug = suiteFilter ? `-${slugifyProjectName(suiteFilter)}` : ''
  // ``downloadBlob`` revokes the object URL only AFTER the browser has read
  // the blob; the synchronous revoke this used to do can cancel the download.
  downloadBlob(blob, `coverage-${projectSlug}${suiteSlug}-${windowLabel}.csv`)
  toast.success(
    `Exported ${suites.length} suite${suites.length === 1 ? '' : 's'} + ${trend.length} day${trend.length === 1 ? '' : 's'} of cadence`,
  )
}

function computeHealthModel(
  summary: Partial<CoverageSummary>,
  suites: CoverageSuite[],
  days: number,
  /** Days with runs among the window's UTC calendar days, the cadence strip's count. */
  calendarDaysWithRuns?: number,
) {
  const totalRuns      = summary.total_executions ?? 0
  const passRate       = Number(summary.avg_pass_rate ?? 0)
  // The strip's count when the trend is in: the summary counts the days a
  // rolling 7 x 24 h touches, up to eight calendar days, so the tile read
  // "7 / 7 days, 100%" above a strip of "6 active days, 1 empty" (browser E2E pass).
  const daysWithRuns   = calendarDaysWithRuns ?? summary.days_with_runs ?? 0
  const suiteCount     = summary.suite_count ?? suites.length
  const untaggedRuns   = suites.filter(isUntaggedRow)
                               .reduce((sum, s) => sum + (s.passed + s.failed + s.skipped), 0)
  const taggedShare    = totalRuns > 0 ? Math.max(0, totalRuns - untaggedRuns) / totalRuns : 0

  // Per-dimension 0-100 scores. Each is a simple linear projection of the
  // underlying metric onto a 0-100 range, with sane caps so a single noisy
  // input doesn't dominate the composite.
  //   - pass_rate     : already 0-100
  //   - tag_quality   : tagged / total, * 100
  //   - run_cadence   : days_with_runs / days, * 100 (capped at 100)
  //   - suite_breadth : min(suite_count, 5) / 5 * 100  (5+ suites = full)
  const passRateScore     = clamp(passRate, 0, 100)
  const tagQualityScore   = totalRuns > 0 ? clamp(taggedShare * 100, 0, 100) : 0
  const runCadenceScore   = days > 0 ? clamp((daysWithRuns / days) * 100, 0, 100) : 0
  const suiteBreadthScore = clamp((Math.min(suiteCount, 5) / 5) * 100, 0, 100)

  const dimensions: DimensionScore[] = [
    { id: 'pass_rate',     label: 'Pass rate',     score: passRateScore,     weight: DIMENSION_WEIGHTS.pass_rate,     tone: toneFor(passRateScore) },
    { id: 'tag_quality',   label: 'Tag quality',   score: tagQualityScore,   weight: DIMENSION_WEIGHTS.tag_quality,   tone: toneFor(tagQualityScore) },
    { id: 'run_cadence',   label: 'Run cadence',   score: runCadenceScore,   weight: DIMENSION_WEIGHTS.run_cadence,   tone: toneFor(runCadenceScore) },
    { id: 'suite_breadth', label: 'Suite breadth', score: suiteBreadthScore, weight: DIMENSION_WEIGHTS.suite_breadth, tone: toneFor(suiteBreadthScore) },
  ]

  const composite = Math.round(dimensions.reduce((sum, d) => sum + d.score * d.weight, 0))
  return {
    composite,
    dimensions,
    untaggedRuns,
    daysWithRuns,
    totalRuns,
    suiteCount,
    passRate,
  }
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 40) return 'warn'
  return 'bad'
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n))
}

// ── Score disclosure ──────────────────────────────────────────────────────
/**
 * What the verdict card held, in the collapsed "How this score is computed"
 * disclosure below the suite table (P3): the verdict and its one-line
 * action, the words that say how to read it, the coverage gaps that move the
 * score, the health score on its meter and the four weighted dimensions. The
 * card's issue rows are not here: each repeated a fact the page already
 * shows (the failing suite: the table; the untagged executions: the callout
 * and the suites tile; the cadence: the Run cadence tile and strip).
 */
function ScoreDetails({
  model, verdict, lede, gaps,
}: {
  model: ReturnType<typeof computeHealthModel>
  verdict: Verdict
  lede: React.ReactNode
  gaps: GapRow[]
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Coverage verdict"
      // One column below 1024 px; from lg the card's 1.45fr | 1fr.
      className="grid gap-6 grid-cols-1 lg:grid-cols-[1.45fr_1fr]"
    >
      <div className="flex flex-col gap-3.5 min-w-0">
        <div>
          <h2 className="text-[15px] font-semibold m-0 mb-1.5 text-[var(--color-text)]">
            <span style={{ color: t.gate }}>{t.label}</span>
            <span className="text-[var(--color-text-muted)] mx-2">·</span>
            <span>{verdictAction(verdict, model)}</span>
          </h2>
          <p className="text-[13px] m-0 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
            {lede}
          </p>
        </div>
        <CoverageGaps gaps={gaps} />
      </div>
      <div className="flex flex-col gap-3.5 pt-0.5 min-w-0">
        <HealthMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
    </section>
  )
}

function verdictAction(v: Verdict, model: ReturnType<typeof computeHealthModel>): string {
  if (v === 'HEALTHY') return 'all signals nominal'
  if (v === 'PENDING') return 'awaiting executions'
  const failing = model.dimensions.filter(d => d.tone === 'bad').length
  if (v === 'BLOCKED') return failing > 0 ? `${failing} dimension${failing === 1 ? '' : 's'} below threshold` : 'critical issues blocking'
  return failing > 0 ? `${failing} dimension${failing === 1 ? '' : 's'} ${failing === 1 ? 'needs' : 'need'} attention` : 'review the gaps below'
}

// ── Coverage comparison strip ─────────────────────────────────────────────
/** Inline panel rendered below the verdict card when the user clicks
 *  "Compare to previous window". Pulls prior-vs-current deltas from the
 *  same double-window trend the FailureAnalysisPage uses (no bespoke
 *  backend endpoint). Cells: total executions, pass rate, days with
 *  runs — the three metrics that map cleanly onto Coverage's headline.
 *  Mirrors ``FailureAnalysisPage.ComparisonStrip`` so the two pages
 *  look like one product. */
type CoverageCompareStats = {
  passed: number; failed: number; skipped: number; total: number;
  passRate: number; days: number; daysWithRuns: number
}

export function CoverageComparisonStrip({
  current, prior, windowDays,
}: {
  current: CoverageCompareStats
  prior: CoverageCompareStats
  windowDays: number
}) {
  const totalDelta        = current.total - prior.total
  const passRateDelta     = current.passRate - prior.passRate
  const daysWithRunsDelta = current.daysWithRuns - prior.daysWithRuns

  // ``deltaColour`` returns CSS values rather than Tailwind classes so the
  // direction-vs-good logic stays explicit at each call site.
  const RED   = 'var(--status-failed)'
  const GREEN = 'var(--status-passed)'
  const NEUTRAL = 'var(--color-text-muted)'
  const colourForRateDelta = (delta: number): string =>
    Math.abs(delta) < 0.01 ? NEUTRAL : (delta > 0 ? GREEN : RED)
  const colourForCadenceDelta = (delta: number): string =>
    delta === 0 ? NEUTRAL : (delta > 0 ? GREEN : RED)

  const fmtInt = (n: number) => Intl.NumberFormat().format(Math.round(n))
  const arrow = (delta: number) => delta === 0 ? '—' : (delta > 0 ? '↑' : '↓')

  return (
    <section
      aria-label="Coverage comparison"
      className="rounded-xl border"
      style={{
        background: 'var(--color-bg-card)',
        borderColor: 'var(--color-border)',
        padding: '14px 16px',
        marginBottom: 14,
      }}
    >
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">
          Compare to previous window
        </h3>
        <span className="text-[11px] text-[var(--color-text-muted)]">
          last {windowDays}d vs prior {windowDays}d
        </span>
      </div>
      <p className="m-0 mb-3 text-[12px] text-[var(--color-text-muted)]" style={{ lineHeight: 1.5 }}>
        Aggregated from daily trends. Prior window = the {prior.days} days immediately before this window.
      </p>
      <div className="grid gap-2.5" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        <CoverageCompareCell
          label="Total executions"
          currentText={fmtInt(current.total)}
          deltaText={`${arrow(totalDelta)} ${fmtInt(Math.abs(totalDelta))}`}
          priorText={`vs prior ${fmtInt(prior.total)}`}
          deltaColor={NEUTRAL}
        />
        <CoverageCompareCell
          label="Pass rate"
          currentText={`${current.passRate.toFixed(1)}%`}
          deltaText={`${arrow(passRateDelta)} ${Math.abs(passRateDelta).toFixed(1)}%`}
          priorText={`vs prior ${prior.passRate.toFixed(1)}%`}
          deltaColor={colourForRateDelta(passRateDelta)}
        />
        <CoverageCompareCell
          label="Days with runs"
          currentText={String(current.daysWithRuns)}
          deltaText={`${arrow(daysWithRunsDelta)} ${Math.abs(daysWithRunsDelta)}`}
          priorText={`vs prior ${prior.daysWithRuns}`}
          deltaColor={colourForCadenceDelta(daysWithRunsDelta)}
        />
      </div>
    </section>
  )
}

function CoverageCompareCell({
  label, currentText, deltaText, priorText, deltaColor,
}: {
  label: string
  currentText: string
  deltaText: string
  priorText: string
  deltaColor: string
}) {
  return (
    <div
      className="rounded-md border"
      style={{ padding: '10px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[11px] text-[var(--color-text-muted)] uppercase tracking-wider">{label}</div>
      <div className="mt-1 text-[18px] font-semibold text-[var(--color-text)]">{currentText}</div>
      <div className="mt-0.5 text-[11.5px]" style={{ color: deltaColor }}>
        {deltaText} <span className="text-[var(--color-text-muted)]">{priorText}</span>
      </div>
    </div>
  )
}

function HealthMeter({ model, verdict }: { model: ReturnType<typeof computeHealthModel>; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  return (
    <div>
      <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Coverage health score
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
          {t.label}
        </span>
      </div>
      {/* PENDING has no score to place: the header says "—", so the bar is
          not-measured (an empty track), never a fill at 0. */}
      <GaugeBar
        className="mt-3"
        value={verdict === 'PENDING' ? null : score}
        label="Coverage health score"
        valueText={`${score} of 100, ${t.label}`}
        gradient="health"
        ticks={HEALTH_TICKS}
      />
    </div>
  )
}

const HEALTH_TICKS = [
  { value: 0, label: 'Block' },
  { value: 33, label: 'At risk' },
  { value: 66, label: 'Healthy' },
  { value: 100 },
]

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

// ── Suite breakdown ────────────────────────────────────────────────────────
/** A suite's own gap in the window: the two per-suite rows `buildGaps` lists, for every suite. */
interface SuiteGapFlag {
  tone: 'bad' | 'warn'
  label: string
  /** The gap in full (the flag's tooltip). */
  title: string
}

function suiteGapFlag(suite: CoverageSuite, days: number): SuiteGapFlag | null {
  if (isUntaggedRow(suite)) return null
  if (suite.failed > 0 && suite.passed === 0) {
    return { tone: 'bad', label: 'No passing run', title: `No passing run in ${days}d — blocks regression baseline` }
  }
  if (suite.passed + suite.failed + suite.skipped < 3) {
    return { tone: 'warn', label: 'Too few runs', title: 'No baseline — too few runs to compare' }
  }
  return null
}

/**
 * The page's one primary content (P3, `data-primary`): every suite in the
 * window — its stacked bar, its executions, its pass rate and, where the
 * window shows one, its gap (a fifth column, drawn only when a suite has a
 * gap, in the full layout). The coverage response has no per-suite last run,
 * so the table shows none.
 */
function SuiteBreakdown({ suites, totalExecutions, days }: { suites: CoverageSuite[]; totalExecutions: number; days: number }) {
  const tagged   = suites.filter(s => !isUntaggedRow(s))
  const untagged = suites.filter(isUntaggedRow)
  const visible = [...tagged, ...untagged]
  // F-18: a row's fixed columns (160 + 64 + 76 + gaps = 348 px) left the bar
  // 0 px wide in a narrow card and scrolled the page sideways at 375 px. The
  // layout follows the rows' OWN width (the card is a column of a two-column
  // grid at every viewport): see `suiteRowsLayout`.
  const [rowsRef, rowsWidth] = useContainerWidth<HTMLDivElement>()
  const layout = suiteRowsLayout(rowsWidth)
  const flags = visible.map((s) => suiteGapFlag(s, days))
  const flagColumn = layout === 'full' && flags.some(Boolean)
  return (
    <section
      aria-label="Suite coverage breakdown"
      data-primary=""
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '16px 18px 18px' }}
    >
      <div className="flex items-center justify-between gap-2.5 flex-wrap mb-3.5">
        {/* h2: the page's primary content, the first heading under the title (P3). */}
        <h2 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Suite coverage breakdown</h2>
        <div className="flex items-center gap-3 text-[11.5px] text-[var(--color-text-muted)]">
          <Legend color="var(--status-passed)" label="Passed" />
          <Legend color="var(--status-failed)" label="Failed" />
          <Legend color="var(--status-broken)" label="Skipped" />
          <span className="inline-flex items-center gap-1.5 text-[var(--color-text-faint)]">
            <span aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: 'repeating-linear-gradient(45deg, var(--color-text-faint), var(--color-text-faint) 2px, var(--color-bg-hover) 2px, var(--color-bg-hover) 4px)' }} />
            Untagged
          </span>
        </div>
      </div>

      {/* One grid for every row (each row is a subgrid of it), so the name
          column is as wide as the widest name and its test chip need, between
          160 and 240 px, in the font that is actually drawn. A fixed 160 px
          fitted Windows fonts and cut "Notifications" to "Notificatio…" in
          DejaVu Sans on Linux (W3 C0 BEFORE note 2).
          Each row is a `row` with four `cell`s, so the grid is a `table`
          (an ARIA row needs a table parent and cells: axe
          aria-required-parent / -children, critical). Roles only: nothing
          is drawn differently. With no suite there is no row, so no table. */}
      <div
        ref={rowsRef}
        data-suite-rows=""
        className="grid"
        role={visible.length > 0 ? 'table' : undefined}
        aria-label={visible.length > 0 ? 'Suites' : undefined}
        data-suite-rows-layout={layout}
        style={{ gridTemplateColumns: flagColumn ? `${SUITE_ROWS_COLUMNS[layout]} auto` : SUITE_ROWS_COLUMNS[layout], columnGap: 16 }}
      >
        {visible.length === 0 && (
          <div className="text-[12.5px] text-[var(--color-text-muted)] py-6 text-center" style={{ gridColumn: '1 / -1' }}>
            No suite data for the selected window.
          </div>
        )}
        {visible.map((s, i) => (
          <SuiteRow
            key={`${s.suite_name}-${i}`}
            suite={s}
            totalExecutions={totalExecutions}
            isLast={i === visible.length - 1}
            layout={layout}
            gap={flagColumn ? (flags[i] ?? 'none') : undefined}
          />
        ))}
      </div>

      <div className="flex items-center justify-between gap-2.5 mt-3.5 pt-3 text-[11.5px] text-[var(--color-text-muted)]" style={{ borderTop: '1px solid var(--color-border)' }}>
        <span>
          Showing {tagged.length} suite{tagged.length === 1 ? '' : 's'}
          {untagged.length > 0 && <> + {untagged.length} untagged group{untagged.length === 1 ? '' : 's'}</>}
          {' · '}
          {totalExecutions} executions total
        </span>
        {/* /coverage/suite needs a suite `name` (without one it is an empty
            page): the list of every suite is /suites. */}
        <Link to="/suites" className="hover:underline" style={{ color: 'var(--color-accent)' }}>
          Browse suites →
        </Link>
      </div>
    </section>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

function SuiteRow({
  suite,
  totalExecutions,
  isLast,
  layout,
  gap,
}: {
  suite: CoverageSuite
  totalExecutions: number
  isLast: boolean
  layout: SuiteRowsLayout
  /** The gap column's cell: a flag, `'none'` (an empty cell), or `undefined` (no gap column). */
  gap?: SuiteGapFlag | 'none'
}) {
  const stacked = layout === 'stacked'
  const untagged = isUntaggedRow(suite)
  const total = suite.passed + suite.failed + suite.skipped
  // The bar's drawn width: each segment is exactly its share of it, so a count is shown only where it fits.
  const [barRef, barWidth] = useContainerWidth<HTMLDivElement>()
  // Skipped tests are not in the denominator (the app's one pass-rate rule),
  // and a suite whose every execution was skipped has no rate at all. Over
  // the total, an all-skipped QuarantinedSuite read 0% in red (browser E2E pass).
  const executed = suite.passed + suite.failed
  const passRate = executed > 0 ? Math.round((suite.passed / executed) * 100) : null
  const passRateTone: 'good' | 'warn' | 'bad' | 'dim' =
    untagged || passRate === null ? 'dim' : passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad'
  const passRateColor =
    passRateTone === 'good' ? 'var(--status-passed)' :
    passRateTone === 'warn' ? 'var(--status-broken)' :
    passRateTone === 'bad'  ? 'var(--status-failed)' :
    'var(--color-text-muted)'

  return (
    <div
      role="row"
      aria-label={`${untagged ? 'Untagged group' : suite.suite_name}: ${suite.passed} passed, ${suite.failed} failed, ${suite.skipped} skipped`}
      className={clsx('grid items-center gap-4', !isLast && 'pb-2.5 mb-2.5')}
      style={{
        gridColumn: '1 / -1',
        gridTemplateColumns: 'subgrid',
        borderBottom: !isLast ? '1px dashed var(--color-border)' : '0',
        paddingTop: 10,
        // Stacked: the name's line and the bar's line sit close (one row of the table).
        ...(stacked ? { rowGap: 6 } : {}),
      }}
    >
      {/* Suite name + count (stacked: a line of its own, over the bar and the pass rate) */}
      <div role="cell" className="flex items-center gap-1.5 min-w-0" style={{
        ...(stacked ? { gridColumn: '1 / -1', minWidth: 0 } : { minWidth: SUITE_NAME_MIN_WIDTH }),
        color: untagged ? 'var(--color-text-muted)' : (suite.failed > 0 && !untagged ? 'var(--status-failed)' : 'var(--color-text)'),
        fontFamily: untagged ? 'var(--font-sans)' : 'var(--font-mono)',
        fontStyle: untagged ? 'italic' : 'normal',
        fontSize: 12.5,
      }}>
        {untagged ? (
          <span className="truncate">Untagged</span>
        ) : (
          // The suite's own page (UX redesign P4): its tests, runs and charts.
          <Link to={suiteHrefByName(suite.suite_name)} className="truncate hover:underline" title={suite.suite_name} style={{ color: 'inherit' }}>
            {suite.suite_name}
          </Link>
        )}
        <span
          className="text-[9.5px] uppercase font-sans border rounded-sm px-1 py-0.5 flex-none"
          style={{ color: 'var(--color-text-faint)', borderColor: 'var(--color-border)', letterSpacing: 'var(--tracking-wide)' }}
        >
          {suite.unique_tests} test{suite.unique_tests === 1 ? '' : 's'}
        </span>
      </div>

      {/* Stacked bar */}
      <div role="cell" ref={barRef} className="relative rounded-sm overflow-hidden flex" style={{ height: 18, background: 'var(--color-bg-secondary)' }}>
        {untagged ? (
          <div
            className="h-full flex items-center justify-center text-[10.5px] font-semibold tabular-nums"
            style={{
              flex: total,
              background: 'repeating-linear-gradient(45deg, var(--color-text-faint), var(--color-text-faint) 4px, var(--color-bg-hover) 4px, var(--color-bg-hover) 8px)',
              color: 'var(--color-text)',
            }}
          >
            {total} attributable
          </div>
        ) : (
          <>
            {suite.passed > 0  && <StackSegment count={suite.passed}  status="passed"  color="var(--status-passed)" fits={segmentCountFits(suite.passed, (barWidth * suite.passed) / total)} />}
            {suite.failed > 0  && <StackSegment count={suite.failed}  status="failed"  color="var(--status-failed)" fits={segmentCountFits(suite.failed, (barWidth * suite.failed) / total)} />}
            {suite.skipped > 0 && <StackSegment count={suite.skipped} status="skipped" color="var(--status-broken)" fits={segmentCountFits(suite.skipped, (barWidth * suite.skipped) / total)} />}
            {total === 0 && <div className="flex-1" />}
          </>
        )}
      </div>

      {/* Total (only in the full layout: under 400 px the bar needs the room, F-18) */}
      {layout === 'full' && (
        <div role="cell" className="text-[13px] tabular-nums text-right" style={{ color: 'var(--color-text-secondary)' }}>
          {total} run{total === 1 ? '' : 's'}
        </div>
      )}

      {/* Pass-rate (the screen-reader total sits in this cell: a row owns cells only; sr-only takes no room) */}
      <div
        role="cell"
        className="text-[13px] font-semibold tabular-nums text-right"
        style={{ color: passRateColor }}
        title={!untagged && passRate === null ? 'No pass rate: every execution was skipped' : undefined}
      >
        {untagged || passRate === null ? '—' : `${passRate}%`}
        <span className="sr-only">Total executions across all suites: {totalExecutions}</span>
      </div>

      {/* The gap (only when some suite in the window has one) */}
      {gap !== undefined && (
        <div role="cell" className="text-right">
          {gap !== 'none' && (
            <span
              data-suite-gap={gap.tone}
              title={gap.title}
              className="inline-flex items-center whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10.5px] font-semibold"
              style={gap.tone === 'bad'
                ? { color: 'var(--status-failed)', borderColor: 'color-mix(in srgb, var(--status-failed) 30%, transparent)', background: 'color-mix(in srgb, var(--status-failed) 8%, transparent)' }
                : { color: 'var(--status-broken)', borderColor: 'var(--gate-conditional-border)', background: 'var(--gate-conditional-bg-soft)' }}
            >
              {gap.label}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * The suite rows' columns: name (and its test chip), bar, runs, pass rate.
 * The name column is as wide as the widest row needs, measured by the
 * browser in the font it draws, never under 160 px (the old fixed width) and
 * never over 240 px (a long name is cut, with the full text in its title).
 */
const SUITE_ROW_COLUMNS = 'fit-content(240px) minmax(0, 1fr) 64px 76px'
const SUITE_NAME_MIN_WIDTH = 160

/**
 * The suite rows' layout for their own width, px (F-18; 0 = not measured yet,
 * so the full layout every desktop width draws, and the committed baselines):
 *   - full (400 px up): name, bar, runs, pass rate;
 *   - compact (320-399): the runs column goes (the row's name still says every
 *     count) and the name column is held at 160 px, so the bar keeps 50+ px;
 *   - stacked (under 320: the card is a column of a two-column grid even at a
 *     375 px viewport, about 170 px wide): the name on a line of its own, the
 *     bar and the pass rate under it.
 */
type SuiteRowsLayout = 'full' | 'compact' | 'stacked'
const SUITE_ROWS_COMPACT_PX = 400
const SUITE_ROWS_STACKED_PX = 320
function suiteRowsLayout(width: number): SuiteRowsLayout {
  if (!(width > 0) || width >= SUITE_ROWS_COMPACT_PX) return 'full'
  return width >= SUITE_ROWS_STACKED_PX ? 'compact' : 'stacked'
}
const SUITE_ROWS_COLUMNS: Record<SuiteRowsLayout, string> = {
  full: SUITE_ROW_COLUMNS,
  compact: 'fit-content(160px) minmax(0, 1fr) 76px',
  stacked: 'minmax(0, 1fr) 76px',
}

/** The segment count's font size, px, and the room a count needs around it, px a side. */
const SEGMENT_FONT_PX = 10.5
const SEGMENT_PAD_PX = 3
/**
 * A bold digit's width, em: an OVER-estimate (DejaVu Sans Bold, the Linux
 * runner's font, is 0.70 em; the Windows fonts are narrower), so a count is
 * only ever hidden that would just have fitted, never drawn clipped.
 */
const SEGMENT_DIGIT_EM = 0.72

/** Whether `count` fits, with its padding, in a segment `width` px wide (`0` = not measured: it does not). */
function segmentCountFits(count: number, width: number): boolean {
  const digits = String(Math.max(0, Math.trunc(count))).length
  return width > 0 && width >= digits * SEGMENT_FONT_PX * SEGMENT_DIGIT_EM + 2 * SEGMENT_PAD_PX
}

/**
 * A count is drawn inside its segment only when it FITS there (the bar is
 * measured; each segment is exactly its share of it): before this, a narrow
 * "38" beside a narrow "12" read as one number, "3812", and a "4" was cut to
 * a sliver (W3 C0 BEFORE note 1). A hidden count stays in the row's name, the
 * segment's title and the runs column. Segments no longer grow to hold their
 * text (`min-w-0`), so the bar is exactly proportional.
 */
function StackSegment({ count, status, color, fits }: { count: number; status: 'passed' | 'failed' | 'skipped'; color: string; fits: boolean }) {
  return (
    <div
      data-suite-segment={status}
      title={`${count} ${status}`}
      className="h-full min-w-0 overflow-hidden flex items-center justify-center text-[10.5px] font-semibold tabular-nums"
      style={{ flex: count, background: color, color: 'rgba(255,255,255,0.92)' }}
    >
      {fits ? count : null}
    </div>
  )
}

// ── Untagged callout ──────────────────────────────────────────────────────
function UntaggedCallout({ untaggedRuns, totalRuns, suites }: { untaggedRuns: number; totalRuns: number; suites: CoverageSuite[] }) {
  if (untaggedRuns === 0) return null
  const pct = totalRuns > 0 ? Math.round((untaggedRuns / totalRuns) * 100) : 0
  // We don't have per-test path data on the coverage endpoint — surface
  // the untagged group(s) we can see in the suites response.
  const untaggedGroups = suites.filter(isUntaggedRow)

  return (
    <section
      aria-label="Untagged executions"
      className="rounded-xl"
      style={{
        padding: '14px 16px',
        border: '1px solid color-mix(in srgb, var(--status-broken) 28%, transparent)',
        borderLeft: '3px solid var(--status-broken)',
        background: 'linear-gradient(90deg, color-mix(in srgb, var(--status-broken) 6%, transparent), transparent 50%), var(--color-bg-card)',
      }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-1 flex items-center gap-2 text-[var(--color-text)]">
        Untagged executions
        <span
          className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full uppercase"
          style={{ background: 'color-mix(in srgb, var(--status-broken) 16%, transparent)', color: 'var(--status-broken)', letterSpacing: 'var(--tracking-wide)' }}
        >
          Data quality
        </span>
      </h3>
      <p className="text-[12.5px] m-0 mb-3 max-w-[70ch]" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        {untaggedRuns} of {totalRuns} executions ({pct}%) didn't carry a suite label, so they appear as{' '}
        <em>Unknown Suite</em>. These tests can't be assigned ownership, prioritized, or trended without a tag.
        The most common cause is a missing <code className="font-mono text-[11.5px]">@suite</code> annotation in
        the test runner config.
      </p>

      <div className="flex flex-col gap-1.5">
        {untaggedGroups.map((g, i) => (
          <div
            key={i}
            className="grid items-center gap-3 rounded-sm border px-2.5 py-2"
            style={{ gridTemplateColumns: '1fr auto', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
          >
            <div className="font-mono text-[11.5px] text-[var(--color-text)]">
              {g.suite_name || 'Unknown Suite'}
              <span className="ml-2.5 italic font-sans text-[11px] text-[var(--color-text-muted)]">
                {g.unique_tests} test{g.unique_tests === 1 ? '' : 's'} in group
              </span>
            </div>
            <div className="text-[11px] tabular-nums text-[var(--color-text-muted)]">
              {g.passed + g.failed + g.skipped} run{g.passed + g.failed + g.skipped === 1 ? '' : 's'}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── Coverage gaps ──────────────────────────────────────────────────────────
interface GapRow {
  tone: 'bad' | 'warn'
  body: React.ReactNode
  ago: string
}

function buildGaps(model: ReturnType<typeof computeHealthModel>, suites: CoverageSuite[], days: number): GapRow[] {
  const gaps: GapRow[] = []
  const failingSuites = suites.filter(s => !isUntaggedRow(s) && s.failed > 0 && s.passed === 0)
  for (const s of failingSuites.slice(0, 2)) {
    gaps.push({
      tone: 'bad',
      body: (
        <>
          <strong>{s.suite_name}</strong> · no passing run in {days}d{' '}
          <span className="dim">— blocks regression baseline</span>
        </>
      ),
      ago: `${days}d+`,
    })
  }
  if (model.daysWithRuns === 0) {
    gaps.push({
      tone: 'warn',
      body: <>No nightly schedule firing <span className="dim">— last scheduled run unknown</span></>,
      ago: '—',
    })
  } else if (model.daysWithRuns < Math.ceil(days * 0.5)) {
    gaps.push({
      tone: 'warn',
      body: <>Run cadence below 50% <span className="dim">— scheduled runs may be paused</span></>,
      ago: `${days - model.daysWithRuns}d`,
    })
  }
  if (model.untaggedRuns > 0) {
    gaps.push({
      tone: 'warn',
      body: <>{model.untaggedRuns} untagged execution{model.untaggedRuns === 1 ? '' : 's'} <span className="dim">— ownership unknown</span></>,
      ago: 'today',
    })
  }
  const lowSampleSuites = suites.filter(s => !isUntaggedRow(s) && (s.passed + s.failed + s.skipped) < 3)
  for (const s of lowSampleSuites.slice(0, 1)) {
    gaps.push({
      tone: 'warn',
      body: <>No baseline for <strong>{s.suite_name}</strong> suite <span className="dim">— too few runs to compare</span></>,
      ago: '—',
    })
  }
  return gaps
}

function CoverageGaps({ gaps }: { gaps: GapRow[] }) {
  return (
    <section
      aria-label="Coverage gaps"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px' }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-1 text-[var(--color-text)]">Coverage gaps</h3>
      <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3">Missing or stale signals that affect the score.</p>
      {gaps.length === 0 ? (
        <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">
          No coverage gaps detected — every signal is up-to-date.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {gaps.map((g, i) => (
            <div
              key={i}
              className="flex items-center gap-2 rounded-sm border"
              style={{ padding: '8px 10px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
            >
              <span aria-hidden className="inline-block w-2 h-2 rounded-sm flex-none" style={{ background: g.tone === 'bad' ? 'var(--status-failed)' : 'var(--status-broken)' }} />
              <div className="flex-1 text-[12.5px] text-[var(--color-text)] leading-[1.35] gap-body">{g.body}</div>
              <span className="text-[10.5px] tabular-nums text-[var(--color-text-faint)]">{g.ago}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ── Run cadence heatmap ───────────────────────────────────────────────────

/** The heatmap draws at most this many days (the README defers a 90-day layout). */
export const CADENCE_MAX_CELLS = 30

/**
 * The cadence strip's cells and its aggregate label, for a window of `days`
 * ending on `todayIso`: one cell per day (quiet days included), its level
 * from the day's executions (0 / 1-5 / 6-20 / 21-50 / more than 50), and only
 * the last `CADENCE_MAX_CELLS` days.
 *
 * The label counts the cells it DRAWS. It used to count the whole window
 * while saying "the last 30 days", so a 90-day window read "last 30 days.
 * 12 active days, 78 empty days" over 30 cells.
 *
 * Volume, not outcome: no cell is marked mixed or failed, so this strip
 * carries no failure cue (Trends' cadence strip is the one that does).
 */
export function coverageCadence(
  trend: TrendPoint[],
  days: number,
  todayIso: string,
): { cells: DayStripCell[]; label: string } {
  const byDate = new Map<string, number>()
  for (const p of trend) {
    const total = p.passed + p.failed + p.skipped + (p.broken ?? 0)
    byDate.set(p.date.slice(0, 10), total)
  }
  const isos = dayWindow(Math.min(days, CADENCE_MAX_CELLS), todayIso)
  const cells = isos.map<DayStripCell>((iso, i) => {
    const runs = byDate.get(iso) ?? 0
    return {
      key: iso,
      label: `${iso} · ${runs} execution${runs === 1 ? '' : 's'}`,
      tone: runs > 0 ? 'pass' : 'none',
      level: intensityLevel(runs),
      marker: i === isos.length - 1 ? 'today' : undefined,
    }
  })
  const counts = countTones(cells)
  return {
    cells,
    label: `Run cadence over the last ${cells.length} days. ${counts.pass} active day${counts.pass === 1 ? '' : 's'}, ${counts.none} empty day${counts.none === 1 ? '' : 's'}.`,
  }
}

function CadenceHeatmap({ trend, days }: { trend: TrendPoint[]; days: number }) {
  const { cells, label } = useMemo(() => coverageCadence(trend, days, utcDayIso()), [trend, days])
  return (
    <section
      aria-label="Run cadence"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px' }}
    >
      {/* P3: the explainer paragraph under the title is its tooltip now
          (spec §2: explanatory text is never a paragraph on the page). */}
      <h3
        className="text-[13px] font-semibold m-0 mb-2.5 flex justify-between items-center text-[var(--color-text)]"
        title="Each cell is a day, shaded by its executions. An empty cell: no run landed that day."
      >
        Run cadence
        <span className="text-[11px] font-medium text-[var(--color-text-muted)]">
          last {days > CADENCE_MAX_CELLS ? `${CADENCE_MAX_CELLS} of ` : ''}{days} days
        </span>
      </h3>
      <DayStrip cells={cells} mode="intensity" label={label} title="Run cadence" />
    </section>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────
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

export default function CoveragePage() {
  // Two body columns from 768 px; one below, where 205 + 124 px columns squeezed the cards (Wave 3, X2/X3).
  const twoBodyColumns = useMinWidth(BODY_GRID_MIN_WIDTH)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Window is a global user preference (shared with Live / Trends /
  // Runs / Failures / Summary / Overview / My Failures). Snap to this
  // page's allowed set when the stored value isn't supported here; the
  // header's `WindowPicker` writes it.
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, WINDOWS) as Window
  // The page's own tabs (`?tab=`): the coverage map by default.
  const [tab, setTab] = useTabParam<CoverageTab>(COVERAGE_TAB_IDS, 'map')

  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames, suiteLabel } = usePageSuiteFilter()
  // P1: this page's saved views (the top-bar release, the window, the suite).
  const viewsMenu = useReportViewsMenu({
    route: '/coverage',
    windowDays: days,
    windowOptions: WINDOWS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
  })
  const { options: suiteOptions } = useSuiteOptions(days)
  // P2: the catalogue's "Filter page by this" writes a suite mark to the select above.
  const suiteTarget = usePageSuiteTarget(selectedSuite, suiteOptions, setSelectedSuite)

  // `error` is read alongside `data`: without it a failed fetch renders the
  // "No coverage data yet" empty state, which tells an operator mid-outage to
  // upload results they have already uploaded.
  const { data: coverageData, isLoading, error: coverageError, mutate: retryCoverage } =
    useCoverage(days, suiteFilter)
  // Real arrival time of this view's payload (the header's "Updated" age),
  // never a hardcoded age that made fresh coverage claim to be hours old.
  const fetchedAt = useDataFreshness(coverageData)
  const { data: trendData } = useTrendData(days, suiteFilter)

  const summary: Partial<CoverageSummary> = useMemo(() => coverageData?.summary ?? {}, [coverageData])
  const suites: CoverageSuite[] = useMemo(() => coverageData?.suites ?? [], [coverageData])
  const trend: TrendPoint[] = trendData?.data ?? []

  // ── Compare-to-previous-window toggle ────────────────────────────────
  // Reuses the FailureAnalysisPage approach: a second trend fetch with
  // 2× the window, split at the midpoint into prior vs current halves.
  // When ``comparing`` is off, the second fetch keys on the same ``days``
  // as the primary trend above, so SWR dedupes and no extra request fires.
  const [comparing, setComparing] = useState(false)
  const compareDays = comparing ? days * 2 : days
  const { data: compareTrendData } = useTrendData(compareDays, suiteFilter)

  const comparison = useMemo(() => {
    if (!comparing) return null
    const points = (compareTrendData?.data ?? []).slice().sort(
      (a, b) => a.date.localeCompare(b.date),
    )
    if (points.length < 2) return null
    // Sparse series contain only days with runs, so splitting by row count can
    // put old dates in the current window. Partition by the UTC calendar
    // boundary the API promises instead.
    const currentStart = shiftDayIso(utcDayIso(), -(days - 1))
    const priorStart = shiftDayIso(currentStart, -days)
    const prior = points.filter(point => point.date >= priorStart && point.date < currentStart)
    const current = points.filter(point => point.date >= currentStart)
    const summarise = (pts: TrendPoint[]) => {
      const passed   = pts.reduce((s, p) => s + (p.passed || 0), 0)
      const failed   = pts.reduce((s, p) => s + (p.failed || 0), 0)
      const broken   = pts.reduce((s, p) => s + (p.broken || 0), 0)
      const skipped  = pts.reduce((s, p) => s + (p.skipped || 0), 0)
      const total    = pts.reduce(
        (s, p) => s + (p.total ?? (p.passed + p.failed + p.skipped + (p.broken ?? 0))),
        0,
      )
      const denom    = passed + failed + broken
      const passRate = denom > 0 ? (passed / denom) * 100 : 0
      // ``days with runs`` is what /coverage's KPI strip surfaces — the
      // count of days that have ANY run activity, prior vs current.
      const daysWithRuns = pts.filter(
        p => (p.passed || 0) + (p.failed || 0) + (p.skipped || 0) + (p.broken || 0) > 0,
      ).length
      return { passed, failed, skipped, total, passRate, days: pts.length, daysWithRuns }
    }
    return { prior: summarise(prior), current: summarise(current) }
  }, [comparing, compareTrendData, days])

  const calendarDaysWithRuns = useMemo(() => {
    if (!trendData) return undefined
    const { cells } = coverageCadence(trendData.data ?? [], days, utcDayIso())
    // The strip draws at most CADENCE_MAX_CELLS days; past that, the summary's count.
    return days <= CADENCE_MAX_CELLS ? countTones(cells).pass : undefined
  }, [trendData, days])
  const model = useMemo(
    () => computeHealthModel(summary, suites, days, calendarDaysWithRuns),
    [summary, suites, days, calendarDaysWithRuns],
  )
  const verdict: Verdict = verdictForScore(model.composite)
  const gaps = useMemo(() => buildGaps(model, suites, days), [model, suites, days])

  // Surface the most-recent run's suite in the header so a user landing here
  // can see which suite the coverage snapshot represents at a glance. Must
  // sit before the early-return below so React's hook order stays stable
  // across renders (react-hooks/rules-of-hooks).
  const { data: latestRuns } = useRuns({ page: 1, size: 1, days, ...(suiteFilter && { suite_name: suiteFilter }) })

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view coverage data."
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  const latestRun = latestRuns?.items?.[0]

  // The Total executions delta: the later half of the window's trend days
  // against the earlier half (it is NOT the previous window; this page has no
  // read of that unless "Compare to previous window" is on). Too few days, or
  // an earlier half that ran nothing, is unmeasured and not drawn.
  const totalExecDelta = (() => {
    if (trend.length < 2) return null
    const half = Math.max(1, Math.floor(trend.length / 2))
    const recent = trend.slice(-half).reduce((s, p) => s + p.passed + p.failed + p.skipped + (p.broken ?? 0), 0)
    const prior  = trend.slice(0, half).reduce((s, p) => s + p.passed + p.failed + p.skipped + (p.broken ?? 0), 0)
    if (prior === 0) return null
    return ((recent - prior) / prior) * 100
  })()

  const lede = (() => {
    if (verdict === 'PENDING') return <>No executions in the last {days} days. Run a workflow to populate the coverage signal.</>
    if (verdict === 'HEALTHY') return <>Coverage signal is solid across {model.suiteCount} suite{model.suiteCount === 1 ? '' : 's'}. Pass rate, tagging, cadence, and breadth are all above target.</>
    const failingNames = suites.filter(s => !isUntaggedRow(s) && s.failed > 0).slice(0, 1).map(s => s.suite_name)
    const failingCount = suites.filter(s => !isUntaggedRow(s) && s.failed > 0).length
    return (
      <>
        Coverage signal is mixed —
        {failingNames[0] ? <> <strong style={{ color: 'var(--color-text)' }}>{failingNames[0]} is failing</strong></> : null}
        {failingCount > 1 ? <> ({failingCount} suites in total)</> : null}
        {model.untaggedRuns > 0 ? <> and {model.untaggedRuns} of {model.totalRuns} executions can't be attributed to a suite</> : null}.
        {model.daysWithRuns < days * 0.5
          ? <> The {days}-day window only saw runs on {model.daysWithRuns} day{model.daysWithRuns === 1 ? '' : 's'}, so trend confidence is limited.</>
          : null}
      </>
    )
  })()

  // The crumb the old header drew as chips, as the compact header's one line.
  const latestSuite = latestRunSuiteText(latestRun)
  const subtitle = [
    `Project ${projectLabel}`,
    suiteLabel ? `Suite ${suiteLabel}` : null,
    days === 1 ? 'last 24 hours' : `last ${days} days`,
    latestSuite ? `Latest run suite ${latestSuite}` : null,
    `Updated ${refreshedAt}`,
  ].filter(Boolean).join(' · ')

  // Every header action beyond the saved Views (the one secondary button):
  // the verdict card's two buttons and the header's Export and suites link.
  const overflow: OverflowItem[] = [
    {
      label: comparing ? 'Hide comparison' : 'Compare to previous window',
      icon: <GitCompare aria-hidden="true" className="h-3.5 w-3.5" />,
      onClick: () => setComparing(c => !c),
    },
    {
      label: 'Export CSV',
      icon: <Download aria-hidden="true" className="h-3.5 w-3.5" />,
      onClick: () => handleCoverageExportCsv({
        summary, suites, trend, project, days, suiteFilter: suiteLabel || null,
        healthScore: model.composite,
        verdict,
      }),
    },
    { label: 'Open triage queue', icon: <ListChecks aria-hidden="true" className="h-3.5 w-3.5" />, href: `/failures?days=${days}` },
    // /coverage/suite needs a suite `name`: the list of every suite is /suites.
    { label: 'View all suites', icon: <Layers aria-hidden="true" className="h-3.5 w-3.5" />, href: '/suites' },
  ]

  const untaggedGroups = suites.filter(isUntaggedRow).length
  const executionsChange: { trend_direction: 'up' | 'down' | 'flat' | 'none'; trend_text?: string } =
    totalExecDelta == null || !Number.isFinite(totalExecDelta)
      ? { trend_direction: 'none' }
      : totalExecDelta === 0
        ? { trend_direction: 'flat', trend_text: '· later vs earlier half of window' }
        : {
            trend_direction: totalExecDelta > 0 ? 'up' : 'down',
            trend_text: `${Math.abs(Math.round(totalExecDelta))}% · later vs earlier half of window`,
          }
  const cadencePaused = model.daysWithRuns < Math.ceil(days * 0.3)

  return (
    <PageShell className="space-y-4">
      {/* The page's one filter row is the header's right side, where the old
          header had it (and as Trends and Home have it): the suite, the
          window, then the saved Views (the one secondary button), then ⋯. */}
      <PageHeader
        compact
        title="Test Coverage"
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

      {isLoading && !coverageData ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : coverageError && !coverageData ? (
        <DataUnavailable
          error={coverageError}
          onRetry={() => void retryCoverage()}
          testId="coverage-data-unavailable"
        />
      ) : suites.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="No coverage data yet"
          description="Upload test results or run a workflow to populate coverage analytics."
        />
      ) : (
        <>
          {/* It wraps to fewer columns when its tiles cannot fit (KpiStrip). */}
          <section aria-label="Coverage metrics">
            <KpiStrip>
              <MetricCard
                compact
                title="Unique tests"
                icon={<TestTube className="h-4 w-4" />}
                metric={{ value: summary.unique_tests ?? 0 }}
              />
              <MetricCard
                compact
                title="Test suites"
                icon={<Layers className="h-4 w-4" />}
                metric={{
                  value: summary.suite_count ?? 0,
                  trend_direction: 'none',
                  trend_text: [
                    untaggedGroups > 0 ? `+ ${untaggedGroups} untagged` : null,
                    suites.filter(s => !isUntaggedRow(s)).slice(0, 3).map(s => s.suite_name).join(' · ') || '—',
                  ].filter(Boolean).join(' · '),
                }}
              />
              <MetricCard
                compact
                title="Total executions"
                icon={<BarChart3 className="h-4 w-4" />}
                metric={{ value: summary.total_executions ?? 0, ...executionsChange }}
              />
              <MetricCard
                compact
                title="Pass rate"
                icon={<TrendingUp className="h-4 w-4" />}
                metric={{
                  value: `${(summary.avg_pass_rate ?? 0).toFixed(1)}%`,
                  trend_direction: 'none',
                  trend_text: (() => {
                    const passed  = suites.reduce((s, x) => s + x.passed,  0)
                    const failed  = suites.reduce((s, x) => s + x.failed,  0)
                    const skipped = suites.reduce((s, x) => s + x.skipped, 0)
                    return `${passed} passed · ${failed} failed · ${skipped} skipped`
                  })(),
                }}
              />
              <MetricCard
                compact
                title="Run cadence"
                icon={<Calendar className="h-4 w-4" />}
                metric={{
                  value: `${model.daysWithRuns} / ${days} days`,
                  trend_direction: 'none',
                  trend_text: cadencePaused ? '⚠ Schedule may be paused' : `${Math.round((model.daysWithRuns / days) * 100)}% of window`,
                }}
              />
            </KpiStrip>
          </section>

          {comparing && (
            comparison ? (
              <CoverageComparisonStrip
                current={comparison.current}
                prior={comparison.prior}
                windowDays={days}
              />
            ) : (
              <section
                className="rounded-xl border"
                style={{
                  background: 'var(--color-bg-card)',
                  borderColor: 'var(--color-border)',
                  padding: '14px 16px',
                  marginBottom: 14,
                }}
              >
                <div className="flex items-center justify-between text-[12.5px] text-[var(--color-text-muted)]">
                  <span>Compare to previous window</span>
                  <span>last {days}d vs prior {days}d</span>
                </div>
                <p className="m-0 mt-2 text-[12.5px] text-[var(--color-text-muted)]">
                  Not enough trend data to compare against the prior window yet.
                </p>
              </section>
            )
          )}

          {/* Body grid — 1.65fr | 1fr from 768 px, one column below it. The
              suite table (left) is the page's primary content. */}
          <div className="body-grid grid gap-3.5" style={{ gridTemplateColumns: twoBodyColumns ? BODY_GRID_TWO_COLUMNS : BODY_GRID_ONE_COLUMN }}>
            <div className="flex flex-col gap-3.5 min-w-0">
              <SuiteBreakdown suites={suites} totalExecutions={summary.total_executions ?? 0} days={days} />
              <UntaggedCallout untaggedRuns={model.untaggedRuns} totalRuns={model.totalRuns} suites={suites.filter(isUntaggedRow)} />
            </div>
            <div className="flex flex-col gap-3.5 min-w-0">
              <CadenceHeatmap trend={trend} days={days} />
            </div>
          </div>

          <Disclosure
            title="How this score is computed"
            summary={verdict === 'PENDING' ? 'Pending · no score yet' : `${VERDICT_THEME[verdict].label} · health ${model.composite} / 100`}
          >
            <ScoreDetails model={model} verdict={verdict} lede={lede} gaps={gaps} />
          </Disclosure>

          <div className="space-y-3">
            <Tabs ariaLabel="Coverage views" items={COVERAGE_TABS} value={tab} onChange={setTab} />
            {/* Keyed: a tab's section mounts when it opens and goes when it
                closes. The block's own top margin is dropped: the tab panel
                spaces it. */}
            <div key={tab} data-tab-panel={tab} data-coverage-scope={tab} className="min-w-0 [&_[data-coverage-advanced]]:mt-0">
              <PageSuiteTargetContext.Provider value={suiteTarget}>
                <CoverageAdvanced days={days} suiteFilter={suiteFilter} sections={COVERAGE_TAB_SECTIONS[tab]} />
              </PageSuiteTargetContext.Provider>
            </div>
          </div>
        </>
      )}

      {/* Below lg the two-column layout is cramped: say so rather than hide it. */}
      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full coverage layout. Some sections may overflow on narrow viewports.
      </div>
    </PageShell>
  )
}
