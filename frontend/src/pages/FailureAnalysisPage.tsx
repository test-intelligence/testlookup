/**
 * Failure Analysis — the UX redesign's page template (P3, `02-design-spec.md`
 * §2 and the §5 Failures row), at 1440 x 900:
 *
 *   Header  → `PageHeader` (compact, **?** = the failure-analysis topic): the
 *             window (`WindowPicker`, the global store) and the suite filter
 *             share the header row with "Views" (secondary) and "Open triage
 *             queue" (primary); Export, Notify owner and Classify are in ⋯.
 *             (A toolbar row of its own put the table past the fold budget
 *             with both a banner and a KPI strip above it.)
 *   Verdict → `StatusBanner` in the "Failure verdict" landmark: the verdict,
 *             the stability score, pass rate, the latest failing suite, the
 *             data's age; its one action toggles the previous-window
 *             comparison.
 *   KPIs    → `KpiStrip`: Repeat failures · Flaky tests · Uncategorized ·
 *             Total executions ("Failure metrics").
 *   PRIMARY → the Top failing table (`data-primary`): the top failing tests
 *             merged with the flaky list — the old "What's failing" card,
 *             the repeat-failure signal and the Flakiness card in one table
 *             (14-day run strip in its header). Row actions: Mute (quarantine
 *             proposal), Jira (dedup-first defect), Suspects (a `SidePanel`
 *             with the ranked suspect commits, Epic 8 US-8.2 — the bisect).
 *   Tabs    → `?tab=`: Groups (failure groups + systemic clusters) · By suite
 *             (the drill ladder) · Scatter (the project test scatter) ·
 *             Categories (failure-kind chips + category distribution). The
 *             three catalogue sections are lazy chunks, each mounted only in
 *             its own tab (the groups also only once near, as before).
 *   Below   → Disclosures: "How this score is computed" (stability meter,
 *             the four weighted dimensions, suites with failures) and the
 *             failure timeline.
 *
 * Data: derives the verdict from existing useFlakyTests / useTopFailing /
 * useFailureCategories / useTrendData. There is no per-failure endpoint
 * (stack traces, owners, mean time to fix), so the page shows none of those: the
 * run strip is filled from the trend tail.
 *
 * Wired actions (US-2.4):
 *   - "Mute" → manual quarantine proposal (POST /api/v1/quarantine,
 *     QA_LEAD+; lands as PROPOSED pending approval on /quarantine).
 *   - "Correct the classification" → analysis lookup by fingerprint
 *     (GET /projects/{id}/analyses/lookup) + rating=incorrect feedback
 *     (POST /feedback/{analysis_id}) feeding the training loop.
 *   - "Jira" (US-6.1) → server-prefilled, dedup-first defect
 *     creation via POST /projects/{id}/defects/jira; disabled with a
 *     tooltip when Jira is offline-gated/unconfigured AND no webhook
 *     receiver is subscribed (US-6.3 fallback).
 *   - "Suspects" (Epic 8 US-8.2, the re-added bisect) opens the Suspects
 *     panel ("Who / what changed") — ranked commits landed since the
 *     last green run, scored by path overlap with the failing test (backend
 *     GET /api/v1/runs/{id}/suspects). Bisect = "show the suspect commit range
 *     for this failure". Framed as suspects, never culprits (monorepo caveat).
 */
import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ChevronRight, Download, GitCommit, Mail, Search, ShieldCheck,
  Tags, TriangleAlert,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import CreateJiraIssueModal, { jiraUnavailableCopy } from '@/components/defects/CreateJiraIssueModal'
import EmptyState from '@/components/ui/EmptyState'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import StatusBanner, { type BannerFact, type BannerState } from '@/components/ui/StatusBanner'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import Disclosure from '@/components/ui/Disclosure'
import SidePanel from '@/components/ui/SidePanel'
import WindowPicker from '@/components/ui/WindowPicker'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useSuspects } from '@/hooks/useCommitAttribution'
import CorrectClassificationModal, {
  CATEGORY_CHOICES,
} from '@/components/ai/CorrectClassificationModal'
import { useJiraDefectMetadata } from '@/hooks/useJiraDefects'
import { useRuns } from '@/hooks/useRuns'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { shortAgo } from '@/utils/formatters'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { flakyQuarantineService } from '@/services/flakyQuarantineService'
import { postData } from '@/services/http'
import {
  useFailureCategories, useFlakyTests, useTopFailing, useTrendData,
} from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { PageSuiteTargetContext, usePageSuiteTarget } from '@/hooks/pageSuiteTarget'
import { bulkWriteSuite } from '@/lib/scopeParams'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type {
  FailureCategoryItem, FailureKindCount, FlakyTestItem, TopFailingItem,
} from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'
import {
  FAILURE_KIND_DEFS, failureKindOf, kindDef, type FailureKind,
} from '@/utils/failureKind'
import { FailureKindBadge, KindBadgeWithEvidence } from '@/components/failures/KindEvidence'
import { utcDayIso } from '@/utils/calendarDay'
import GaugeBar from '@/components/charts/GaugeBar'
import { useContainerWidth } from '@/components/charts/chartLayout'
import DayStrip from '@/components/charts/DayStrip'
import { countTones, dayWindow, type DayStripCell } from '@/components/charts/dayStrip.model'
import { csvBlob, csvCell } from '@/lib/viz/csv'
import { downloadBlob } from '@/utils/download'
import LazySection from '@/components/reports/catalogue/LazySection'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import { lazyWithRetry } from '@/utils/lazyWithRetry'

// ── The catalogue sections, one per tab ───────────────────────────────────
// Each is a lazy chunk of its own, fetched only when its tab is open (they
// were one composite, `FailuresAdvanced`, stacked under the page). Dynamic
// imports: no section code, chart kit or d3 reaches this page's static
// closure (the d3-confinement and section-only ratchets walk static imports).
const FailureGroupsSection = lazyWithRetry(() => import('@/components/reports/catalogue/FailureGroupsSection'))
const FailuresDrill = lazyWithRetry(() => import('@/components/reports/catalogue/FailuresDrill'))
const ScatterSection = lazyWithRetry(() => import('@/components/reports/catalogue/ScatterSection'))

/**
 * The groups section's height when drawn, px: its placeholder until it is near
 * (the groups have no lazy box of their own; the ladder and the scatter do).
 * Measured with the composite (X3, 1280 x 800): bubbles + ranked table + hint.
 */
const GROUPS_HEIGHT = 860

/** The page's help topic (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/failures')

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours (rendered as "24h"); the rest are day counts. Mirrors
// Overview/Runs/Live/Trends/Coverage so users get a single mental model.
const WINDOWS = [1, 7, 14, 30, 90] as const
type Window = (typeof WINDOWS)[number]

// ── CSV export ────────────────────────────────────────────────────────────
// Cells go through the shared ``csvCell`` (lib/viz/csv.ts, VIZ-606) — the
// same RFC 4180 quoting this page always used — which ALSO neutralises
// formula injection: test and suite names come from ingested CI reports, and
// a test named ``=HYPERLINK(…)`` must not be evaluated by the spreadsheet
// that opens this file — nor ``x;=…`` in the locales that split on ``;``
// (csv.ts says how). Plain numbers are untouched.

interface ExportSources {
  topFailing: TopFailingItem[]
  flaky: FlakyTestItem[]
  categories: FailureCategoryItem[]
  meta: {
    projectName: string
    windowLabel: string
    suiteName: string | null
    generatedAt: string
  }
}

/** Build the multi-section CSV that ``Export`` produces. Three sections:
 *  metadata header, top failing tests, failure categories, flaky tests.
 *  Sections are separated by a blank line and a ``# Section`` marker so
 *  Excel/Sheets users can navigate without manual splitting. */
export function buildFailuresCsv({
  topFailing, flaky, categories, meta,
}: ExportSources): string {
  const lines: string[] = []

  // Header / metadata block — explains the source of truth so a CSV
  // pasted into a Slack channel still answers "what window / project".
  lines.push('# TestLookup — Failure analysis export')
  lines.push(`# Project,${csvCell(meta.projectName)}`)
  lines.push(`# Window,${csvCell(meta.windowLabel)}`)
  lines.push(`# Suite filter,${csvCell(meta.suiteName ?? 'All suites')}`)
  lines.push(`# Generated,${csvCell(meta.generatedAt)}`)
  lines.push('')

  lines.push('# Top failing tests')
  lines.push(['test_name', 'suite_name', 'class_name', 'failure_category', 'fail_count', 'last_failed'].join(','))
  for (const t of topFailing) {
    lines.push([
      csvCell(t.test_name),
      csvCell(t.suite_name ?? ''),
      csvCell(t.class_name ?? ''),
      csvCell(t.failure_category ?? ''),
      csvCell(t.fail_count),
      csvCell(t.last_failed ?? ''),
    ].join(','))
  }
  lines.push('')

  lines.push('# Failure categories')
  lines.push(['category', 'count'].join(','))
  for (const c of categories) {
    lines.push([csvCell(c.category), csvCell(c.count)].join(','))
  }
  lines.push('')

  lines.push('# Flaky tests')
  lines.push(['test_name', 'suite_name', 'total_runs', 'fail_count', 'failure_rate_pct'].join(','))
  for (const f of flaky) {
    lines.push([
      csvCell(f.test_name),
      csvCell(f.suite_name ?? ''),
      csvCell(f.total_runs),
      csvCell(f.fail_count),
      csvCell(f.failure_rate_pct),
    ].join(','))
  }
  // Trailing newline so POSIX tooling (wc -l, awk) counts the last row.
  return lines.join('\r\n') + '\r\n'
}

/** Build a filename slug from a project name. Lowercases, replaces any
 *  non-alphanumeric run with a single dash, and trims edge dashes. */
function slugifyProjectName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'project'
}

/** Triggers a CSV download of the in-memory failure data. Pure DOM —
 *  no backend round-trip — because the data the user wants is already
 *  on the page; a server-side ``GET /export`` would just re-serialise
 *  what we already have. */
function handleExportCsv({
  topFailing, flaky, categories, project, days, suiteFilter,
}: {
  topFailing: TopFailingItem[]
  flaky: FlakyTestItem[]
  categories: FailureCategoryItem[]
  project: { id: string; name: string } | null
  days: number
  suiteFilter: string | null
}): void {
  const hasData = topFailing.length > 0 || flaky.length > 0 || categories.length > 0
  if (!hasData) {
    toast('No failure data to export in this window', { icon: '📭' })
    return
  }
  const windowLabel = days === 1 ? '24h' : `${days}d`
  const csv = buildFailuresCsv({
    topFailing, flaky, categories,
    meta: {
      projectName: project?.name ?? 'All projects',
      windowLabel,
      suiteName: suiteFilter,
      generatedAt: new Date().toISOString(),
    },
  })
  // BOM so Excel opens the file with UTF-8 encoding by default;
  // without it, non-ASCII test names (German umlauts, Japanese
  // characters in suite labels, etc.) render as mojibake. ``csvBlob`` adds it.
  const blob = csvBlob(csv)
  const projectSlug = project ? slugifyProjectName(project.name) : 'all-projects'
  const suiteSlug = suiteFilter ? `-${slugifyProjectName(suiteFilter)}` : ''
  // ``downloadBlob`` revokes the object URL only AFTER the browser has read
  // the blob; the synchronous revoke this used to do can cancel the download.
  downloadBlob(blob, `failures-${projectSlug}${suiteSlug}-${windowLabel}.csv`)
  toast.success(`Exported ${topFailing.length} failing test${topFailing.length === 1 ? '' : 's'}`)
}
// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'REPEAT_FAILURE' | 'FLAKY' | 'FIRST_TIME' | 'RECOVERING' | 'STABLE' | 'PENDING'

/** The verdict's words and hues: the banner's title, and the score meter in "How this score is computed". */
interface VerdictTheme {
  label: string
  /** The banner's state (its pill word and hue). */
  banner: BannerState
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  /** One sentence on what the verdict means: the banner title's tooltip (never a paragraph on the page). */
  meaning: string
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  REPEAT_FAILURE: {
    label:  'Repeat failure',
    banner: 'fail',
    pillBg: 'color-mix(in srgb, var(--status-failed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    meaning: 'The same tests failed on every observed run in the window: a deterministic regression, not a flake. Re-running will not fix it; bisect against the last green commit.',
  },
  FLAKY: {
    label:  'Flaky',
    banner: 'warn',
    pillBg: 'color-mix(in srgb, var(--status-broken) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    meaning: 'Tests show pass/fail oscillation on the same SHA. Re-runs may pass without fixing the underlying race or fixture issue.',
  },
  FIRST_TIME: {
    label:  'First-time failure',
    banner: 'warn',
    pillBg: 'color-mix(in srgb, var(--status-broken) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    meaning: 'A new failure landed in this window with no prior history. Check the recent merges and the failure category before deciding to gate.',
  },
  RECOVERING: {
    label:  'Recovering',
    banner: 'ok',
    pillBg: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
    pillFg: 'var(--color-accent)',
    meter:  'var(--color-accent)',
    meaning: 'Previously failing tests have started passing again. Confirm with one more run before declaring resolution.',
  },
  STABLE: {
    label:  'Stable',
    banner: 'ok',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    meaning: 'Every test in the window passed. No regressions, no flakes: nothing to triage.',
  },
  PENDING: {
    label:  'Pending',
    banner: 'pending',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    meaning: 'No executions in the window. Run a workflow or extend the window to populate failure analysis.',
  },
}

// ── Stability model ───────────────────────────────────────────────────────
// Weights from README §5.4: Pass 35 / Categorization 20 / Flake-free 20 /
// Time to fix 25.
type DimensionId = 'pass_rate' | 'categorization' | 'flake_free' | 'time_to_fix'
const WEIGHTS: Record<DimensionId, number> = {
  pass_rate:      0.35,
  categorization: 0.20,
  flake_free:     0.20,
  time_to_fix:    0.25,
}

interface DimensionScore {
  id: DimensionId
  label: string
  score: number
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

interface StabilityModel {
  composite: number
  dimensions: DimensionScore[]
  totalRuns: number
  passedRuns: number
  failedRuns: number
  skippedRuns: number
  repeatFailures: TopFailingItem[]
  flakyCount: number
  uncategorizedPct: number
  unknownCount: number
  totalCategorised: number
  topFailingTest: TopFailingItem | null
  trend: TrendPoint[]
}

function computeStabilityModel({
  flaky, categories, topFailing, trend,
}: {
  flaky: FlakyTestItem[]
  categories: FailureCategoryItem[]
  topFailing: TopFailingItem[]
  trend: TrendPoint[]
}): StabilityModel {
  const passedRuns  = trend.reduce((s, p) => s + p.passed,  0)
  const failedRuns  = trend.reduce((s, p) => s + p.failed,  0)
  const skippedRuns = trend.reduce((s, p) => s + p.skipped, 0)
  const totalRuns = passedRuns + failedRuns + skippedRuns

  const passRatePct = totalRuns > 0 ? (passedRuns / totalRuns) * 100 : 0

  const totalCategorised = categories.reduce((s, c) => s + c.count, 0)
  const unknownCount = categories
    .filter(c => /unknown|unclassified/i.test(c.category))
    .reduce((s, c) => s + c.count, 0)
  const uncategorizedPct = totalCategorised > 0
    ? (unknownCount / totalCategorised) * 100
    : 0

  const flakyCount = flaky.length
  // Manually-triaged flakes carry failure_rate_pct=100 as a "human-flagged"
  // marker, not a measured rate. Exclude them from the flake-free *score* —
  // otherwise a single human-flagged test pins the score to 0 regardless of the
  // actual measured intermittency. They still count toward flakyCount/verdict
  // (a known flake is a known flake).
  const autoFlakes = flaky.filter(f => f.source !== 'manual')
  const flakeFreeScore = autoFlakes.length === 0
    ? 100
    : Math.max(0, 100 - Math.max(...autoFlakes.map(f => f.failure_rate_pct)))

  const repeatFailures = topFailing.filter(t => t.fail_count >= 2)

  // Time-to-fix: synthesise from consecutive-failure tail length.
  let timeToFixScore = 100
  if (failedRuns > 0) {
    let consecutiveFailingDays = 0
    for (let i = trend.length - 1; i >= 0; i--) {
      const p = trend[i]
      if (p.failed > 0) consecutiveFailingDays++
      else if (p.passed > 0) break
    }
    if (consecutiveFailingDays > 0) {
      timeToFixScore = Math.max(0, 100 - consecutiveFailingDays * 14)
    } else {
      timeToFixScore = 70
    }
  }

  const categorizationScore = totalCategorised > 0
    ? Math.max(0, 100 - uncategorizedPct)
    : (failedRuns === 0 ? 100 : 0)

  const dimensions: DimensionScore[] = [
    { id: 'pass_rate',      label: 'Pass rate',      score: passRatePct,         weight: WEIGHTS.pass_rate,      tone: toneFor(passRatePct) },
    { id: 'categorization', label: 'Categorization', score: categorizationScore, weight: WEIGHTS.categorization, tone: toneFor(categorizationScore) },
    { id: 'flake_free',     label: 'Flake-free',     score: flakeFreeScore,      weight: WEIGHTS.flake_free,     tone: toneFor(flakeFreeScore) },
    { id: 'time_to_fix',    label: 'Time to fix',    score: timeToFixScore,      weight: WEIGHTS.time_to_fix,    tone: toneFor(timeToFixScore) },
  ]
  const composite = Math.round(dimensions.reduce((sum, d) => sum + d.score * d.weight, 0))

  return {
    composite, dimensions, totalRuns, passedRuns, failedRuns, skippedRuns,
    repeatFailures, flakyCount, uncategorizedPct, unknownCount, totalCategorised,
    topFailingTest: topFailing[0] ?? null, trend,
  }
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 33) return 'warn'
  return 'bad'
}

function pickVerdict(model: StabilityModel): Verdict {
  if (model.totalRuns === 0) return 'PENDING'
  if (model.failedRuns === 0 && model.flakyCount === 0) return 'STABLE'
  if (model.repeatFailures.length > 0 && model.flakyCount === 0) return 'REPEAT_FAILURE'
  if (model.flakyCount > 0) return 'FLAKY'
  if (model.failedRuns > 0) return 'FIRST_TIME'
  return 'STABLE'
}

// ── "How this score is computed" (a Disclosure below the primary content) ──
// The stability meter, the four weighted dimensions and the suites with
// failures: the verdict card's right half, which the banner replaced above
// the fold (§2: the gauge and its dimensions move below the primary content).
function ScoreDetails({ model, verdict, topFailing }: { model: StabilityModel; verdict: Verdict; topFailing: TopFailingItem[] }) {
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}>
      <div className="flex flex-col gap-3.5 min-w-0">
        <StabilityMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
      <div className="min-w-0">
        <SuiteFailureBreakdown topFailing={topFailing} />
      </div>
    </div>
  )
}

function StabilityMeter({ model, verdict }: { model: StabilityModel; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  return (
    <div>
      <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Stability score
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
      {/* `--gradient-stability` IS `--gradient-health` (index.css). PENDING
          has no score to place, so the bar is not measured, never a 0 fill. */}
      <GaugeBar
        className="mt-3"
        value={verdict === 'PENDING' ? null : score}
        label="Stability score"
        valueText={`${score} of 100, ${t.label}`}
        gradient="health"
        ticks={STABILITY_TICKS}
      />
    </div>
  )
}

const STABILITY_TICKS = [
  { value: 0, label: 'Block' },
  { value: 33, label: 'At risk' },
  { value: 66, label: 'Stable' },
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

// ── Suite-level failure breakdown ─────────────────────────────────────────
// Surfaces which test suites are accumulating failures in the current
// window so the verdict isn't just "X tests broken" without context. Bins
// the topFailing list by ``suite_name`` (server returns it per row),
// sorts by total failures, and shows the top 4 suites + an "Other" row.
function SuiteFailureBreakdown({ topFailing }: { topFailing: TopFailingItem[] }) {
  const rows = useMemo(() => {
    const byBin = new Map<string, { suite: string; failures: number; tests: number }>()
    for (const t of topFailing) {
      const suite = (t.suite_name && t.suite_name.trim()) || 'Unknown Suite'
      const cur = byBin.get(suite) ?? { suite, failures: 0, tests: 0 }
      cur.failures += t.fail_count
      cur.tests += 1
      byBin.set(suite, cur)
    }
    return [...byBin.values()].sort((a, b) => b.failures - a.failures)
  }, [topFailing])

  if (rows.length === 0) return null

  const totalFailures = rows.reduce((s, r) => s + r.failures, 0)
  const head = rows.slice(0, 4)
  const tail = rows.slice(4)
  const tailRow = tail.length > 0
    ? {
        suite: `+${tail.length} more`,
        failures: tail.reduce((s, r) => s + r.failures, 0),
        tests: tail.reduce((s, r) => s + r.tests, 0),
      }
    : null

  return (
    <div
      className="rounded-sm px-2.5 py-2 border"
      style={{ background: 'rgba(255,255,255,0.025)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] flex justify-between mb-1.5"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        <span>Suites with failures</span>
        <span className="text-[var(--color-text-faint)] font-medium">{rows.length}</span>
      </div>
      <ul className="m-0 p-0 list-none space-y-1">
        {head.map(r => {
          const pct = totalFailures > 0 ? Math.round((r.failures / totalFailures) * 100) : 0
          return (
            <li key={r.suite} className="flex items-center gap-2 text-[11.5px]">
              <span className="truncate flex-1 text-[var(--color-text-secondary)]" title={r.suite}>
                {r.suite}
              </span>
              <span className="tabular-nums text-[var(--color-text-muted)]">
                {r.tests} test{r.tests === 1 ? '' : 's'}
              </span>
              <div className="w-12 h-1 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
                <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: 'var(--status-failed)' }} />
              </div>
              <span className="tabular-nums font-semibold text-[var(--color-text)] min-w-[28px] text-right">
                {r.failures}
              </span>
            </li>
          )
        })}
        {tailRow && (
          <li className="flex items-center gap-2 text-[11.5px] text-[var(--color-text-faint)]">
            <span className="flex-1 truncate italic">{tailRow.suite}</span>
            <span className="tabular-nums">{tailRow.tests} tests</span>
            <span className="w-12" aria-hidden />
            <span className="tabular-nums min-w-[28px] text-right">{tailRow.failures}</span>
          </li>
        )}
      </ul>
    </div>
  )
}

// ── What's failing card ───────────────────────────────────────────────────
/** The What's-failing run strip always shows the last fortnight, whatever the window. */
export const RUN_STRIP_DAYS = 14

/**
 * The run strip: the last `RUN_STRIP_DAYS` days ending on `todayIso`, one
 * cell each — `fail` if the day had a failed OR broken result, `pass` if it
 * had a pass and neither, otherwise `none` ("not run": nothing, or skips only).
 *
 * Broken is a failure-like result, not an absence: a broken-only day used to
 * be drawn as "not run", so a day whose every test broke looked like a quiet
 * day. A chart does not silently drop a status (the owner's rule: Overview
 * OD-7, Trends OD-16); the timeline below counts broken the same way.
 */
export function runStripCells(trend: TrendPoint[], todayIso: string): DayStripCell[] {
  const byDate = new Map<string, { tone: DayStripCell['tone']; word: string }>()
  for (const p of trend) {
    const iso = p.date.slice(0, 10)
    const broken = p.broken ?? 0
    byDate.set(
      iso,
      p.failed > 0
        ? { tone: 'fail', word: broken > 0 ? 'failed and broken' : 'failed' }
        : broken > 0
          ? { tone: 'fail', word: 'broken' }
          : p.passed > 0
            ? { tone: 'pass', word: 'passed' }
            : { tone: 'none', word: NOT_RUN },
    )
  }
  return dayWindow(RUN_STRIP_DAYS, todayIso).map((iso) => {
    const day = byDate.get(iso) ?? { tone: 'none' as const, word: NOT_RUN }
    return { key: iso, label: `${iso} · ${day.word}`, tone: day.tone }
  })
}

/** The strip's aggregate name: B0's visual spec finds the strip by its "Run strip:" prefix. */
export function runStripLabel(cells: readonly DayStripCell[]): string {
  const c = countTones(cells)
  return `Run strip: ${c.fail} failed, ${c.pass} passed, ${c.none} not run.`
}

/**
 * The word both strips on this page use for a day with nothing evaluated.
 * Module-level so the strips' models are not rebuilt on every render.
 */
const NOT_RUN = 'not run'
const RUN_STRIP_TEXT = { none: 'Not run' }

// ── The Top failing table (the page's primary content) ────────────────────

/** One row: a failing test, with its flaky-list entry when the flake detector (or a human) flagged it. */
interface FailingRow {
  key: string
  test: TopFailingItem
  flaky: FlakyTestItem | null
  /** False for a test only the flaky list names: it carries no category, so no kind is claimed for it. */
  fromTopFailing: boolean
}

/** Rows drawn before "Show all": a list page is one viewport plus a page of 25 (§2). */
const TABLE_PAGE_ROWS = 25

/**
 * The top failing tests merged with the flaky list, most failures first (a
 * stable sort: equal counts keep the server's order, top-failing first).
 * Every failing test is one row; a flaky test the top-failing list does not
 * name is a row too — the Flakiness card's list, now in the same table.
 *
 * A flaky entry pairs with a test by fingerprint when both have one, else by
 * name: two suites may each have a "login times out", and only the
 * fingerprint tells them apart.
 */
function failingRows(topFailing: TopFailingItem[], flaky: FlakyTestItem[]): FailingRow[] {
  const paired = new Set<FlakyTestItem>()
  const flakyOf = (t: TopFailingItem): FlakyTestItem | null =>
    flaky.find(f => (t.test_fingerprint && f.test_fingerprint
      ? f.test_fingerprint === t.test_fingerprint
      : f.test_name === t.test_name)) ?? null
  const rows: FailingRow[] = topFailing.filter(t => t.fail_count > 0).map((t, i) => {
    const f = flakyOf(t)
    if (f) paired.add(f)
    return { key: `t${i}:${t.test_fingerprint ?? t.test_name}`, test: t, flaky: f, fromTopFailing: true }
  })
  flaky.forEach((f, i) => {
    if (paired.has(f) || f.fail_count <= 0) return
    rows.push({
      key: `f${i}:${f.test_fingerprint || f.test_name}`,
      test: {
        test_name: f.test_name,
        fail_count: f.fail_count,
        test_fingerprint: f.test_fingerprint || null,
        suite_name: f.suite_name ?? null,
        class_name: f.class_name ?? null,
      },
      flaky: f,
      fromTopFailing: false,
    })
  })
  return rows.sort((a, b) => b.test.fail_count - a.test.fail_count)
}

/** A percentage that never collapses a real sub-1 % rate to "0%": 8 of 2773 reads "0.3%". */
function fmtPct(n: number): string {
  return n > 0 && n < 1 ? `${n.toFixed(1)}%` : `${Math.round(n)}%`
}

/** Search for the test's runs: the record carries no run or test-case id to deep-link into. */
function testSearchHref(name: string): string {
  return `/search?q=${encodeURIComponent(name)}&scope=tests&mode=keyword`
}

/** The "Repeat failure" signal's meaning (the old "Why" toast), as its tooltip. */
const NOT_A_FLAKE =
  'Failed in two or more runs and not on the flaky list: a real failure, not a flake. A test is flaky when it both passed and failed on the same fingerprint within the window; re-running will not fix this one.'

interface RowActionHandlers {
  onMute: (row: FailingRow) => void
  muteDisabledReason: (row: FailingRow) => string | null
  onCreateJira: (row: FailingRow) => void
  createJiraDisabledReason: (row: FailingRow) => string | null
  onShowSuspects: (row: FailingRow) => void
  /** Set when there is no failed run in the window to attribute commits against. */
  suspectsDisabledReason: string | null
}

function TopFailingTable({
  rows, trend, totalRuns, failedExecutions, actions,
}: {
  rows: FailingRow[]
  trend: TrendPoint[]
  totalRuns: number
  /** The window's failed executions: the denominator of a test's share of failures. */
  failedExecutions: number
  actions: RowActionHandlers
}) {
  const [showAll, setShowAll] = useState(false)
  // Aggregate failed-run count from trend (which reads test_runs.failed_tests
  // directly). A suite can have failed run aggregates (pass rate < 100%)
  // while test_cases rows haven't landed — the live-stream Redis-buffer gap
  // from CLAUDE.md pitfall #15. Cross-check so we don't render the green
  // "every recent run passed" all-clear while pass-rate / KPI tiles show
  // the same suite is failing.
  const failingExecutions = trend.reduce(
    (s, p) => s + (p.failed || 0) + (p.broken || 0), 0,
  )
  const perTestRowsMissing = !rows.some(r => r.fromTopFailing) && failingExecutions > 0

  const cells = runStripCells(trend, utcDayIso())
  const { fail: failedCells, pass: passedCells, none: notRunCells } = countTones(cells)
  const runStrip = (
    <div className="flex items-center gap-2 px-4 py-2" style={{ borderBottom: '1px solid var(--color-border)' }}>
      <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Last 14 days
      </span>
      {/* Fail is `--status-failed` (it was `--gate-no-go`, which index.css
          defines as `var(--status-failed)`: one name for one meaning — OD-9). */}
      <DayStrip
        className="flex-1 min-w-0"
        cells={cells}
        mode="status"
        label={runStripLabel(cells)}
        title="Run strip"
        cellHeight={14}
        legend={false}
        text={RUN_STRIP_TEXT}
      />
      <span className="text-[10.5px] tabular-nums text-[var(--color-text-faint)]">
        {failedCells} fail · {notRunCells} idle · {passedCells} pass
      </span>
    </div>
  )

  if (rows.length === 0) {
    return (
      <CardShell
        title="Top failing tests"
        rightSlot={perTestRowsMissing ? <Pill tone="warn">Per-test data pending</Pill> : <Pill tone="good">No failures</Pill>}
      >
        {perTestRowsMissing ? (
          <div className="px-4 py-6 flex flex-col items-center text-center">
            <TriangleAlert className="h-8 w-8 mb-2" style={{ color: 'var(--status-broken)' }} />
            <p className="text-[13px] text-[var(--color-text-secondary)] m-0 max-w-md">
              <strong style={{ color: 'var(--color-text)' }}>{failingExecutions}</strong>{' '}
              failing execution{failingExecutions === 1 ? '' : 's'} detected in this window,
              but per-test rows haven&apos;t been persisted yet — common right after a
              live-stream run finishes. Inspect the failed runs on the Runs page.
            </p>
          </div>
        ) : (
          <div className="px-4 py-6 flex flex-col items-center text-center">
            <ShieldCheck className="h-8 w-8 mb-2" style={{ color: 'var(--status-passed)' }} />
            <p className="text-[13px] text-[var(--color-text-secondary)] m-0">
              No failing tests in this window — every recent run passed.
            </p>
          </div>
        )}
      </CardShell>
    )
  }

  const shown = showAll ? rows : rows.slice(0, TABLE_PAGE_ROWS)
  return (
    <CardShell
      title="Top failing tests"
      rightSlot={
        <>
          {perTestRowsMissing && <Pill tone="warn">Per-test data pending</Pill>}
          <span>
            {rows.length} test{rows.length === 1 ? '' : 's'} · {totalRuns} execution{totalRuns === 1 ? '' : 's'} in window
          </span>
        </>
      }
    >
      {runStrip}
      <div className="overflow-x-auto">
        <table aria-label="Top failing tests" className="w-full text-[12.5px]">
          <thead>
            <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
              <Th label="Test" />
              <Th label="Kind" />
              <Th label="Failures" />
              <Th label="Signal" />
              <Th label="Last failed" />
              <Th label="Actions" align="right" />
            </tr>
          </thead>
          <tbody>
            {shown.map(row => (
              <FailingRowEl key={row.key} row={row} failedExecutions={failedExecutions} actions={actions} />
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > TABLE_PAGE_ROWS && (
        <div className="px-4 py-2 text-[12px]" style={{ borderTop: '1px solid var(--color-border)' }}>
          <button type="button" onClick={() => setShowAll(v => !v)} className="text-[var(--color-accent)] hover:underline">
            {showAll ? `Show the first ${TABLE_PAGE_ROWS}` : `Show all ${rows.length}`}
          </button>
        </div>
      )}
    </CardShell>
  )
}

function Th({ label, align }: { label: string; align?: 'right' }) {
  return (
    <th
      scope="col"
      className="whitespace-nowrap"
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

function FailingRowEl({ row, failedExecutions, actions }: { row: FailingRow; failedExecutions: number; actions: RowActionHandlers }) {
  const { test, flaky } = row
  // AI-classified failure kind (US-9.2): the server-derived value, else the
  // category mirror for older payloads. A flaky-only row has no category.
  const kind = row.fromTopFailing ? (test.failure_kind ?? failureKindOf(test.failure_category)) : null
  // The test's OWN runs are the only honest denominator for a failure rate,
  // and only the flaky list carries them (a human-flagged entry's counts are
  // a marker, not a measurement). Without them: the count and its share of
  // the window's failures — never one test's failures over every execution
  // in the window (the "0% failure rate — failed 8 of 2773" report).
  const measured = flaky && flaky.source !== 'manual' && flaky.total_runs > 0 ? flaky : null
  const sharePct = failedExecutions > 0 ? (test.fail_count / failedExecutions) * 100 : null
  const muteReason = actions.muteDisabledReason(row)
  const jiraReason = actions.createJiraDisabledReason(row)
  const suite = [test.suite_name, test.class_name].filter(Boolean).join(' · ')
  return (
    <tr data-failing-row="" style={{ borderBottom: '1px solid var(--color-border)' }} className="hover:bg-[var(--color-bg-hover)]">
      <td style={{ padding: '8px 12px', minWidth: 220 }}>
        <div className="flex flex-col gap-0.5 min-w-0">
          <Link
            to={testSearchHref(test.test_name)}
            title="Find this test's runs"
            className="font-mono text-[12.5px] font-medium truncate hover:underline"
            style={{ color: 'var(--status-failed)' }}
          >
            {test.test_name}
          </Link>
          {suite && <span className="text-[10.5px] text-[var(--color-text-muted)] truncate">{suite}</span>}
          {test.failure_step && (
            <span className="text-[10.5px] text-[var(--color-text-muted)] truncate" title={`Failed at step: ${test.failure_step}`}>
              failed at: <span className="font-mono" style={{ color: 'var(--status-failed)' }}>{test.failure_step}</span>
            </span>
          )}
        </div>
      </td>
      <td style={{ padding: '8px 12px' }}>
        {kind
          ? <KindBadgeWithEvidence kind={kind} testFingerprint={test.test_fingerprint} compact />
          : <span className="text-[var(--color-text-faint)]">—</span>}
      </td>
      <td style={{ padding: '8px 12px' }}>
        <div className="text-[13px] font-semibold tabular-nums" style={{ color: 'var(--status-failed)' }}>{test.fail_count}</div>
        {/* Wraps: on one line this cell pushed the table past its card at
            1280 px and the Jira / Suspects row actions off-screen. */}
        <div data-failure-rate="" className="text-[11px] text-[var(--color-text-muted)]">
          {measured
            ? <><strong>{fmtPct((measured.fail_count / measured.total_runs) * 100)} failure rate</strong> · failed {measured.fail_count} of {measured.total_runs} executions</>
            : sharePct !== null
              ? <>failed {test.fail_count} time{test.fail_count === 1 ? '' : 's'} — <strong>{fmtPct(sharePct)}</strong> of failures here</>
              : null}
        </div>
      </td>
      <td style={{ padding: '8px 12px' }}>
        <SignalCell row={row} />
      </td>
      <td className="whitespace-nowrap text-[11.5px] tabular-nums text-[var(--color-text-secondary)]" style={{ padding: '8px 12px' }} title={test.last_failed ?? undefined}>
        {test.last_failed ? shortAgo(test.last_failed) : '—'}
      </td>
      <td style={{ padding: '8px 12px' }}>
        <div className="flex justify-end gap-1.5">
          <RowAction
            label={`Mute test ${test.test_name}`}
            title={muteReason ?? 'Propose quarantine for this test with a documented reason'}
            disabled={Boolean(muteReason)}
            onClick={() => actions.onMute(row)}
          >
            Mute
          </RowAction>
          <RowAction
            label={`Create Jira issue for ${test.test_name}`}
            title={jiraReason ?? 'File a pre-filled Jira issue for this failure (dedups against open defects)'}
            disabled={Boolean(jiraReason)}
            onClick={() => actions.onCreateJira(row)}
          >
            Jira
          </RowAction>
          {/* Epic 8 US-8.2 — the bisect: the suspect commit range for this failure, in a side panel. */}
          <RowAction
            label={`Suspects for ${test.test_name}`}
            title={actions.suspectsDisabledReason ?? 'Bisect: show the suspect commits between the last green run and this failure'}
            disabled={Boolean(actions.suspectsDisabledReason)}
            onClick={() => actions.onShowSuspects(row)}
          >
            <GitCommit className="h-3 w-3" aria-hidden="true" /> Suspects
          </RowAction>
        </div>
      </td>
    </tr>
  )
}

/**
 * The row's status, only when the data says so: the flaky list names the
 * test (measured, or flagged by a human), or it failed in two or more runs
 * (the page's "Repeat failures" rule). Otherwise nothing.
 */
function SignalCell({ row }: { row: FailingRow }) {
  const { test, flaky } = row
  if (flaky?.source === 'manual') {
    return <Pill tone="neutral" title="Manually flagged as flaky on /my-failures (a human triage, not a measured rate)">Flagged</Pill>
  }
  if (flaky) {
    return (
      <div className="flex flex-col gap-0.5">
        <span className="inline-flex items-center gap-1.5">
          <Pill tone="warn" title="Intermittent pass/fail on the same SHA: re-runs won't fix it — investigate the race or fixture.">Flaky</Pill>
          <span className="text-[11px] tabular-nums" style={{ color: 'var(--status-broken)' }}>{Math.round(flaky.failure_rate_pct)}% flake</span>
        </span>
        {flaky.likely_cause && <span className="text-[10.5px] text-[var(--color-text-muted)] leading-tight">{flaky.likely_cause}</span>}
      </div>
    )
  }
  if (test.fail_count >= 2) return <Pill tone="bad" title={NOT_A_FLAKE}>Repeat failure</Pill>
  return <span className="text-[var(--color-text-faint)]">—</span>
}

function RowAction({
  children, label, title, disabled, onClick,
}: { children: React.ReactNode; label: string; title: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-secondary)] hover:border-[var(--color-border-light)] hover:text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
    >
      {children}
    </button>
  )
}

// ── Suspects panel (Epic 8 US-8.2) ────────────────────────────────────────
// "Who / what changed" — the re-added bisect surface. Ranks the commits
// landed since the last green run as SUSPECTS (never culprits) for the
// headline failing test, with the overlapping-files rationale expandable and
// commit deep links. Honest empty state when there's no commit range.
export function SuspectsPanel({
  runId, fingerprint, panelId,
}: {
  runId: string | null
  fingerprint?: string | null
  panelId?: string
}) {
  const { ranking, suspects, available, caveat, hasLocationSignal, isLoading } =
    useSuspects(runId, { fingerprint: fingerprint ?? undefined })

  // No failed run to attribute against — don't render a dead card.
  if (!runId) return null

  const source = ranking?.source
  return (
    <div id={panelId} data-testid="suspects-panel">
      <CardShell
        title="Who / what changed"
        rightSlot={
          <>
            <GitCommit className="h-3.5 w-3.5" />
            <Pill tone="neutral">Suspects</Pill>
          </>
        }
      >
        <div className="px-4 py-3.5">
          {isLoading ? (
            <p className="text-[12.5px] text-[var(--color-text-muted)] m-0">
              Resolving the commit range…
            </p>
          ) : !available || suspects.length === 0 ? (
            <div className="text-[12.5px] text-[var(--color-text-muted)]">
              <p className="m-0">
                No commit range available for this failure yet
                {source === 'unavailable'
                  ? ' — no GitHub connector is configured and no commit list was supplied on ingest.'
                  : '.'}
              </p>
              <p className="mt-1.5 mb-0 text-[11.5px] text-[var(--color-text-faint)]">
                Configure the GitHub integration, or have your CI push the commit
                range on ingest, to see ranked suspects here.
              </p>
            </div>
          ) : (
            <>
              {/* Suspects-not-culprits + monorepo caveat, surfaced verbatim. */}
              <p className="m-0 mb-2.5 text-[11.5px] text-[var(--color-text-faint)] flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                <span>{caveat}</span>
              </p>
              {!hasLocationSignal && (
                <p className="m-0 mb-2.5 text-[11.5px] text-[var(--color-text-faint)]">
                  No source location was derivable for this test — commits are
                  ordered by recency, not path overlap. Read as a range to inspect.
                </p>
              )}
              <ul className="list-none m-0 p-0 flex flex-col gap-2">
                {suspects.map((s, i) => (
                  <li
                    key={s.sha}
                    className="rounded-md border"
                    style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
                  >
                    <div className="flex items-start justify-between gap-2 px-3 py-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] tabular-nums text-[var(--color-text-faint)]">#{i + 1}</span>
                          {s.commit_url ? (
                            <a
                              href={s.commit_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono text-[12.5px] font-medium text-[var(--color-accent)] hover:underline"
                            >
                              {(s.sha || '').slice(0, 8)}
                            </a>
                          ) : (
                            <span className="font-mono text-[12.5px] font-medium text-[var(--color-text)]">
                              {(s.sha || '').slice(0, 8)}
                            </span>
                          )}
                          <span className="text-[12px] text-[var(--color-text-muted)] truncate">
                            {s.author ?? 'unknown author'}
                          </span>
                        </div>
                        <div className="text-[12px] text-[var(--color-text-secondary)] truncate mt-0.5">
                          {s.message || '(no message)'}
                        </div>
                      </div>
                      <span
                        className="shrink-0 tabular-nums text-[12px] font-semibold px-2 py-0.5 rounded"
                        title="Suspect score (0–100): path overlap dominates, recency breaks ties"
                        style={{ background: 'var(--color-bg-card)', color: 'var(--color-text)' }}
                      >
                        {s.score.toFixed(0)}
                      </span>
                    </div>
                    {/* Every ranking is inspectable — which files overlapped. */}
                    <details className="px-3 pb-2">
                      <summary className="text-[11.5px] text-[var(--color-text-muted)] cursor-pointer select-none">
                        Why this suspect
                      </summary>
                      <div className="mt-1.5 text-[11.5px] text-[var(--color-text-faint)] flex flex-col gap-1">
                        <div>
                          Recency rank {s.rationale.recency_rank} · overlap score{' '}
                          {s.rationale.overlap_score.toFixed(2)} ·{' '}
                          {s.rationale.changed_file_count} file
                          {s.rationale.changed_file_count === 1 ? '' : 's'} changed
                          {s.rationale.author_touched_module_before
                            ? ' · author touched this module elsewhere in the range'
                            : ''}
                        </div>
                        {s.rationale.overlapping_files.length > 0 ? (
                          <div>
                            <span className="text-[var(--color-text-muted)]">Overlapping files:</span>
                            <ul className="list-none m-0 mt-1 p-0 flex flex-col gap-0.5">
                              {s.rationale.overlapping_files.map(f => (
                                <li key={f} className="font-mono text-[11px] truncate">{f}</li>
                              ))}
                            </ul>
                          </div>
                        ) : (
                          <div className="italic">No file overlapped the failing test's path.</div>
                        )}
                      </div>
                    </details>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </CardShell>
    </div>
  )
}

// ── Failure kind (US-9.2) ─────────────────────────────────────────────────
// The triad — product / test_code / infrastructure (/ unknown) — is the
// AI-derived triage axis from backend/app/services/failure_kind.py. It is
// a classification, not ground truth, so every surface carries the
// "AI-classified" provenance copy.

// The badge (and its AI-4 evidence-popover sibling) live in the shared
// component so RunDetail rows can carry the same treatment. Re-exported
// here for backward compatibility with existing imports.
export { FailureKindBadge }

/** Chip row that filters the failure surfaces below by AI-classified kind.
 *  Counts come from the backend's by-kind aggregation (which applies the
 *  BROKEN-status nudge); zero-count kinds stay visible so the triad reads
 *  as a stable mental model across windows. */
function KindFilterChips({
  byKind, value, onChange,
}: {
  byKind: FailureKindCount[]
  value: FailureKind | 'all'
  onChange: (v: FailureKind | 'all') => void
}) {
  const counts = new Map(byKind.map(k => [k.kind, k.count]))
  const total = byKind.reduce((s, k) => s + k.count, 0)
  const chips: { id: FailureKind | 'all'; label: string; count: number; color?: string }[] = [
    { id: 'all', label: 'All', count: total },
    ...FAILURE_KIND_DEFS.map(d => ({
      id: d.id, label: d.label, count: counts.get(d.id) ?? 0, color: d.color,
    })),
  ]
  return (
    <div
      role="group"
      aria-label="Filter by failure kind (AI-classified)"
      className="flex items-center gap-2 flex-wrap rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '10px 16px', marginBottom: 14 }}
    >
      <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Failure kind
      </span>
      <span
        className="text-[10px] text-[var(--color-text-faint)]"
        title="Kinds are derived by the failure analyzer from its category verdict and the failure shape — corrections feed the training loop."
      >
        AI-classified
      </span>
      <span aria-hidden className="w-px h-4 mx-1" style={{ background: 'var(--color-border)' }} />
      {chips.map(chip => {
        const active = value === chip.id
        return (
          <button
            key={chip.id}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(chip.id)}
            className={clsx(
              'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[12px] font-medium border transition-colors',
              active ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
            style={{
              background: active
                ? (chip.color ? `color-mix(in srgb, ${chip.color} 14%, transparent)` : 'var(--color-bg-hover)')
                : 'transparent',
              borderColor: active
                ? (chip.color ? `color-mix(in srgb, ${chip.color} 40%, transparent)` : 'var(--color-border-light)')
                : 'var(--color-border)',
            }}
          >
            {chip.color && (
              <span aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: chip.color }} />
            )}
            {chip.label}
            <span className="tabular-nums text-[11px]" style={{ color: active && chip.color ? chip.color : 'var(--color-text-faint)' }}>
              {chip.count}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ── Failure category card ─────────────────────────────────────────────────
// ``kind`` maps each display bucket onto the failure-kind triad so the rows
// can carry the same color-coded badge as the filter chips. The matchers
// cover BOTH the backend FailureCategory enum vocabulary (PRODUCT_BUG /
// TEST_DATA / AUTOMATION_DEFECT / FLAKY / INFRASTRUCTURE / UNKNOWN) and the
// looser historical strings older payloads carried — previously the enum
// values (except INFRASTRUCTURE/UNKNOWN) all fell through to the Unknown
// bucket, which made the card disagree with the kind chips.
const CANONICAL_CATEGORIES: { id: string; label: string; color: string; matcher: RegExp; kind: FailureKind }[] = [
  { id: 'unknown',   label: 'Unknown',            color: 'var(--cat-unknown)',    matcher: /unknown|unclassified/i, kind: 'unknown' },
  { id: 'assertion', label: 'Assertion / product', color: 'var(--cat-assertion)',  matcher: /assert|product/i,       kind: 'product' },
  { id: 'test_code', label: 'Test code / data',    color: 'var(--kind-test-code)', matcher: /automation|test.?data|test.?code|script|fixture|flaky|intermittent/i, kind: 'test_code' },
  { id: 'timeout',   label: 'Timeout',             color: 'var(--cat-timeout)',    matcher: /timeout|timed.?out/i,   kind: 'infrastructure' },
  { id: 'network',   label: 'Network / 5xx',       color: 'var(--cat-network)',    matcher: /network|http|5\d\d/i,   kind: 'infrastructure' },
  { id: 'infra',     label: 'Infra / runner',      color: 'var(--cat-infra)',      matcher: /infra|runner|ci|env/i,  kind: 'infrastructure' },
]

/**
 * The category rows' columns: label + badge, bar, "n of N", percent. The label
 * column is never narrower than its min-content — the widest WHOLE name (or
 * badge: the badge wraps under the name before a name is cut) — and never
 * wider than name + badge on one line; the bar gives up the width.
 */
const CATEGORY_GRID = {
  gridTemplateColumns: 'minmax(min-content, max-content) minmax(48px, 1fr) 56px 56px',
  columnGap: 12,
} as const
/** The width right of the label column: the bar's floor, the two 56 px figures and the three 12 px gaps. */
const CATEGORY_FIXED_WIDTH = 48 + 56 + 56 + 3 * 12
/** A card too narrow for even that: one column, each row stacked (name line, then bar and figures). */
const CATEGORY_STACKED_GRID = { gridTemplateColumns: 'minmax(0, 1fr)' } as const

function FailureCategoryCard({ categories, totalFailures, uncategorizedPct, onCorrect, onClassify, kindFilter = 'all' }: {
  categories: FailureCategoryItem[]
  totalFailures: number
  uncategorizedPct: number
  /** Opens the correct-classification dialog for the top failing test. */
  onCorrect: () => void
  /** Opens the bulk "classify uncategorised failures" dialog. */
  onClassify?: () => void
  /** Active failure-kind filter — 'all' shows everything (US-9.2). */
  kindFilter?: FailureKind | 'all'
}) {
  const buckets = new Map<string, number>()
  for (const c of categories) {
    const cat = CANONICAL_CATEGORIES.find(x => x.matcher.test(c.category))
    const id = cat?.id ?? 'unknown'
    buckets.set(id, (buckets.get(id) ?? 0) + c.count)
  }
  const total = Array.from(buckets.values()).reduce((s, n) => s + n, 0)
    || (kindFilter === 'all' ? totalFailures : 0)

  // Wave 3 R2-B F-17: a card narrower than the widest whole name + the three
  // other columns STACKS each row (name line above bar and figures) instead of
  // cutting the names or drawing them over the bars. Measured in the reader's
  // font: the name and badge spans keep their natural width in both layouts,
  // so the choice never flips back and forth. Unmeasured (0): the columns.
  const [measureGrid, gridWidth] = useContainerWidth<HTMLDivElement>()
  const gridRef = useRef<HTMLDivElement | null>(null)
  const attachGrid = useCallback((node: HTMLDivElement | null) => {
    gridRef.current = node
    measureGrid(node)
  }, [measureGrid])
  const [labelMin, setLabelMin] = useState(0)
  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    let widest = 0
    for (const part of grid.querySelectorAll<HTMLElement>('[data-category-name], [data-category-badge]')) {
      widest = Math.max(widest, part.getBoundingClientRect().width)
    }
    setLabelMin(widest)
  }, [gridWidth])
  const stacked = gridWidth > 0 && labelMin > 0 && gridWidth < labelMin + CATEGORY_FIXED_WIDTH

  return (
    <CardShell
      title="Failure category distribution"
      rightSlot={
        <div className="flex items-center gap-2">
          {kindFilter !== 'all' && <FailureKindBadge kind={kindFilter} compact />}
          <span>{total} failure{total === 1 ? '' : 's'}</span>
        </div>
      }
    >
      <div className="px-4 py-3.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
          Categories computed by the failure-analyzer{kindFilter !== 'all' && (
            <> — filtered to AI-classified <em>{kindDef(kindFilter).label.toLowerCase()}</em> failures</>
          )}. Empty buckets are kept visible — the page
          tells you what didn't happen, not just what did.
        </p>

        {uncategorizedPct >= 50 && (
          <div
            className="flex items-start gap-2 rounded-md p-2.5 mb-3 text-[12px]"
            style={{ background: 'color-mix(in srgb, var(--status-broken) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--status-broken) 28%, transparent)', color: 'var(--color-text-secondary)' }}
          >
            <AlertTriangle className="h-3.5 w-3.5 flex-none mt-0.5" style={{ color: 'var(--status-broken)' }} />
            <span>
              Classifier confidence low — {Math.round(uncategorizedPct)}% of failures are sitting in <em>Unknown</em>.{' '}
              <button
                type="button"
                onClick={onCorrect}
                className="text-[var(--color-accent)] hover:underline"
              >
                Correct the classification →
              </button>
              {/* The verdict card's "Classify" issue row, moved beside its own evidence. */}
              {onClassify && (
                <>
                  {' · '}
                  <button
                    type="button"
                    onClick={onClassify}
                    className="text-[var(--color-accent)] hover:underline"
                  >
                    Classify them so owners can be auto-routed →
                  </button>
                </>
              )}
            </span>
          </div>
        )}

        {/* ONE grid for the six rows (each row a subgrid of it), so the label
            column is as wide as the WIDEST label + badge in the reader's own
            font — never a reserve tuned to one font. A fixed 220 px fitted
            every label in Segoe UI and cut "Assertion / prod…", "Network …"
            and "Infra / ru…" in DejaVu Sans (the Linux CI renderer, Wave 3
            BEFORE note 3). The bar gives up the width; it never drops below
            48 px. A name is never cut: the badge wraps under it first, and a
            card too narrow for the widest name and the other columns stacks
            each row instead (R2-B F-17). */}
        <div
          ref={attachGrid}
          data-category-grid=""
          data-category-layout={stacked ? 'stacked' : 'columns'}
          className="grid"
          style={stacked ? CATEGORY_STACKED_GRID : CATEGORY_GRID}
        >
          {CANONICAL_CATEGORIES.map((c, i, arr) => {
            const count = buckets.get(c.id) ?? 0
            const pct = total > 0 ? Math.round((count / total) * 100) : 0
            const empty = count === 0
            return (
              <div
                key={c.id}
                role="row"
                aria-label={`${c.label}: ${pct}% (${count} of ${total})`}
                className={clsx('grid items-center gap-3', i < arr.length - 1 && 'pb-2 mb-2')}
                style={{
                  gridTemplateColumns: stacked ? 'minmax(0, 1fr) 56px 56px' : 'subgrid',
                  gridColumn: '1 / -1',
                  rowGap: stacked ? 6 : undefined,
                  borderBottom: i < arr.length - 1 ? '1px dashed var(--color-border)' : '0',
                  paddingTop: 8,
                }}
              >
                <div
                  className="flex items-center min-w-0 text-[12.5px] text-[var(--color-text)]"
                  style={{ flexWrap: 'wrap', columnGap: 8, rowGap: 4, gridColumn: stacked ? '1 / -1' : undefined }}
                >
                  <span data-category-name="" className="inline-flex items-center gap-2 flex-none whitespace-nowrap">
                    <span aria-hidden className="inline-block w-2 h-2 rounded-sm flex-none" style={{ background: c.color }} />
                    {c.label}
                  </span>
                  <span data-category-badge="" className="inline-flex flex-none">
                    <FailureKindBadge kind={c.kind} compact />
                  </span>
                </div>
                <div className="relative h-3.5 rounded-sm overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
                  <i
                    className="block h-full"
                    style={{
                      width: `${pct}%`,
                      background: c.id === 'unknown' && pct > 0
                        ? 'repeating-linear-gradient(45deg, #94a3b8, #94a3b8 4px, #475569 4px, #475569 8px)'
                        : c.color,
                    }}
                  />
                </div>
                <div className="text-[12px] tabular-nums text-right text-[var(--color-text-secondary)]">
                  {count > 0 ? `${count} of ${total}` : '0'}
                </div>
                <div
                  className={clsx('text-[13px] font-semibold tabular-nums text-right', empty && 'text-[var(--color-text-muted)]')}
                  style={empty ? undefined : { color: c.id === 'unknown' ? 'var(--status-broken)' : 'var(--color-text)' }}
                >
                  {empty ? '—' : `${pct}%`}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </CardShell>
  )
}

// ── Comparison strip ──────────────────────────────────────────────────────
// Renders the current-window vs prior-window deltas after the user clicks
// "Compare to previous window" in verdictCtas. Designed to be cheap: three
// metric rows (failures, total runs, pass rate), no charts. The label on
// each delta is colourised the way a triager would expect — fewer failures
// = green, more failures = red; pass rate inverted.
type ComparisonStats = { failed: number; total: number; passRate: number; days: number }

function ComparisonStrip({
  current, prior, windowDays, suiteName,
}: { current: ComparisonStats; prior: ComparisonStats; windowDays: number; suiteName?: string | null }) {
  const failedDelta = current.failed - prior.failed
  const totalDelta  = current.total  - prior.total
  const rateDelta   = current.passRate - prior.passRate

  // ``deltaColour`` returns CSS values rather than Tailwind classes so the
  // direction-vs-good logic stays explicit at each call site — a higher
  // failure count is bad, a higher pass rate is good.
  const RED   = 'var(--status-failed)'
  const GREEN = 'var(--status-passed)'
  const NEUTRAL = 'var(--color-text-muted)'
  const colourForFailureDelta = (delta: number): string =>
    delta === 0 ? NEUTRAL : (delta > 0 ? RED : GREEN)
  const colourForRateDelta = (delta: number): string =>
    Math.abs(delta) < 0.01 ? NEUTRAL : (delta > 0 ? GREEN : RED)

  // Days of actual data behind each half. The backend returns one
  // trend point per day-with-runs, so a sparse project (only 2 days
  // of activity in a 14-day window) produces ``current.days = 1``
  // and ``prior.days = 1``. The card title used to say
  // "last {windowDays}d vs prior {windowDays}d" unconditionally,
  // which misled users into reading the numbers as 14-day totals
  // when they were actually single-day totals. Now we show the
  // requested window when both halves cover it, and the actual
  // data spans otherwise.
  const actualLabel = (current.days >= windowDays && prior.days >= windowDays)
    ? `last ${windowDays}d vs prior ${windowDays}d`
    : `last ${current.days}d (of ${windowDays}d) vs prior ${prior.days}d`

  return (
    <CardShell
      title="Compare to previous window"
      rightSlot={<span>{actualLabel}</span>}
    >
      <div className="px-4 py-3.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
          {suiteName ? (
            <>
              Filtered to suite <code className="font-mono text-[11px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1 py-px rounded-sm">{suiteName}</code>.
              {' '}Aggregated from daily trends; prior window = the {prior.days} day{prior.days === 1 ? '' : 's'} immediately before this window.
            </>
          ) : (
            <>
              Aggregated from daily trends. Prior window = the {prior.days} day{prior.days === 1 ? '' : 's'} immediately before this window.
            </>
          )}
        </p>
        <div className="grid gap-2.5" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          <ComparisonCell
            label="Failures"
            current={current.failed}
            prior={prior.failed}
            delta={failedDelta}
            deltaColor={colourForFailureDelta(failedDelta)}
            formatter={(n) => Intl.NumberFormat().format(n)}
          />
          <ComparisonCell
            label="Test executions"
            current={current.total}
            prior={prior.total}
            delta={totalDelta}
            deltaColor={NEUTRAL}
            formatter={(n) => Intl.NumberFormat().format(n)}
          />
          <ComparisonCell
            label="Pass rate"
            current={current.passRate}
            prior={prior.passRate}
            delta={rateDelta}
            deltaColor={colourForRateDelta(rateDelta)}
            formatter={(n) => `${n.toFixed(1)}%`}
          />
        </div>
      </div>
    </CardShell>
  )
}

function ComparisonCell({
  label, current, prior, delta, deltaColor, formatter,
}: {
  label: string
  current: number
  prior: number
  delta: number
  deltaColor: string
  formatter: (n: number) => string
}) {
  const arrow = delta === 0 ? '—' : (delta > 0 ? '↑' : '↓')
  return (
    <div
      className="rounded-md border"
      style={{ padding: '10px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[11px] text-[var(--color-text-muted)] uppercase tracking-wider">{label}</div>
      <div className="mt-1 text-[18px] font-semibold text-[var(--color-text)]">{formatter(current)}</div>
      <div className="mt-0.5 text-[11.5px]" style={{ color: deltaColor }}>
        {arrow} {formatter(Math.abs(delta))} <span className="text-[var(--color-text-muted)]">vs prior {formatter(prior)}</span>
      </div>
    </div>
  )
}


// ── Failure timeline ──────────────────────────────────────────────────────

/** The timeline draws at most this many days. */
export const TIMELINE_MAX_CELLS = 30

/**
 * The failure timeline's cells, for a window of `days` ending on `todayIso`
 * (at most `TIMELINE_MAX_CELLS`, oldest first): `fail` when anything failed or
 * broke — its `severity` (a deeper red for a worse day) is the
 * failed-plus-broken share of all four statuses — `pass` when something
 * passed, otherwise `none`, "not run": no result, or skips only.
 *
 * That is the run strip's rule and word for the same day on the same card
 * (`runStripCells`). A pass day names what it counts: "5 passed, 3 skipped",
 * never "8 passing", and a skips-only day is not a green "4 passing" (R1 F6).
 *
 * Broken counts, in the total and as failure-like: it used to be left out of
 * both, so a broken-only day was drawn as "0 runs" and broken results thinned
 * no day's severity (the owner's rule: a chart does not silently drop a
 * status, OD-7 / OD-16).
 */
export function failureTimelineCells(trend: TrendPoint[], days: number, todayIso: string): DayStripCell[] {
  const byDate = new Map<string, { passed: number; failed: number; skipped: number; broken: number }>()
  for (const p of trend) {
    byDate.set(p.date.slice(0, 10), { passed: p.passed, failed: p.failed, skipped: p.skipped, broken: p.broken ?? 0 })
  }
  return dayWindow(Math.min(days, TIMELINE_MAX_CELLS), todayIso).map<DayStripCell>((iso) => {
    const r = byDate.get(iso)
    const total = r ? r.passed + r.failed + r.skipped + r.broken : 0
    if (!r || total === 0) return { key: iso, label: `${iso} · ${NOT_RUN}`, tone: 'none' }
    const failing = r.failed + r.broken
    if (failing > 0) {
      const what = r.broken === 0 ? 'failed' : r.failed === 0 ? 'broken' : `failed or broken (${r.broken} broken)`
      return { key: iso, label: `${iso} · ${failing} of ${total} ${what}`, tone: 'fail', severity: failing / total }
    }
    if (r.passed === 0) return { key: iso, label: `${iso} · ${NOT_RUN} (${r.skipped} skipped)`, tone: 'none' }
    const skipped = r.skipped > 0 ? `, ${r.skipped} skipped` : ''
    return { key: iso, label: `${iso} · ${r.passed} passed${skipped}`, tone: 'pass' }
  })
}

export function failureTimelineLabel(cells: readonly DayStripCell[]): string {
  const failures = countTones(cells).fail
  return `Failure timeline: ${failures} day${failures === 1 ? '' : 's'} with failures over the last ${cells.length} days.`
}

/** The failure timeline, inside its Disclosure below the primary content (one cell a day, at most 30). */
function FailureTimeline({ trend, days }: { trend: TrendPoint[]; days: number }) {
  const cells = useMemo(() => failureTimelineCells(trend, days, utcDayIso()), [trend, days])

  return (
    <div data-failure-timeline="">
      <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
        Repeat-failure events vs total runs in this window. Each cell = 1 day.
      </p>
      <DayStrip cells={cells} mode="status" label={failureTimelineLabel(cells)} title="Failure timeline" text={RUN_STRIP_TEXT} />
    </div>
  )
}

// ── Shared shell + pill ───────────────────────────────────────────────────
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

function Pill({ children, tone, title }: { children: React.ReactNode; tone: 'good' | 'warn' | 'bad' | 'neutral'; title?: string }) {
  const palette = {
    good:    { bg: 'color-mix(in srgb, var(--status-passed) 15%, transparent)',      bd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)', fg: 'var(--status-passed)' },
    warn:    { bg: 'color-mix(in srgb, var(--status-broken) 15%, transparent)',     bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)', fg: 'var(--status-broken)' },
    bad:     { bg: 'var(--status-failed-soft)', bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)', fg: 'var(--status-failed)' },
    neutral: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' },
  }[tone]
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase whitespace-nowrap"
      style={{ background: palette.bg, border: `1px solid ${palette.bd}`, color: palette.fg, letterSpacing: 'var(--tracking-wide)' }}
      title={title}
    >
      {children}
    </span>
  )
}

// ── Failure-category vocabulary ───────────────────────────────────────────
// Shared by the bulk-classify modal and the correct-classification dialog.
// Mirrors the backend ``FailureCategory`` enum minus UNKNOWN (correcting a
// verdict *to* Unknown is a no-op the training loop can't learn from).
// ── Mute-test (quarantine proposal) modal — US-2.4 ────────────────────────
// Wires the "Mute test" CTA to the existing flaky-quarantine workflow:
// submits a manual PROPOSED row via POST /api/v1/quarantine (QA_LEAD+),
// which then awaits approval on /quarantine. The reason is required — it
// lands in the proposal's rationale and the quarantine audit trail.
function MuteTestModal({
  test, flakyEntry, projectId, onClose,
}: {
  test: TopFailingItem
  flakyEntry: FlakyTestItem | null
  projectId: string
  onClose: () => void
}) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const trimmedReason = reason.trim()

  async function handleSubmit() {
    if (!trimmedReason || !test.test_fingerprint || submitting) return
    setSubmitting(true)
    try {
      const row = await flakyQuarantineService.propose({
        project_id: projectId,
        test_fingerprint: test.test_fingerprint,
        test_name: test.test_name,
        suite_name: test.suite_name ?? null,
        detection_method: 'manual',
        fail_count: test.fail_count,
        // Carry the measured intermittency when the flaky list has it —
        // gives the approving QA Lead the same context this page shows.
        ...(flakyEntry && flakyEntry.source !== 'manual'
          ? {
              flip_rate: Math.min(1, flakyEntry.failure_rate_pct / 100),
              pass_count: Math.max(0, flakyEntry.total_runs - flakyEntry.fail_count),
            }
          : {}),
        rationale: { reason: trimmedReason, source: 'failure-analysis-page' },
      })
      toast.success(
        row.status === 'PROPOSED'
          ? 'Quarantine proposal created — pending QA Lead approval on /quarantine.'
          : `Quarantine request updated — now ${row.status} (see /quarantine).`,
        { duration: 8000 },
      )
      onClose()
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to create the quarantine proposal'
      toast.error(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Mute test (propose quarantine)"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="w-full max-w-md rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] p-5 shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-[var(--color-text)] m-0">
          Mute test (propose quarantine)
        </h2>
        <p className="mt-1 text-[12.5px] text-[var(--color-text-muted)]">
          Proposes <code className="font-mono text-[11.5px]">{test.test_name}</code> for
          quarantine. A QA Lead approves or rejects the proposal on /quarantine —
          nothing is muted until then.
        </p>

        <div
          className="mt-3 rounded-md border px-3 py-2 text-[12px]"
          style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
        >
          {flakyEntry ? (
            flakyEntry.source === 'manual' ? (
              <>Already flagged flaky by a human on /my-failures · failed {test.fail_count} time{test.fail_count === 1 ? '' : 's'} in this window.</>
            ) : (
              <>Flake history: failed {flakyEntry.fail_count} of {flakyEntry.total_runs} run{flakyEntry.total_runs === 1 ? '' : 's'} ({Math.round(flakyEntry.failure_rate_pct)}% in this window).</>
            )
          ) : (
            <>No intermittency signal in this window — failed {test.fail_count} time{test.fail_count === 1 ? '' : 's'} straight. Muting a consistently-failing test hides a real regression; say why below.</>
          )}
        </div>

        <label className="block mt-3 text-[12px] font-medium text-[var(--color-text)]">
          Reason <span style={{ color: 'var(--status-failed)' }}>*</span>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            rows={3}
            placeholder="Why should this test stop gating runs? (goes to the quarantine audit trail)"
            className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
          />
        </label>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || !trimmedReason}
            title={!trimmedReason ? 'A reason is required' : undefined}
            className="text-[12.5px] font-medium rounded-md px-3 py-1.5 disabled:opacity-50"
            style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
          >
            {submitting ? 'Proposing…' : 'Propose quarantine'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── The secondary sections (tabs under the primary content) ─────────────
type SectionTab = 'groups' | 'suite' | 'scatter' | 'categories'

const SECTION_TABS: readonly TabItem<SectionTab>[] = [
  { id: 'groups', label: 'Groups' },
  { id: 'suite', label: 'By suite' },
  { id: 'scatter', label: 'Scatter' },
  { id: 'categories', label: 'Categories' },
]
const SECTION_TAB_IDS: readonly SectionTab[] = SECTION_TABS.map(t => t.id)
const DEFAULT_SECTION_TAB: SectionTab = 'groups'
const SECTION_LABEL: Record<SectionTab, string> = {
  groups: 'Failure groups',
  suite: 'Results by suite',
  scatter: 'Test scatter',
  categories: 'Failure categories',
}

/** The tab key, and the section-owned keys a tab change clears (the drill path, the rows panel). */
const TAB_KEY = 'tab'
const SECTION_URL_KEYS = ['drill', 'rows'] as const

/** A rows panel's owner (`rows=by~<owner>`, `useDrillPath`) → the tab its section lives in. */
const TAB_OF_ROWS_OWNER: Readonly<Record<string, SectionTab>> = {
  'failures-drill': 'suite',
  'scatter-project': 'scatter',
}

/**
 * The tab a link that names none belongs in: a drill path (`drill=`) is the
 * By-suite ladder's, a rows panel (`rows=by~<owner>`) its section's. A link
 * copied before the tabs existed (or a hand-made one) then opens on the
 * section it describes, not on Groups with the state invisible. Read here, not
 * through `useDrillPath`: that module is section-only (it must stay out of
 * this page's static closure, `sectionOnlyModules.test.ts`).
 */
function tabOfLink(params: URLSearchParams): SectionTab | null {
  const owner = /^by~(.+)$/.exec(params.get('rows') ?? '')?.[1]
  if (owner && TAB_OF_ROWS_OWNER[owner]) return TAB_OF_ROWS_OWNER[owner]
  return params.has('drill') ? 'suite' : null
}

// ── Page ──────────────────────────────────────────────────────────────────
export default function FailureAnalysisPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Global shared time-window preference — picking 24h here propagates
  // to /reports/summary, /live, /coverage, /trends, /runs, /overview,
  // /my-failures and vice versa (the header's `WindowPicker` writes it).
  // Snapped to this page's allowed set.
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, WINDOWS) as Window

  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames, suiteLabel } = usePageSuiteFilter()
  // P1: this page's saved views (the top-bar release, the window, the suite).
  const viewsMenu = useReportViewsMenu({
    route: '/failures',
    windowDays: days,
    windowOptions: WINDOWS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
  })
  const { options: suiteOptions } = useSuiteOptions(days)
  // P2: the catalogue's "Filter page by this" writes a suite mark to the select above.
  const suiteTarget = usePageSuiteTarget(selectedSuite, suiteOptions, setSelectedSuite)

  // ── The section tabs (?tab=) ─────────────────────────────────────────
  const [searchParams, setSearchParams] = useSearchParams()
  const [tabParam, setTabParam] = useTabParam(SECTION_TAB_IDS, DEFAULT_SECTION_TAB)
  // A link naming no tab opens the tab its drill / rows state belongs to,
  // and the URL is completed to say so (replace), so leaving that state
  // (the ladder's root link) keeps the tab.
  const linkedTab = searchParams.has(TAB_KEY) ? null : tabOfLink(searchParams)
  const tab = linkedTab ?? tabParam
  useEffect(() => {
    if (linkedTab) setTabParam(linkedTab)
  }, [linkedTab, setTabParam])
  // A tab change closes the section it leaves: its drill path and rows panel
  // are that section's state (and would otherwise pull the page back to it).
  const selectTab = useCallback((next: SectionTab) => {
    if (next === tab) return
    setSearchParams((current) => {
      const out = new URLSearchParams(current)
      for (const key of SECTION_URL_KEYS) out.delete(key)
      if (next === DEFAULT_SECTION_TAB) out.delete(TAB_KEY)
      else out.set(TAB_KEY, next)
      return out
    }, { replace: true })
  }, [tab, setSearchParams])

  // ── Compare-to-previous-window toggle ────────────────────────────────
  // The banner's "Compare to previous window" flips this on, which triggers a
  // second trend fetch covering twice the window. We split that into
  // current + prior halves to compute deltas without a bespoke backend
  // endpoint. The toggle stays page-local so a stale comparison can't
  // leak across navigations.
  const [comparing, setComparing] = useState(false)

  const { data: flakyData,    isLoading: flakyLoading    } = useFlakyTests(days, suiteFilter)
  const { data: categoryData, isLoading: categoryLoading } = useFailureCategories(days, suiteFilter)
  const { data: topData,      isLoading: topLoading      } = useTopFailing(days, suiteFilter)
  const { data: trendsData,   isLoading: trendsLoading   } = useTrendData(days, suiteFilter)
  // Double-window trend used for prior-vs-current delta computation.
  // When the user hasn't enabled comparison, this keys on ``days`` (the
  // same key as the primary fetch above), so SWR dedupes and no second
  // request is issued. When ``comparing`` is on, the hook re-keys on
  // ``days * 2`` and fetches the extended window, which we split into
  // halves to derive the prior-window stats.
  const compareDays = comparing ? days * 2 : days
  const { data: compareTrendsData, isLoading: compareLoading } = useTrendData(
    compareDays, suiteFilter,
  )
  // The most recent failing run: its suite is a banner fact, and it is the
  // run the Suspects panel attributes commits against.
  const { data: latestFailedRuns } = useRuns({ page: 1, size: 1, days, status: 'FAILED', ...(suiteFilter && { suite_name: suiteFilter }) })
  const latestFailedRun = latestFailedRuns?.items?.[0]

  const flaky      = useMemo<FlakyTestItem[]>(() => normaliseList<FlakyTestItem>(flakyData), [flakyData])
  const categories = useMemo<FailureCategoryItem[]>(() => normaliseList<FailureCategoryItem>(categoryData), [categoryData])
  const topFailing = useMemo<TopFailingItem[]>(() => normaliseList<TopFailingItem>(topData), [topData])
  const trend: TrendPoint[] = useMemo(() => trendsData?.data ?? [], [trendsData])
  const rows = useMemo(() => failingRows(topFailing, flaky), [topFailing, flaky])

  // ── Failure-kind triad (US-9.2) ──────────────────────────────────────
  // Chip filter over the AI-classified kind. The by-kind aggregation comes
  // from the backend (it applies the BROKEN-status nudge); when an older
  // cached payload lacks it, derive category-only counts client-side.
  const [kindFilter, setKindFilter] = useState<FailureKind | 'all'>('all')
  const byKind = useMemo<FailureKindCount[]>(() => {
    const raw = categoryData as { by_kind?: FailureKindCount[] } | undefined
    if (raw?.by_kind && Array.isArray(raw.by_kind)) return raw.by_kind
    const counter = new Map<FailureKind, number>()
    for (const c of categories) {
      const k = (c.kind as FailureKind | undefined) ?? failureKindOf(c.category)
      counter.set(k, (counter.get(k) ?? 0) + c.count)
    }
    return FAILURE_KIND_DEFS.map(d => ({ kind: d.id, count: counter.get(d.id) ?? 0 }))
  }, [categoryData, categories])
  // Category items narrowed to the selected kind. Scoped to the category
  // distribution card — the verdict / stability model stays computed over
  // the full window so a filter can't flip the page's headline verdict.
  const kindFilteredCategories = useMemo<FailureCategoryItem[]>(() => {
    if (kindFilter === 'all') return categories
    return categories.filter(
      c => ((c.kind as FailureKind | undefined) ?? failureKindOf(c.category)) === kindFilter,
    )
  }, [categories, kindFilter])

  // Comparison stats — only computed when ``comparing`` is true. We
  // split the double-window trend into "prior" (older half) and
  // "current" (newer half) and aggregate each. Trend points are
  // already date-sorted ascending by the backend; if the upstream
  // ordering ever changes, the sort below makes this resilient.
  const comparison = useMemo(() => {
    if (!comparing) return null
    const points = (compareTrendsData?.data ?? []).slice().sort(
      (a, b) => a.date.localeCompare(b.date),
    )
    if (points.length < 2) return null
    // Cut at the midpoint so prior == older half, current == newer half.
    // Odd counts give the extra day to the current window — feels more
    // honest when the user is looking at "is it getting worse right now".
    const mid = Math.floor(points.length / 2)
    const prior   = points.slice(0, mid)
    const current = points.slice(mid)
    const summarise = (pts: TrendPoint[]) => {
      const failed = pts.reduce((s, p) => s + (p.failed || 0), 0)
      const passed = pts.reduce((s, p) => s + (p.passed || 0), 0)
      const broken = pts.reduce((s, p) => s + (p.broken || 0), 0)
      const total  = pts.reduce((s, p) => s + (p.total ?? (p.passed + p.failed + p.skipped + p.broken)), 0)
      const denom  = passed + failed + broken
      const passRate = denom > 0 ? (passed / denom) * 100 : 0
      return { failed, total, passRate, days: pts.length }
    }
    return { prior: summarise(prior), current: summarise(current) }
  }, [comparing, compareTrendsData])

  const model = useMemo(
    () => computeStabilityModel({ flaky, categories, topFailing, trend }),
    [flaky, categories, topFailing, trend],
  )
  const verdict = pickVerdict(model)

  // ── US-2.4 action state: mute-to-quarantine, Jira, suspects (per row) ──
  // + the classifier correction, which targets the headline failing test.
  const [muteFor, setMuteFor] = useState<FailingRow | null>(null)
  const [jiraFor, setJiraFor] = useState<FailingRow | null>(null)
  const [suspectsFor, setSuspectsFor] = useState<FailingRow | null>(null)
  const [correctionOpen, setCorrectionOpen] = useState(false)
  const { metadata: jiraMeta } = useJiraDefectMetadata(project?.id ?? null)

  const actionTarget = model.topFailingTest
  const rowActions = useMemo<RowActionHandlers>(() => ({
    onMute: (row) => setMuteFor(row),
    muteDisabledReason: (row) => !project?.id
      ? 'Pick a specific project to propose a quarantine.'
      : !row.test.test_fingerprint
        ? 'Test identity (fingerprint) not available yet — cannot propose a quarantine.'
        : null,
    onCreateJira: (row) => setJiraFor(row),
    // Disabled when there's no identity/project, or when the metadata probe
    // says BOTH delivery paths are dead (Jira gated/unconfigured AND no
    // webhook receiver). While metadata is still loading the button stays
    // enabled — the dialog itself gates submission.
    createJiraDisabledReason: (row) => !project?.id
      ? 'Pick a specific project to create a Jira issue.'
      : !row.test.test_fingerprint
        ? 'Test identity (fingerprint) not available yet — cannot file a defect.'
        : jiraMeta && !jiraMeta.available && !jiraMeta.webhook_available
          ? jiraUnavailableCopy(jiraMeta.reason)
          : null,
    onShowSuspects: (row) => setSuspectsFor(row),
    suspectsDisabledReason: latestFailedRun?.id ? null : 'No failed run in this window to attribute commits against.',
  }), [project?.id, jiraMeta, latestFailedRun?.id])

  const openCorrection = useCallback(() => {
    if (!project?.id) {
      toast.error('Pick a specific project to correct a classification.')
      return
    }
    if (!actionTarget?.test_fingerprint) {
      toast('No per-test identity available yet — corrections need a failing test with a fingerprint.', { icon: '🏷️' })
      return
    }
    setCorrectionOpen(true)
  }, [project?.id, actionTarget])

  const isLoading = flakyLoading || categoryLoading || topLoading || trendsLoading

  // Real arrival time of this view's payload (the banner's "Updated"). This
  // age used to be the literal '4h ago' for every project, however fresh the data was.
  const fetchedAt = useDataFreshness(isLoading ? undefined : model)

  // ── Overflow handlers ────────────────────────────────────────────────────
  const [notifyingOwner, setNotifyingOwner] = useState(false)

  async function handleNotifyOwner() {
    const top = model.topFailingTest
    if (!top) return
    if (!project?.id) {
      toast.error('Pick a specific project to notify the owner.')
      return
    }
    if (notifyingOwner) return
    setNotifyingOwner(true)
    const loadingId = toast.loading(`Notifying suite owner for "${top.test_name}"…`)
    try {
      type Resp = {
        queued: boolean
        sent_to: string | null
        owner_name: string | null
        suite_name: string | null
        is_fallback_owner: boolean
        reason: string | null
      }
      const resp = await postData<Resp>('/api/v1/analytics/notify-owner', {
        project_id: project.id,
        test_name: top.test_name,
        days,
        fail_count: top.fail_count,
      })
      toast.dismiss(loadingId)
      if (resp.queued) {
        toast.success(
          `Notified ${resp.owner_name ?? resp.sent_to}${resp.is_fallback_owner ? ' (project manager — no explicit suite owner)' : ''}`,
          { icon: '✉️', duration: 6000 },
        )
      } else {
        toast(resp.reason ?? 'Could not notify the owner.', { icon: '⚠️', duration: 8000 })
      }
    } catch (err: unknown) {
      toast.dismiss(loadingId)
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to notify owner'
      toast.error(detail)
    } finally {
      setNotifyingOwner(false)
    }
  }

  // Classify modal — opened from ⋯ and from the category card's low-confidence
  // note. Lets the user bulk-assign a category (Flaky / Product Bug /
  // Infrastructure / Test Data / Automation Defect) to every uncategorised
  // failure in the current project + window. The selection persists via the
  // ``/analytics/classify-uncategorized`` endpoint.
  const [classifyOpen, setClassifyOpen] = useState(false)
  const [classifying, setClassifying] = useState(false)

  async function handleClassify(category: 'FLAKY' | 'PRODUCT_BUG' | 'INFRASTRUCTURE' | 'TEST_DATA' | 'AUTOMATION_DEFECT') {
    if (!project?.id) {
      toast.error('Pick a specific project to classify failures.')
      return
    }
    // A bulk WRITE must never be broader than what is on screen. The endpoint
    // takes one suite; with several selected (VIZ-303) sending none would tag
    // every suite's failures, so refuse instead. The suite is the one the
    // confirmation text names (`suiteLabel`, from the same `suiteNames`) —
    // not the settled data scope, which trails a click by 250 ms.
    const writeSuite = bulkWriteSuite(suiteNames)
    if (writeSuite.kind === 'several') {
      toast.error('Pick a single suite to classify failures.')
      return
    }
    if (classifying) return
    setClassifying(true)
    try {
      type Resp = { updated: number; category: string }
      const resp = await postData<Resp>('/api/v1/analytics/classify-uncategorized', {
        project_id: project.id,
        category,
        days,
        ...(writeSuite.kind === 'one' ? { suite_name: writeSuite.name } : {}),
      })
      toast.success(
        resp.updated > 0
          ? `Tagged ${resp.updated} failure${resp.updated === 1 ? '' : 's'} as ${category.replace('_', ' ').toLowerCase()}.`
          : 'No uncategorised failures in this window.',
      )
      setClassifyOpen(false)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to classify failures'
      toast.error(detail)
    } finally {
      setClassifying(false)
    }
  }

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Search className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view failure analysis."
      />
    )
  }

  if (isLoading && trend.length === 0 && model.totalRuns === 0) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }

  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  const theme = VERDICT_THEME[verdict]

  // The banner's headline after the verdict word.
  const summaryNode: React.ReactNode = (() => {
    if (verdict === 'STABLE')         return <>0 failures in {days} days</>
    if (verdict === 'PENDING')        return <>awaiting executions</>
    if (verdict === 'REPEAT_FAILURE') {
      const top = model.topFailingTest
      return top
        ? <><code className="font-mono">{top.test_name}</code> broken in {top.fail_count} of {top.fail_count} runs</>
        : <>{model.repeatFailures.length} test{model.repeatFailures.length === 1 ? '' : 's'} failing repeatedly</>
    }
    if (verdict === 'FLAKY') return <>{model.flakyCount} test{model.flakyCount === 1 ? '' : 's'} intermittent</>
    if (verdict === 'FIRST_TIME') {
      const top = model.topFailingTest
      return top
        ? <><code className="font-mono">{top.test_name}</code> failed for the first time</>
        : <>new failure in this window</>
    }
    return <>recovering from prior failures</>
  })()

  const passRatePct = model.totalRuns > 0 ? (model.passedRuns / model.totalRuns) * 100 : null
  const latestSuite = latestFailedRun && (latestFailedRun.primary_suite_name || latestFailedRun.suite_names?.length)
    ? <SuiteBadge inline primary={latestFailedRun.primary_suite_name} all={latestFailedRun.suite_names} />
    : null
  // The banner is ONE line at 1440 px (§2): the facts are short, and the
  // headline after the verdict word is cut (whole text in its tooltip).
  const bannerFacts: BannerFact[] = [
    { label: 'Stability', value: verdict === 'PENDING' ? '—' : `${model.composite}/100` },
    { label: 'Pass rate', value: passRatePct === null ? '—' : `${passRatePct.toFixed(1)}%` },
    ...(latestSuite ? [{ label: 'Latest failing suite', value: latestSuite }] : []),
    { label: 'Updated', value: refreshedAt },
  ]

  const overflow: OverflowItem[] = [
    {
      label: 'Export CSV',
      icon: <Download className="h-3.5 w-3.5" aria-hidden="true" />,
      onClick: () => handleExportCsv({ topFailing, flaky, categories, project, days, suiteFilter: suiteLabel || null }),
    },
    ...(model.topFailingTest
      ? [{
          label: 'Notify suite owner',
          icon: <Mail className="h-3.5 w-3.5" aria-hidden="true" />,
          onClick: () => { void handleNotifyOwner() },
          disabled: notifyingOwner,
        }]
      : []),
    // The verdict card's "N% of failures aren't categorised yet" issue row.
    ...(model.uncategorizedPct >= 50
      ? [{
          label: 'Classify uncategorised failures',
          icon: <Tags className="h-3.5 w-3.5" aria-hidden="true" />,
          onClick: () => setClassifyOpen(true),
        }]
      : []),
  ]

  return (
    <PageShell className="space-y-4">
      <PageHeader
        compact
        title="Failure Analysis"
        helpTopic={HELP_TOPIC}
        actions={
          <>
            {/* The page's toolbar (window + suite) shares the header row: with a banner AND a KPI strip above the
                table, a row of its own put the table past the 300 px fold budget. */}
            <WindowPicker options={WINDOWS} />
            <SuiteFilterSelect
              value={selectedSuite}
              onChange={setSelectedSuite}
              options={suiteOptions}
              allLabel="All suites"
            />
            {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
            <Link
              to={`/runs?days=${days}`}
              title="Open the triage queue filtered to this window"
              className="btn-primary inline-flex items-center gap-1 !px-3 !py-1.5 text-[13px]"
            >
              Open triage queue <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </>
        }
        overflow={overflow}
      />

      {/* "Failure verdict" stays the page's landmark (the shell and rollout specs wait for it). */}
      <section aria-label="Failure verdict" aria-live="polite">
        <StatusBanner
          state={theme.banner}
          title={
            <span data-verdict={verdict} title={theme.meaning} className="inline-flex max-w-full min-w-0 items-baseline">
              <span className="whitespace-nowrap">{theme.label}</span>{' '}
              <span aria-hidden="true" className="mx-1.5 text-[var(--color-text-muted)]">·</span>{' '}
              <span className="max-w-[26ch] truncate font-normal">{summaryNode}</span>
            </span>
          }
          facts={bannerFacts}
          action={{ label: comparing ? 'Hide comparison' : 'Compare to previous window', onClick: () => setComparing(c => !c) }}
        />
      </section>

      {/* Values only: a tile with a meta line is ~99 px (the strip's target is ~72), and the table must start
          within the fold budget below a banner AND this strip. The window's passed / failed split is the
          banner's pass rate; the uncategorised share's counts are in the Categories tab. */}
      <section aria-label="Failure metrics">
        <KpiStrip>
          {[
            <MetricCard key="repeat" compact icon={null} title="Repeat failures" metric={{ value: model.repeatFailures.length }} />,
            <MetricCard key="flaky" compact icon={null} title="Flaky tests" metric={{ value: model.flakyCount }} />,
            <MetricCard
              key="uncategorized"
              compact
              icon={null}
              title="Uncategorized"
              metric={{ value: model.totalCategorised === 0 ? '—' : `${Math.round(model.uncategorizedPct)}%` }}
            />,
            <MetricCard key="executions" compact icon={null} title="Total executions" metric={{ value: model.totalRuns }} />,
          ]}
        </KpiStrip>
      </section>

      <section data-primary="" aria-label="Top failing tests">
        <TopFailingTable
          rows={rows}
          trend={trend}
          totalRuns={model.totalRuns}
          failedExecutions={model.failedRuns}
          actions={rowActions}
        />
      </section>

      {comparing && (
        comparison ? (
          <ComparisonStrip
            current={comparison.current}
            prior={comparison.prior}
            windowDays={days}
            suiteName={suiteLabel || null}
          />
        ) : (
          <CardShell title="Compare to previous window" rightSlot={<span>last {days}d vs prior {days}d</span>}>
            <div className="px-4 py-3.5 text-[12.5px] text-[var(--color-text-muted)]">
              {compareLoading
                ? 'Loading prior-window data…'
                : 'Not enough trend data to compare against the prior window yet.'}
            </div>
          </CardShell>
        )
      )}

      {/* The catalogue sections and the categories, one tab each. Only the open tab mounts (and asks for) its section. */}
      <PageSuiteTargetContext.Provider value={suiteTarget}>
        <div data-failures-sections="">
          <Tabs items={SECTION_TABS} value={tab} onChange={selectTab} ariaLabel="Failure analysis sections" />
          <div role="tabpanel" aria-label={SECTION_LABEL[tab]} data-tab-panel={tab} className="pt-3 min-w-0">
            {tab === 'groups' && (
              <LazySection label="failures-groups" minHeight={GROUPS_HEIGHT}>
                <SectionErrorBoundary message="Failure groups failed to load">
                  <Suspense fallback={null}>
                    <FailureGroupsSection days={days} suiteFilter={suiteFilter} />
                  </Suspense>
                </SectionErrorBoundary>
              </LazySection>
            )}
            {tab === 'suite' && (
              <SectionErrorBoundary message="Failures by suite failed to load">
                <Suspense fallback={null}>
                  <FailuresDrill days={days} suiteFilter={suiteFilter} />
                </Suspense>
              </SectionErrorBoundary>
            )}
            {tab === 'scatter' && (
              <SectionErrorBoundary message="Test scatter failed to load">
                <Suspense fallback={null}>
                  <ScatterSection days={days} suiteFilter={suiteFilter} placement="project" />
                </Suspense>
              </SectionErrorBoundary>
            )}
            {tab === 'categories' && (
              <>
                <KindFilterChips byKind={byKind} value={kindFilter} onChange={setKindFilter} />
                <FailureCategoryCard
                  categories={kindFilteredCategories}
                  totalFailures={model.failedRuns}
                  uncategorizedPct={model.uncategorizedPct}
                  onCorrect={openCorrection}
                  onClassify={() => setClassifyOpen(true)}
                  kindFilter={kindFilter}
                />
              </>
            )}
          </div>
        </div>
      </PageSuiteTargetContext.Provider>

      <Disclosure
        title="How this score is computed"
        summary={verdict === 'PENDING' ? 'not measured' : `stability ${model.composite} / 100`}
      >
        <ScoreDetails model={model} verdict={verdict} topFailing={topFailing} />
      </Disclosure>

      <Disclosure title="Failure timeline" summary={`last ${Math.min(days, TIMELINE_MAX_CELLS)} days`}>
        <FailureTimeline trend={trend} days={days} />
      </Disclosure>

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>

      {/* Epic 8 US-8.2 — the bisect: the suspect commits for a row's test, beside the table. */}
      <SidePanel
        open={suspectsFor !== null && Boolean(latestFailedRun?.id)}
        onClose={() => setSuspectsFor(null)}
        title={suspectsFor ? `Suspects: ${suspectsFor.test.test_name}` : 'Suspects'}
        closeLabel="Close suspects"
      >
        <SuspectsPanel
          runId={latestFailedRun?.id ?? null}
          fingerprint={suspectsFor?.test.test_fingerprint ?? null}
          panelId="suspects-panel"
        />
      </SidePanel>

      {muteFor?.test.test_fingerprint && project?.id && (
        <MuteTestModal
          test={muteFor.test}
          flakyEntry={muteFor.flaky}
          projectId={project.id}
          onClose={() => setMuteFor(null)}
        />
      )}

      {correctionOpen && actionTarget?.test_fingerprint && project?.id && (
        <CorrectClassificationModal
          projectId={project.id}
          fingerprint={actionTarget.test_fingerprint}
          testName={actionTarget.test_name}
          onClose={() => setCorrectionOpen(false)}
        />
      )}

      {jiraFor?.test.test_fingerprint && project?.id && (
        <CreateJiraIssueModal
          projectId={project.id}
          fingerprint={jiraFor.test.test_fingerprint}
          testName={jiraFor.test.test_name}
          onClose={() => setJiraFor(null)}
        />
      )}

      {classifyOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Classify uncategorised failures"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !classifying && setClassifyOpen(false)}
        >
          <div
            className="w-full max-w-md rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] p-5 shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold text-[var(--color-text)] m-0">
              Classify uncategorised failures
            </h2>
            <p className="mt-1 text-[12.5px] text-[var(--color-text-muted)]">
              Every failing test in the last <strong>{days}</strong> day{days === 1 ? '' : 's'}
              {suiteLabel && <> in <code className="font-mono">{suiteLabel}</code></>} that
              has no category yet will be tagged with the selected category.
            </p>

            <div className="mt-4 space-y-2">
              {CATEGORY_CHOICES.map(c => (
                <button
                  key={c.id}
                  type="button"
                  disabled={classifying}
                  onClick={() => handleClassify(c.id)}
                  className="w-full text-left px-3 py-2.5 rounded-md border border-[var(--color-border)] hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-hover)]/40 transition-colors disabled:opacity-50"
                >
                  <div className="text-[13px] font-medium text-[var(--color-text)]">{c.label}</div>
                  <div className="text-[11.5px] text-[var(--color-text-muted)] mt-0.5">{c.desc}</div>
                </button>
              ))}
            </div>

            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={() => setClassifyOpen(false)}
                disabled={classifying}
                className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────
function normaliseList<T>(raw: unknown): T[] {
  // The analytics endpoints return either an array or a {items: T[]} envelope
  // depending on the route. Coerce to a plain array so the model code doesn't
  // have to know.
  if (Array.isArray(raw)) return raw as T[]
  if (raw && typeof raw === 'object' && Array.isArray((raw as { items?: unknown }).items)) {
    return (raw as { items: T[] }).items
  }
  return []
}
