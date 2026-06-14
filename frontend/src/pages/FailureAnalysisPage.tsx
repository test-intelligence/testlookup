/**
 * Failure Analysis — verdict-led redesign per
 * design_handoff_failure_analysis/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb (project · window · updated) + Customize +
 *             7d/14d/30d/90d window picker + Export + "Open triage queue" CTA.
 *   Verdict → 1.45fr | 1fr split. Five variants (REPEAT_FAILURE blocked /
 *             FLAKY at-risk / FIRST_TIME at-risk / RECOVERING info / STABLE
 *             healthy / PENDING). Left: pulsing eyebrow → 26 px headline
 *             "<verdict> · <summary>" → lede → 3 issue rows → CTAs. Right:
 *             44 px stability score + threshold-marked meter (red→amber→
 *             green) + 2×2 weighted dimension grid (Pass rate 35 % /
 *             Categorization 20 % / Flake-free 20 % / Time to fix 25 %).
 *   Ribbon  → slim 4-stage workflow strip with skip-aware variant: Flaky
 *             Detection skipped when there's no flake signal, rendered
 *             informational (not red).
 *   KPIs    → 5 cells: Repeat failures · Flaky tests (zero = good!) ·
 *             Uncategorized (≥50% warns) · Total executions · Mean time
 *             to fix.
 *   Body    → 1.65fr | 1fr.
 *     Left  → What's failing (assertion block + 14-cell run strip) +
 *             Failure-category distribution + Failure timeline.
 *     Right → Flakiness card (zero-state positive — the load-bearing
 *             reframe) + Recommended actions + Provenance footer.
 *
 * Out of scope (Phase 2 — README §"Out of Scope"):
 *   - <600 px mobile (bottom-fixed notice on narrow viewports)
 *   - Bisect-runner modal body
 *   - Workflow stage drawer body
 *   - Decision-trail modal body
 *   - Classifier hint editor
 *   - Print styles
 *
 * Data: derives the verdict from existing useFlakyTests / useTopFailing /
 * useFailureCategories / useTrendData. The README proposes a dedicated
 * /api/projects/:id/failures and a runs sub-resource — neither exists
 * yet, so v1 fills the run strip from the trend tail and synthesizes
 * MTTF / lastGreenSha placeholders. CTAs that need new endpoints
 * (mute, bisect, classifier hint) emit toast placeholders.
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Check, ChevronRight, Clock, Code as CodeIcon,
  Download, FileText, GitBranch, LayoutGrid, Minus, Search, ShieldCheck,
  TestTube, TriangleAlert, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import { useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { postData } from '@/services/http'
import {
  useFailureCategories, useFlakyTests, useTopFailing, useTrendData,
} from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type {
  FailureCategoryItem, FlakyTestItem, TopFailingItem,
} from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours (rendered as "24h"); the rest are day counts. Mirrors
// Overview/Runs/Live/Trends/Coverage so users get a single mental model.
const WINDOWS = [1, 7, 14, 30, 90] as const
type Window = (typeof WINDOWS)[number]

// ── CSV export ────────────────────────────────────────────────────────────
/** Wrap a CSV cell. Fields containing comma / quote / newline must be
 *  quoted, and inner double-quotes must be escaped by doubling. */
function csvCell(value: unknown): string {
  if (value == null) return ''
  const s = String(value)
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

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
  // characters in suite labels, etc.) render as mojibake.
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const projectSlug = project ? slugifyProjectName(project.name) : 'all-projects'
  const suiteSlug = suiteFilter ? `-${slugifyProjectName(suiteFilter)}` : ''
  a.download = `failures-${projectSlug}${suiteSlug}-${windowLabel}.csv`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  toast.success(`Exported ${topFailing.length} failing test${topFailing.length === 1 ? '' : 's'}`)
}
// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'REPEAT_FAILURE' | 'FLAKY' | 'FIRST_TIME' | 'RECOVERING' | 'STABLE' | 'PENDING'

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
  REPEAT_FAILURE: {
    border: 'rgba(239,68,68,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-no-go-glow), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: '#fca5a5',
    gateText:    '#fca5a5',
    pillBg: 'rgba(239,68,68,0.12)',
    pillBd: 'rgba(239,68,68,0.30)',
    pillFg: '#fca5a5',
    meter:  '#fca5a5',
    label:  'Repeat failure',
    pulse:  true,
  },
  FLAKY: {
    border: 'rgba(245,158,11,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(245,158,11,0.10), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: '#fcd34d',
    gateText:    '#fcd34d',
    pillBg: 'rgba(245,158,11,0.12)',
    pillBd: 'rgba(245,158,11,0.30)',
    pillFg: '#fcd34d',
    meter:  '#fcd34d',
    label:  'Flaky',
    pulse:  true,
  },
  FIRST_TIME: {
    border: 'rgba(245,158,11,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(245,158,11,0.10), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: '#fcd34d',
    gateText:    '#fcd34d',
    pillBg: 'rgba(245,158,11,0.12)',
    pillBd: 'rgba(245,158,11,0.30)',
    pillFg: '#fcd34d',
    meter:  '#fcd34d',
    label:  'First-time failure',
    pulse:  true,
  },
  RECOVERING: {
    border: 'rgba(68,147,248,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(68,147,248,0.10), transparent 55%)',
    bar:    'var(--color-accent)',
    eyebrowText: '#93c5fd',
    gateText:    '#93c5fd',
    pillBg: 'rgba(68,147,248,0.12)',
    pillBd: 'rgba(68,147,248,0.30)',
    pillFg: '#93c5fd',
    meter:  '#93c5fd',
    label:  'Recovering',
    pulse:  false,
  },
  STABLE: {
    border: 'rgba(34,197,94,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-go-glow), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrowText: '#86efac',
    gateText:    '#86efac',
    pillBg: 'rgba(34,197,94,0.12)',
    pillBd: 'rgba(34,197,94,0.30)',
    pillFg: '#86efac',
    meter:  '#86efac',
    label:  'Stable',
    pulse:  false,
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

// ── Atoms ──────────────────────────────────────────────────────────────────
function GhostBtn({
  children, onClick, title, disabled, asChildLink,
}: {
  children: React.ReactNode
  onClick?: () => void
  title?: string
  disabled?: boolean
  asChildLink?: string
}) {
  const cls = 'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50'
  if (asChildLink) {
    return (
      <Link to={asChildLink} className={cls} style={{ borderColor: 'var(--color-border)' }} title={title}>
        {children}
      </Link>
    )
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
    return (
      <Link to={asChildLink} className={cls} style={style} title={title} onMouseEnter={hoverIn} onMouseLeave={hoverOut}>
        {children}
      </Link>
    )
  }
  return (
    <button type="button" onClick={onClick} title={title} className={cls} style={style} onMouseEnter={hoverIn} onMouseLeave={hoverOut}>
      {children}
    </button>
  )
}

function WindowPicker({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Window"
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
  model, verdict, summary, lede, issues, ctas, topFailing,
}: {
  model: StabilityModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
  issues: IssueRowSpec[]
  ctas: { primary?: IssueRowSpec['cta']; secondary: IssueRowSpec['cta'][] }
  topFailing: TopFailingItem[]
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Failure verdict"
      aria-live="polite"
      className="relative rounded-xl border overflow-hidden grid gap-6"
      style={{
        gridTemplateColumns: '1.45fr 1fr',
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
          Failure verdict
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
        <StabilityMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
        <SuiteFailureBreakdown topFailing={topFailing} />
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
    bad:  { bg: 'rgba(239,68,68,0.08)',  bd: 'rgba(239,68,68,0.30)',  icBg: 'rgba(239,68,68,0.16)',  icFg: '#fca5a5' },
    warn: { bg: 'rgba(245,158,11,0.06)', bd: 'rgba(245,158,11,0.28)', icBg: 'rgba(245,158,11,0.16)', icFg: '#fcd34d' },
    info: { bg: 'rgba(68,147,248,0.06)', bd: 'rgba(68,147,248,0.25)', icBg: 'rgba(68,147,248,0.16)', icFg: '#93c5fd' },
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
              style={{ color: 'var(--color-accent)', borderColor: 'rgba(68,147,248,0.25)', background: 'rgba(68,147,248,0.06)' }}>
              {issue.cta.label} →
            </Link>
          : <button type="button" onClick={issue.cta.onClick} className="text-[11.5px] font-medium px-2 py-0.5 rounded-full border whitespace-nowrap transition-colors"
              style={{ color: 'var(--color-accent)', borderColor: 'rgba(68,147,248,0.25)', background: 'rgba(68,147,248,0.06)' }}>
              {issue.cta.label} →
            </button>
      )}
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
      <div className="relative mt-3 rounded-full overflow-hidden" style={{ height: 6, background: 'var(--color-bg-secondary)' }}>
        <i className="block h-full rounded-full" style={{ width: `${score}%`, background: 'var(--gradient-stability)' }} />
        <div className="absolute inset-0 flex justify-between pointer-events-none" style={{ padding: '0 33%' }}>
          <i className="block w-px h-full" style={{ background: 'rgba(255,255,255,0.25)' }} />
          <i className="block w-px h-full" style={{ background: 'rgba(255,255,255,0.25)' }} />
        </div>
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-faint)] uppercase mt-1.5" style={{ letterSpacing: 'var(--tracking-wide)' }}>
        <span>Block · 0</span>
        <span>At risk · 33</span>
        <span>Stable · 66</span>
        <span>100</span>
      </div>
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
  const valueColor = dim.tone === 'bad' ? '#fca5a5' : dim.tone === 'warn' ? '#fcd34d' : '#34d399'
  const barColor   = dim.tone === 'bad' ? '#ef4444' : dim.tone === 'warn' ? '#f59e0b' : '#22c55e'
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
                <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: '#ef4444' }} />
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

// ── Workflow ribbon ───────────────────────────────────────────────────────
type StageDisplay = 'done' | 'skipped' | 'failed'

interface RibbonStage {
  num: number
  name: string
  status: StageDisplay
  evidence: number
  meta?: React.ReactNode
}

function buildRibbon(model: StabilityModel): RibbonStage[] {
  // Flaky-detection skipped only when there's no flake signal AND we have
  // failures in the window (meaning the stage couldn't run usefully).
  // Otherwise it ran and returned 0 — that's a 'done' stage.
  const flakyDetectionRan = model.flakyCount > 0 || model.failedRuns === 0
  return [
    {
      num: 1, name: 'Flaky Detection',
      status: flakyDetectionRan ? 'done' : 'skipped',
      evidence: model.flakyCount,
      meta: flakyDetectionRan
        ? <>{model.flakyCount} evidence</>
        : <span className="italic">Skipped · no flake signal</span>,
    },
    {
      num: 2, name: 'Category Clustering',
      status: 'done',
      evidence: 1,
      meta: model.totalCategorised === 0
        ? <>0 evidence · <span style={{ color: 'var(--color-text-faint)' }}>no failures yet</span></>
        : model.uncategorizedPct >= 50
          ? <>1 evidence · <span style={{ color: '#fcd34d', fontWeight: 600 }}>unknown cluster</span></>
          : <>1 evidence · <span style={{ color: '#34d399', fontWeight: 600 }}>{Math.round(100 - model.uncategorizedPct)}% confidence</span></>,
    },
    {
      num: 3, name: 'Hotspot Ranking',
      status: 'done',
      evidence: 1,
      meta: <>1 evidence · <span style={{ color: model.repeatFailures.length > 0 ? '#fca5a5' : '#34d399', fontWeight: 600 }}>{model.repeatFailures.length} repeat{model.repeatFailures.length === 1 ? '' : 's'}</span></>,
    },
    {
      num: 4, name: 'Remediation Focus',
      status: 'done',
      evidence: 1,
      meta: <>1 evidence</>,
    },
  ]
}

function CoverageRibbon({ stages }: { stages: RibbonStage[] }) {
  const completed = stages.filter(s => s.status === 'done').length
  const skipped   = stages.filter(s => s.status === 'skipped').length
  const evidence  = stages.reduce((s, x) => s + (x.status === 'skipped' ? 0 : x.evidence), 0)
  return (
    <section
      aria-label="Failure analysis workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Failure analysis workflow · last analysis</h3>
        <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
          <span className="h-1.5 w-1.5 rounded-full inline-block" style={{ background: 'var(--status-passed)' }} />
          {completed} of {stages.length} stages completed
          {skipped > 0 && <> · {skipped} skipped</>}
          <> · {evidence} evidence item{evidence === 1 ? '' : 's'}</>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        {stages.map((s, i) => <StageCell key={s.num} stage={s} isLast={i === stages.length - 1} />)}
      </div>
    </section>
  )
}

function StageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  const ic = stage.status === 'done'
    ? { bg: 'var(--status-passed-soft)', fg: '#34d399', icon: <Check className="h-2.5 w-2.5" strokeWidth={3} /> }
    : stage.status === 'failed'
      ? { bg: 'var(--status-failed-soft)', fg: '#fca5a5', icon: <XCircle className="h-2.5 w-2.5" strokeWidth={3} /> }
      : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: <Minus className="h-2.5 w-2.5" strokeWidth={3} /> }
  const trackFg = stage.status === 'done' ? 'var(--status-passed)'
    : stage.status === 'failed' ? 'var(--gate-no-go)'
    : 'var(--color-text-muted)'
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}, ${stage.status}, ${stage.evidence} evidence`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className={clsx(
        'relative flex items-center gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]',
        stage.status === 'skipped' && 'opacity-80',
      )}
      style={{ padding: '8px 12px', borderRight: isLast ? '0' : '1px solid var(--color-border)', textAlign: 'left' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none"
        style={{ width: 18, height: 18, background: ic.bg, color: ic.fg }}
      >
        {ic.icon}
      </span>
      <span className="flex flex-col gap-px min-w-0">
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] leading-[1.2]">{stage.name}</span>
        <span className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums truncate">{stage.meta}</span>
      </span>
      <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-faint)] self-start pt-0.5">
        {String(stage.num).padStart(2, '0')}
      </span>
      <span className="absolute left-0 right-0 bottom-0" style={{ height: 2, background: trackFg, opacity: 0.7 }} />
    </button>
  )
}

// ── KPI strip ─────────────────────────────────────────────────────────────
type KpiTone = 'good' | 'warn' | 'bad' | 'accent' | 'neutral'

interface KpiCellProps {
  Icon?: typeof TestTube
  label: string
  value: React.ReactNode
  meta?: React.ReactNode
  tone?: KpiTone
  isFirst?: boolean
  isLast?: boolean
}

function KpiCell({ Icon, label, value, meta, tone = 'neutral', isFirst, isLast }: KpiCellProps) {
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
      </div>
      {meta && <div className="text-[10.5px] text-[var(--color-text-muted)]">{meta}</div>}
    </div>
  )
}

// ── What's failing card ───────────────────────────────────────────────────
interface RunCell {
  iso: string
  kind: 'pass' | 'fail' | 'notrun'
}

function build14CellStrip(trend: TrendPoint[]): RunCell[] {
  const byDate = new Map<string, RunCell['kind']>()
  for (const p of trend) {
    const iso = p.date.slice(0, 10)
    if (p.failed > 0)      byDate.set(iso, 'fail')
    else if (p.passed > 0) byDate.set(iso, 'pass')
    else                   byDate.set(iso, 'notrun')
  }
  const today = new Date()
  const cells: RunCell[] = []
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(today.getDate() - i)
    const iso = d.toISOString().slice(0, 10)
    cells.push({ iso, kind: byDate.get(iso) ?? 'notrun' })
  }
  return cells
}

function WhatsFailingCard({
  topFailingTest, totalRuns, trend, onBisect, onMute,
}: {
  topFailingTest: TopFailingItem | null
  totalRuns: number
  trend: TrendPoint[]
  onBisect: () => void
  onMute: () => void
}) {
  // Aggregate failed-run count from trend (which reads test_runs.failed_tests
  // directly). A suite can have failed run aggregates (pass rate < 100%)
  // while test_cases rows haven't landed — the live-stream Redis-buffer gap
  // from CLAUDE.md pitfall #15. Cross-check so we don't render the green
  // "every recent run passed" all-clear while pass-rate / KPI tiles show
  // the same suite is failing.
  const failingExecutions = trend.reduce(
    (s, p) => s + (p.failed || 0) + (p.broken || 0), 0,
  )
  const perTestRowsMissing = (
    (!topFailingTest || topFailingTest.fail_count === 0) && failingExecutions > 0
  )

  if (perTestRowsMissing) {
    return (
      <CardShell
        title="What's failing"
        rightSlot={<Pill tone="warn">Per-test data pending</Pill>}
      >
        <div className="px-4 py-6 flex flex-col items-center text-center">
          <TriangleAlert className="h-8 w-8 mb-2" style={{ color: '#fcd34d' }} />
          <p className="text-[13px] text-[var(--color-text-secondary)] m-0 max-w-md">
            <strong style={{ color: 'var(--color-text)' }}>{failingExecutions}</strong>{' '}
            failing execution{failingExecutions === 1 ? '' : 's'} detected in this window,
            but per-test rows haven&apos;t been persisted yet — common right after a
            live-stream run finishes. Inspect the failed runs on the Runs page.
          </p>
        </div>
      </CardShell>
    )
  }

  if (!topFailingTest || topFailingTest.fail_count === 0) {
    return (
      <CardShell title="What's failing" rightSlot={<Pill tone="good">No failures</Pill>}>
        <div className="px-4 py-6 flex flex-col items-center text-center">
          <ShieldCheck className="h-8 w-8 mb-2" style={{ color: 'var(--status-passed)' }} />
          <p className="text-[13px] text-[var(--color-text-secondary)] m-0">
            No failing tests in this window — every recent run passed.
          </p>
        </div>
      </CardShell>
    )
  }

  const cells = build14CellStrip(trend)
  const failedCells = cells.filter(c => c.kind === 'fail').length
  const passedCells = cells.filter(c => c.kind === 'pass').length
  const notRunCells = cells.filter(c => c.kind === 'notrun').length

  return (
    <CardShell
      title="What's failing"
      rightSlot={
        <div className="flex items-center gap-2">
          <Pill tone="bad">Hard regression</Pill>
          <Pill tone="neutral">Not flaky</Pill>
        </div>
      }
    >
      <div
        className="relative"
        style={{
          padding: '16px 18px',
          borderLeft: '3px solid #ef4444',
          background: 'linear-gradient(90deg, rgba(239,68,68,0.06), transparent 30%), var(--color-bg-card)',
        }}
      >
        <div className="flex items-start justify-between gap-3 flex-wrap mb-2.5">
          <div className="min-w-0">
            <div className="font-mono text-[13.5px] font-medium truncate" style={{ color: '#fca5a5' }}>
              {topFailingTest.test_name}
            </div>
            <div className="text-[12px] text-[var(--color-text-muted)] mt-1 flex flex-wrap gap-x-3 gap-y-1">
              <span>Failed across <strong style={{ color: 'var(--color-text)' }}>{topFailingTest.fail_count}</strong> run{topFailingTest.fail_count === 1 ? '' : 's'}</span>
              <span>· of {totalRuns} total in window</span>
              <span>· per-test owner data not yet available</span>
            </div>
            {topFailingTest.failure_step && (
              <div
                className="text-[11.5px] mt-1"
                style={{ color: 'var(--color-text-muted)' }}
                title={`Failed at step: ${topFailingTest.failure_step}`}
              >
                failed at:{' '}
                <span className="font-mono" style={{ color: '#fca5a5' }}>
                  {topFailingTest.failure_step}
                </span>
              </div>
            )}
          </div>
          <div className="text-[12px]" style={{ color: '#fca5a5', fontWeight: 600, whiteSpace: 'nowrap' }}>
            {topFailingTest.fail_count} failed
          </div>
        </div>

        <pre
          role="region"
          aria-label="Recent failure summary"
          className="font-mono text-[11.5px] m-0 whitespace-pre-wrap"
          style={{
            background: 'var(--color-bg)',
            border: '1px solid var(--color-border)',
            borderLeft: '2px solid rgba(239,68,68,0.55)',
            borderRadius: 'var(--radius-sm)',
            padding: '10px 12px',
            color: 'var(--color-text-secondary)',
          }}
        >
{`✗ ${topFailingTest.test_name}
  Failed ${topFailingTest.fail_count} time${topFailingTest.fail_count === 1 ? '' : 's'} in the current window.
  Per-failure stack traces will appear here when the failures endpoint lands.`}
        </pre>

        <div className="flex items-center gap-2 mt-3">
          <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
            Last 14 days
          </span>
          <div
            role="img"
            aria-label={`Run strip: ${failedCells} failed, ${passedCells} passed, ${notRunCells} not run.`}
            className="grid gap-[3px] flex-1"
            style={{ gridTemplateColumns: 'repeat(14, 1fr)' }}
          >
            {cells.map((c, i) => (
              <div
                key={i}
                title={`${c.iso} · ${c.kind}`}
                aria-label={`${c.iso}: ${c.kind === 'fail' ? 'failed' : c.kind === 'pass' ? 'passed' : 'not run'}`}
                className="rounded-sm"
                style={{
                  height: 14,
                  background: c.kind === 'fail' ? 'var(--gate-no-go)'
                    : c.kind === 'pass' ? 'var(--status-passed)'
                    : 'var(--color-bg-secondary)',
                  border: c.kind === 'notrun' ? '1px solid var(--color-border)' : '1px solid transparent',
                  opacity: c.kind === 'pass' ? 0.7 : 1,
                }}
              />
            ))}
          </div>
          <span className="text-[10.5px] tabular-nums text-[var(--color-text-faint)]">
            {failedCells} fail · {notRunCells} idle · {passedCells} pass
          </span>
        </div>

        <div className="flex flex-wrap gap-2 mt-3.5">
          <PrimaryBtn onClick={onBisect}>
            <GitBranch className="h-3.5 w-3.5" /> Start bisect
          </PrimaryBtn>
          <GhostBtn onClick={() => toast('Run logs open in /runs — coming in Phase 2', { icon: '🪵' })}>
            View run logs
          </GhostBtn>
          <GhostBtn onClick={onMute} title="Mute the test with a documented reason">
            Mute test
          </GhostBtn>
        </div>
      </div>
    </CardShell>
  )
}

// ── Failure category card ─────────────────────────────────────────────────
const CANONICAL_CATEGORIES: { id: string; label: string; color: string; matcher: RegExp }[] = [
  { id: 'unknown',   label: 'Unknown',           color: 'var(--cat-unknown)',   matcher: /unknown|unclassified/i },
  { id: 'assertion', label: 'Assertion mismatch', color: 'var(--cat-assertion)', matcher: /assert/i },
  { id: 'timeout',   label: 'Timeout',            color: 'var(--cat-timeout)',   matcher: /timeout|timed.?out/i },
  { id: 'network',   label: 'Network / 5xx',      color: 'var(--cat-network)',   matcher: /network|http|5\d\d/i },
  { id: 'infra',     label: 'Infra / runner',     color: 'var(--cat-infra)',     matcher: /infra|runner|ci|env/i },
]

function FailureCategoryCard({ categories, totalFailures, uncategorizedPct }: {
  categories: FailureCategoryItem[]
  totalFailures: number
  uncategorizedPct: number
}) {
  const buckets = new Map<string, number>()
  for (const c of categories) {
    const cat = CANONICAL_CATEGORIES.find(x => x.matcher.test(c.category))
    const id = cat?.id ?? 'unknown'
    buckets.set(id, (buckets.get(id) ?? 0) + c.count)
  }
  const total = Array.from(buckets.values()).reduce((s, n) => s + n, 0) || totalFailures || 0

  return (
    <CardShell title="Failure category distribution" rightSlot={<span>{total} failure{total === 1 ? '' : 's'}</span>}>
      <div className="px-4 py-3.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
          Categories computed by the failure-analyzer. Empty buckets are kept visible — the page
          tells you what didn't happen, not just what did.
        </p>

        {uncategorizedPct >= 50 && (
          <div
            className="flex items-start gap-2 rounded-md p-2.5 mb-3 text-[12px]"
            style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.28)', color: 'var(--color-text-secondary)' }}
          >
            <AlertTriangle className="h-3.5 w-3.5 flex-none mt-0.5" style={{ color: '#fcd34d' }} />
            <span>
              Classifier confidence low — {Math.round(uncategorizedPct)}% of failures are sitting in <em>Unknown</em>.{' '}
              <button
                type="button"
                onClick={() => toast('Classifier hint editor — coming in Phase 2', { icon: '🏷️' })}
                className="text-[var(--color-accent)] hover:underline"
              >
                Add a fingerprint hint →
              </button>
            </span>
          </div>
        )}

        <div className="flex flex-col">
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
                  gridTemplateColumns: '160px 1fr 56px 56px',
                  borderBottom: i < arr.length - 1 ? '1px dashed var(--color-border)' : '0',
                  paddingTop: 8,
                }}
              >
                <div className="flex items-center gap-2 min-w-0 text-[12.5px] text-[var(--color-text)]">
                  <span aria-hidden className="inline-block w-2 h-2 rounded-sm flex-none" style={{ background: c.color }} />
                  <span className="truncate">{c.label}</span>
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
                  style={empty ? undefined : { color: c.id === 'unknown' ? '#fcd34d' : 'var(--color-text)' }}
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
  const RED   = '#fca5a5'
  const GREEN = '#86efac'
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
function FailureTimeline({ trend, days }: { trend: TrendPoint[]; days: number }) {
  const byDate = new Map<string, { passed: number; failed: number; skipped: number }>()
  for (const p of trend) {
    byDate.set(p.date.slice(0, 10), { passed: p.passed, failed: p.failed, skipped: p.skipped })
  }
  const today = new Date()
  const len = Math.min(days, 30)
  const cells: { iso: string; kind: 'empty' | 'pass' | 'fail'; severity: number }[] = []
  for (let i = len - 1; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(today.getDate() - i)
    const iso = d.toISOString().slice(0, 10)
    const r = byDate.get(iso)
    if (!r || (r.passed === 0 && r.failed === 0 && r.skipped === 0)) {
      cells.push({ iso, kind: 'empty', severity: 0 })
    } else if (r.failed > 0) {
      const total = r.passed + r.failed + r.skipped
      cells.push({ iso, kind: 'fail', severity: total > 0 ? r.failed / total : 1 })
    } else {
      cells.push({ iso, kind: 'pass', severity: 0 })
    }
  }
  const failureCount = cells.filter(c => c.kind === 'fail').length

  return (
    <CardShell title="Failure timeline" rightSlot={<span>last {days} days</span>}>
      <div className="px-4 py-3.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3" style={{ lineHeight: 1.5 }}>
          Repeat-failure events vs total runs in this window. Each cell = 1 day.
        </p>
        <div
          role="img"
          aria-label={`Failure timeline: ${failureCount} day${failureCount === 1 ? '' : 's'} with failures over the last ${len} days.`}
          className="grid gap-[3px] mb-2.5"
          style={{ gridTemplateColumns: `repeat(${len}, 1fr)` }}
        >
          {cells.map((c) => (
            <div
              key={c.iso}
              title={`${c.iso} · ${c.kind}`}
              aria-label={c.kind === 'empty' ? `${c.iso}: 0 runs` : `${c.iso}: ${c.kind === 'fail' ? 'failures' : 'passing'}`}
              className="rounded-sm"
              style={{
                aspectRatio: '1',
                background: c.kind === 'empty'
                  ? 'var(--color-bg-secondary)'
                  : c.kind === 'fail'
                    ? `rgba(239,68,68,${0.45 + 0.55 * Math.min(1, c.severity)})`
                    : 'rgba(34,197,94,0.7)',
                border: c.kind === 'empty' ? '1px solid var(--color-border)' : '1px solid transparent',
              }}
            />
          ))}
        </div>
        <div className="flex justify-between text-[10px] text-[var(--color-text-muted)]">
          <span>{len} day{len === 1 ? '' : 's'} ago</span>
          <span className="inline-flex items-center gap-1">
            Pass
            <i className="inline-block w-2 h-2 rounded-sm" style={{ background: 'rgba(34,197,94,0.7)' }} />
            <span aria-hidden>·</span>
            Fail
            <i className="inline-block w-2 h-2 rounded-sm" style={{ background: '#ef4444' }} />
          </span>
          <span>Today</span>
        </div>
      </div>
    </CardShell>
  )
}

// ── Flakiness card ────────────────────────────────────────────────────────
function FlakinessCard({ flaky, repeatFailures }: { flaky: FlakyTestItem[]; repeatFailures: TopFailingItem[] }) {
  const isStable = flaky.length === 0
  const headerBorder = isStable ? 'rgba(34,197,94,0.28)' : 'rgba(245,158,11,0.28)'
  const stripe       = isStable ? '#22c55e'              : '#f59e0b'
  const ledeBg       = isStable
    ? 'linear-gradient(90deg, rgba(34,197,94,0.06), transparent 40%), var(--color-bg-card)'
    : 'linear-gradient(90deg, rgba(245,158,11,0.06), transparent 40%), var(--color-bg-card)'
  return (
    <section
      aria-label="Flakiness analysis"
      className="rounded-xl"
      style={{
        background: ledeBg,
        border: `1px solid ${headerBorder}`,
        borderLeft: `3px solid ${stripe}`,
        padding: '14px 16px',
      }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-1 flex items-center gap-2 text-[var(--color-text)]">
        Flakiness
        <Pill tone={isStable ? 'good' : 'warn'}>{isStable ? 'Stable' : 'Flaky'}</Pill>
      </h3>

      {isStable && repeatFailures.length > 0 ? (
        <>
          <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
            <strong style={{ color: 'var(--color-text)' }}>This is a real failure, not a flake.</strong>{' '}
            Don't re-run hoping for green. The signal is consistent — every observed run on this branch hit the same assertion.
          </p>
          <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            <FlakeMetaCell k="Detector"      v="v2"        tone="good" />
            <FlakeMetaCell k="Required runs" v="≥ 5 mixed" tone="neutral" />
            <FlakeMetaCell k="Cost saved"    v="—"         tone="neutral" />
          </div>
        </>
      ) : isStable ? (
        <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
          <strong style={{ color: 'var(--color-text)' }}>No flakes detected this window.</strong>{' '}
          Every observed run produced a deterministic outcome. Zero flakes is good news — keep going.
        </p>
      ) : (
        <>
          {(() => {
            // Manual-triage entries (source==='manual') are human-flagged, not
            // measured intermittents, so the "intermittent pass/fail" copy only
            // applies to auto-detected ones. Describe each present source.
            const autoCount = flaky.filter(f => f.source !== 'manual').length
            const manualCount = flaky.length - autoCount
            return (
              <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
                {autoCount > 0 && (
                  <>
                    {autoCount} test{autoCount === 1 ? '' : 's'} show intermittent pass/fail patterns on the same SHA. Re-runs
                    won't fix these — investigate the underlying race or fixture issue.
                  </>
                )}
                {autoCount > 0 && manualCount > 0 && ' '}
                {manualCount > 0 && (
                  <>
                    {manualCount} test{manualCount === 1 ? '' : 's'} {manualCount === 1 ? 'was' : 'were'} manually flagged as
                    flaky on /my-failures{autoCount > 0 ? ' as well' : ''}.
                  </>
                )}
              </p>
            )
          })()}
          <div className="flex flex-col gap-1.5 mt-3">
            {flaky.slice(0, 5).map(f => (
              <div
                key={f.test_fingerprint}
                className="flex flex-col gap-1 rounded-sm border px-2.5 py-2 text-[12px]"
                style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
              >
                <div className="grid items-center gap-3" style={{ gridTemplateColumns: '1fr auto' }}>
                  <span className="font-mono text-[11.5px] text-[var(--color-text)] truncate">{f.test_name}</span>
                  {f.source === 'manual' ? (
                    <span className="tabular-nums" style={{ color: '#93c5fd' }} title="Manually triaged as flaky on /my-failures">Flagged</span>
                  ) : (
                    <span className="tabular-nums" style={{ color: '#fcd34d' }}>{Math.round(f.failure_rate_pct)}% flake</span>
                  )}
                </div>
                {f.likely_cause && (
                  <span className="text-[10.5px] text-[var(--color-text-muted)] leading-tight">{f.likely_cause}</span>
                )}
              </div>
            ))}
            {flaky.length > 5 && (
              <span className="text-[11px] text-[var(--color-text-muted)] mt-1">+{flaky.length - 5} more</span>
            )}
          </div>
        </>
      )}
    </section>
  )
}

function FlakeMetaCell({ k, v, tone }: { k: string; v: string; tone: 'good' | 'neutral' }) {
  const fg = tone === 'good' ? '#34d399' : 'var(--color-text-secondary)'
  return (
    <div
      className="rounded-sm border px-2 py-2"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>{k}</div>
      <div className="text-[13px] font-semibold tabular-nums mt-0.5" style={{ color: fg }}>{v}</div>
    </div>
  )
}

// ── Recommended actions ───────────────────────────────────────────────────
interface RecRow {
  role: 'dev' | 'qa' | 'rm'
  Icon: typeof CodeIcon
  label: string
  body: React.ReactNode
  dim?: boolean
  cta?: { label: string; onClick: () => void }
}

function buildRecActions(model: StabilityModel): RecRow[] {
  const recs: RecRow[] = []
  const top = model.topFailingTest
  if (top) {
    recs.push({
      role: 'dev',
      Icon: CodeIcon,
      label: `Developer · ${(top.test_name.split(/[./\\]/).pop() ?? top.test_name).slice(0, 40)}`,
      body: (
        <>
          Investigate why <code>{top.test_name}</code> is failing — failed {top.fail_count} time{top.fail_count === 1 ? '' : 's'} in this window.
          {' '}Bisect from the last green run if you have one.
        </>
      ),
      cta: { label: 'Open', onClick: () => toast('Test detail — coming in Phase 2', { icon: '🔍' }) },
    })
  }
  if (model.uncategorizedPct >= 50) {
    recs.push({
      role: 'qa',
      Icon: ShieldCheck,
      label: 'QA · clustering',
      body: <>Add category hints so future runs auto-route the failing tests instead of sitting in <em>Unknown</em>.</>,
      cta: { label: 'Open', onClick: () => toast('Classifier hint editor — coming in Phase 2', { icon: '🏷️' }) },
    })
  }
  recs.push({
    role: 'rm',
    Icon: Clock,
    label: 'Release manager · gating',
    dim: model.repeatFailures.length === 0,
    body: model.repeatFailures.length > 0
      ? <>Block deploys that include the failing test{model.repeatFailures.length === 1 ? '' : 's'} in their gate path until the regression is resolved.</>
      : <>No deploy-gate action required — no repeat failures in this window.</>,
    cta: model.repeatFailures.length > 0
      ? { label: 'Open', onClick: () => toast('Release-gate editor — coming in Phase 2', { icon: '🚦' }) }
      : undefined,
  })
  return recs
}

function RecommendedActions({ recs }: { recs: RecRow[] }) {
  return (
    <CardShell title="Recommended actions" rightSlot={<span>routed by role</span>}>
      <div className="px-4 py-3.5 flex flex-col gap-2">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-1">Generated from the failing test and its history.</p>
        {recs.map((r, i) => <RecActionRow key={i} rec={r} />)}
      </div>
    </CardShell>
  )
}

function RecActionRow({ rec }: { rec: RecRow }) {
  const palette = {
    dev: { bg: 'rgba(168,85,247,0.16)', fg: '#c4b5fd' },
    qa:  { bg: 'rgba(68,147,248,0.16)', fg: '#93c5fd' },
    rm:  { bg: 'rgba(34,197,94,0.16)',  fg: '#86efac' },
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
      {rec.cta ? (
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
      ) : (
        <span className="text-[11px] text-[var(--color-text-faint)] px-2">—</span>
      )}
    </div>
  )
}

// ── Provenance footer ─────────────────────────────────────────────────────
function ProvenanceFooter({ model, refreshedAt }: { model: StabilityModel; refreshedAt: string }) {
  const skippedStages = model.repeatFailures.length > 0 && model.flakyCount === 0 ? 1 : 0
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>failure analyzer v2</span>
        <span aria-hidden>·</span>
        <span>{model.totalRuns} executions analysed</span>
        <span aria-hidden>·</span>
        <span>{skippedStages} stage{skippedStages === 1 ? '' : 's'} skipped</span>
        <span aria-hidden>·</span>
        <span>last refreshed {refreshedAt}</span>
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

function Pill({ children, tone }: { children: React.ReactNode; tone: 'good' | 'warn' | 'bad' | 'neutral' }) {
  const palette = {
    good:    { bg: 'rgba(34,197,94,0.15)',      bd: 'rgba(34,197,94,0.30)', fg: '#86efac' },
    warn:    { bg: 'rgba(245,158,11,0.15)',     bd: 'rgba(245,158,11,0.30)', fg: '#fcd34d' },
    bad:     { bg: 'var(--status-failed-soft)', bd: 'rgba(239,68,68,0.30)', fg: '#fca5a5' },
    neutral: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)' },
  }[tone]
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase"
      style={{ background: palette.bg, border: `1px solid ${palette.bd}`, color: palette.fg, letterSpacing: 'var(--tracking-wide)' }}
    >
      {children}
    </span>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────
export default function FailureAnalysisPage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Global shared time-window preference — picking 24h here propagates
  // to /reports/summary, /live, /coverage, /trends, /runs, /overview,
  // /my-failures and vice versa. Snapped to this page's allowed set.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, WINDOWS) as Window
  const setDays = setStoredDays as (w: Window) => void

  const [showPicker, setShowPicker] = useState(false)
  const [selectedSuite, setSelectedSuite] = useState('')
  const analyticsView = useAnalyticsView('failures')
  const suiteFilter = selectedSuite || null
  const { options: suiteOptions } = useSuiteOptions(days)

  // ── Compare-to-previous-window toggle ────────────────────────────────
  // The "Compare to previous window" CTA flips this on, which triggers a
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
  // Surface the suite of the most-recent failing run in the header so a user
  // landing on this page can immediately see which test suite owns the
  // failures they're about to triage.
  const { data: latestFailedRuns } = useRuns({ page: 1, size: 1, days, status: 'FAILED', ...(selectedSuite && { suite_name: selectedSuite }) })
  const latestFailedRun = latestFailedRuns?.items?.[0]

  const flaky      = useMemo<FlakyTestItem[]>(() => normaliseList<FlakyTestItem>(flakyData), [flakyData])
  const categories = useMemo<FailureCategoryItem[]>(() => normaliseList<FailureCategoryItem>(categoryData), [categoryData])
  const topFailing = useMemo<TopFailingItem[]>(() => normaliseList<TopFailingItem>(topData), [topData])
  const trend: TrendPoint[] = useMemo(() => trendsData?.data ?? [], [trendsData])

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
  const ribbonStages = useMemo(() => buildRibbon(model), [model])
  const recs = useMemo(() => buildRecActions(model), [model])

  const isLoading = flakyLoading || categoryLoading || topLoading || trendsLoading

  // ── CTA handlers ─────────────────────────────────────────────────────────
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

  // Classify modal — opened by the "Category unknown" issue row CTA. Lets
  // the user bulk-assign a category (Flaky / Product Bug / Infrastructure /
  // Test Data / Automation Defect) to every uncategorised failure in the
  // current project + window. The selection persists via the
  // ``/analytics/classify-uncategorized`` endpoint.
  const [classifyOpen, setClassifyOpen] = useState(false)
  const [classifying, setClassifying] = useState(false)

  async function handleClassify(category: 'FLAKY' | 'PRODUCT_BUG' | 'INFRASTRUCTURE' | 'TEST_DATA' | 'AUTOMATION_DEFECT') {
    if (!project?.id) {
      toast.error('Pick a specific project to classify failures.')
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
        ...(selectedSuite ? { suite_name: selectedSuite } : {}),
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

  // Why-not-a-flake explanation — surfaces the heuristics so users understand
  // what the verdict means rather than having to read the rules engine code.
  function showFlakeWhyDetails() {
    toast(
      [
        'Why this isn\'t flagged as flaky:',
        '',
        '• A test is "flaky" when it both passed and failed on the same fingerprint within the window.',
        '• Every failing test in this window has only failing executions — no recent pass that would indicate intermittency.',
        '• Retrying won\'t fix it; treat the failures as a hard regression and bisect against the last green run.',
      ].join('\n'),
      { icon: '🪛', duration: 12000, style: { whiteSpace: 'pre-line', maxWidth: 480 } },
    )
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

  const projectLabel = project?.name ?? 'All Projects'
  const refreshedAt = '4h ago'

  // Verdict summary headline
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

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      return <>No executions in the last {days} days. Run a workflow or extend the window to populate failure analysis.</>
    if (verdict === 'STABLE')
      return <>Every test in the {days}-day window passed. No regressions, no flakes — nothing to triage.</>
    if (verdict === 'REPEAT_FAILURE')
      return <>The same assertion has failed on every observed run in the window. This is a deterministic regression, not a flake — re-running won't fix it. Start by bisecting against the last green commit.</>
    if (verdict === 'FLAKY')
      return <>{model.flakyCount} test{model.flakyCount === 1 ? '' : 's'} show pass/fail oscillation on the same SHA. Re-runs may pass without fixing the underlying race or fixture issue.</>
    if (verdict === 'FIRST_TIME')
      return <>A new failure landed in this window with no prior history. Check the recent merges and the failure category before deciding to gate.</>
    return <>Previously failing tests have started passing again. Confirm with one more run before declaring resolution.</>
  })()

  // Issues — up to 3
  const issues: IssueRowSpec[] = []
  if (model.topFailingTest && model.topFailingTest.fail_count > 0) {
    const t = model.topFailingTest
    // Prefer the flaky-list entry for this exact test (if it exists)
    // since that carries the test's OWN ``total_runs`` — the only
    // denominator that makes "failure rate" honest. Falling back to
    // ``model.totalRuns`` (executions across every test in the window)
    // produced the user-reported "0% failure rate on test X — failed
    // 8 of 2773" bug: 8/2773 rounds to 0%, and the denominator was
    // comparing one test's failures to the entire suite's executions.
    const flakyMatch = flaky.find(f => f.test_name === t.test_name)
    const perTestRatePct = flakyMatch && flakyMatch.total_runs > 0
      ? (flakyMatch.fail_count / flakyMatch.total_runs) * 100
      : null
    // Share of failures in the current window — meaningful when the
    // per-test rate isn't available. Tells the user "this single test
    // accounts for X% of the failures you're looking at."
    const failureSharePct = model.failedRuns > 0
      ? (t.fail_count / model.failedRuns) * 100
      : null

    // Format a percentage so values under 1% show one decimal instead
    // of collapsing to "0%". 8/2773 now reads as "0.3%", not "0%".
    const fmtPct = (n: number): string => (
      n > 0 && n < 1 ? `${n.toFixed(1)}%` : `${Math.round(n)}%`
    )

    const headline = perTestRatePct !== null
      ? <><strong>{fmtPct(perTestRatePct)} failure rate</strong> on <code>{t.test_name}</code> — failed {t.fail_count} of {flakyMatch!.total_runs} executions.</>
      // Drop the misleading denominator when we don't actually know this
      // test's run count. Lead with the count + share so the user gets
      // an actionable signal rather than a fake-precise rate.
      : (
          <><code>{t.test_name}</code> failed <strong>{t.fail_count}</strong> time{t.fail_count === 1 ? '' : 's'} in this window{failureSharePct !== null ? <> — <strong>{fmtPct(failureSharePct)}</strong> of failures here</> : null}.</>
        )

    issues.push({
      tone: 'bad',
      Icon: XCircle,
      body: (
        <>
          {headline}
          {model.repeatFailures.length > 1 && <> <span className="dim">+{model.repeatFailures.length - 1} other repeat{model.repeatFailures.length - 1 === 1 ? '' : 's'}.</span></>}
        </>
      ),
      cta: {
        label: 'Open test',
        // The top-failing-test record only carries (test_name, fail_count) —
        // there's no canonical run/test-case id to deep-link into. Route the
        // user to /search scoped to test cases with the test name pre-filled
        // so they can click into the most recent failing occurrence (or any
        // historical run) from there. Keyword mode matches an exact substring
        // on the test_name column.
        onClick: () => navigate(`/search?q=${encodeURIComponent(t.test_name)}&scope=tests&mode=keyword`),
      },
    })
  }
  if (model.uncategorizedPct >= 50) {
    // Re-worded from the previous "clustering ran but 100% of failures
    // couldn't be matched to a known pattern. No owner auto-routed; no
    // playbook attached." That phrasing implied (a) the cluster stage
    // was the categorisation source (it isn't — categories come from
    // the AI triage path), and (b) something concrete failed during
    // owner routing (the routing simply doesn't fire without a
    // category). The new wording is shorter, accurate, and points the
    // user at the actionable next step.
    const pct = Math.round(model.uncategorizedPct)
    issues.push({
      tone: 'warn',
      Icon: AlertTriangle,
      body: (
        <>
          <strong>{pct}% of failures aren&apos;t categorised yet.</strong>
          {' '}<span className="dim">Classify them so owners can be auto-routed and a playbook applied.</span>
        </>
      ),
      cta: {
        label: 'Classify',
        // Opens the inline category picker (FLAKY / PRODUCT_BUG /
        // INFRASTRUCTURE / TEST_DATA / AUTOMATION_DEFECT) — the user can
        // bulk-label every uncategorised failure in the current project +
        // window. Persists via /api/v1/analytics/classify-uncategorized.
        onClick: () => setClassifyOpen(true),
      },
    })
  }
  if (verdict === 'REPEAT_FAILURE' && model.flakyCount === 0) {
    issues.push({
      tone: 'info',
      Icon: Check,
      body: (
        <>
          <strong>Not a flake</strong> — flake detector found zero intermittents.
          {' '}<span className="dim">Treat as a hard regression, not a re-run candidate.</span>
        </>
      ),
      cta: { label: 'Why', onClick: () => showFlakeWhyDetails() },
    })
  }

  const verdictCtas = {
    primary: { label: 'Open triage queue', onClick: () => navigate(`/runs?days=${days}`) } as IssueRowSpec['cta'],
    secondary: [
      model.topFailingTest
        ? { label: 'Notify owner', onClick: () => handleNotifyOwner() } as IssueRowSpec['cta']
        : null,
      // Toggle an inline comparison panel that shows current-window vs
      // prior-window deltas (failures, runs, pass rate). Cheap client-
      // side compute on a double-window trend fetch — no bespoke
      // backend endpoint needed.
      {
        label: comparing ? 'Hide comparison' : 'Compare to previous window',
        onClick: () => setComparing(c => !c),
      } as IssueRowSpec['cta'],
    ].filter((c): c is IssueRowSpec['cta'] => c !== null),
  }

  return (
    <PageShell>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Failure Analysis
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Project</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            {selectedSuite && (
              <>
                <span aria-hidden>·</span>
                <span>Suite</span>
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{selectedSuite}</code>
              </>
            )}
            <span aria-hidden>·</span>
            <span>Window</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">last {days} days</code>
            {latestFailedRun && (latestFailedRun.primary_suite_name || latestFailedRun.suite_names?.length) && (
              <>
                <span aria-hidden>·</span>
                <span>Latest failing suite</span>
                <SuiteBadge primary={latestFailedRun.primary_suite_name} all={latestFailedRun.suite_names} />
              </>
            )}
            <span aria-hidden>·</span>
            <span>Updated {refreshedAt}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <GhostBtn onClick={() => setShowPicker(true)} title="Customize widgets">
            <LayoutGrid className="h-3.5 w-3.5" />
            Customize
          </GhostBtn>
          <WindowPicker value={days} onChange={setDays} />
          <SuiteFilterSelect
            value={selectedSuite}
            onChange={setSelectedSuite}
            options={suiteOptions}
            allLabel="All suites"
          />
          <GhostBtn
            onClick={() => handleExportCsv({
              topFailing, flaky, categories, project, days, suiteFilter,
            })}
            title="Export failure data as CSV"
          >
            <Download className="h-3.5 w-3.5" />
            Export
          </GhostBtn>
          <PrimaryBtn asChildLink={`/runs?days=${days}`} title="Open the triage queue filtered to this window">
            Open triage queue <ChevronRight className="h-3.5 w-3.5" />
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
        topFailing={topFailing}
      />

      <CoverageRibbon stages={ribbonStages} />

      {(analyticsView.widgetIds.length === 0 || analyticsView.widgetIds.includes('failure_kpis')) && (
        <section aria-label="Failure metrics" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
          <KpiCell
            Icon={TriangleAlert}
            label="Repeat failures"
            value={model.repeatFailures.length}
            tone={model.repeatFailures.length > 0 ? 'bad' : 'good'}
            meta={model.repeatFailures.length > 0
              ? <>tests failing ≥ 2 runs · {model.repeatFailures.length} unresolved</>
              : <>no test failed twice in this window</>
            }
            isFirst
          />
          <KpiCell
            Icon={ShieldCheck}
            label="Flaky tests"
            value={model.flakyCount}
            tone={model.flakyCount === 0 ? 'good' : model.flakyCount <= 2 ? 'warn' : 'bad'}
            meta={model.flakyCount === 0
              ? <>100% deterministic · positive signal</>
              : <>{model.flakyCount} test{model.flakyCount === 1 ? '' : 's'} intermittent</>
            }
          />
          <KpiCell
            Icon={AlertTriangle}
            label="Uncategorized"
            value={model.totalCategorised === 0 ? '—' : `${Math.round(model.uncategorizedPct)}%`}
            tone={model.uncategorizedPct >= 80 ? 'bad' : model.uncategorizedPct >= 50 ? 'warn' : 'good'}
            meta={model.totalCategorised === 0
              ? <>no failures classified yet</>
              : model.unknownCount > 0
                ? <>{model.unknownCount} of {model.totalCategorised} failure{model.totalCategorised === 1 ? '' : 's'}</>
                : <>classifier matched every failure</>
            }
          />
          <KpiCell
            label="Total executions"
            value={model.totalRuns}
            meta={<>{model.passedRuns} passed · {model.failedRuns} failed · {model.skippedRuns} skipped</>}
          />
          <KpiCell
            label="Mean time to fix"
            value={model.failedRuns === 0 ? '—' : '—'}
            tone={model.failedRuns === 0 ? 'good' : 'warn'}
            meta={model.failedRuns === 0
              ? <>no open failures</>
              : <>open since first detection</>
            }
            isLast
          />
        </section>
      )}

      <div className="grid gap-3.5 body-grid" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          <WhatsFailingCard
            topFailingTest={model.topFailingTest}
            totalRuns={model.totalRuns}
            trend={trend}
            onBisect={() => toast('Bisect modal — coming in Phase 2', { icon: '🪓' })}
            onMute={() => toast('Mute modal — coming in Phase 2', { icon: '🔕' })}
          />
          <FailureCategoryCard
            categories={categories}
            totalFailures={model.failedRuns}
            uncategorizedPct={model.uncategorizedPct}
          />
          {comparing && (
            comparison ? (
              <ComparisonStrip
                current={comparison.current}
                prior={comparison.prior}
                windowDays={days}
                suiteName={selectedSuite || null}
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
          <FailureTimeline trend={trend} days={days} />
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <FlakinessCard flaky={flaky} repeatFailures={model.repeatFailures} />
          <RecommendedActions recs={recs} />
        </div>
      </div>

      <ProvenanceFooter model={model} refreshedAt={refreshedAt} />

      {showPicker && (
        <WidgetPicker
          page="failures"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>

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
              {selectedSuite && <> in <code className="font-mono">{selectedSuite}</code></>} that
              has no category yet will be tagged with the selected category.
            </p>

            <div className="mt-4 space-y-2">
              {([
                { id: 'FLAKY',             label: 'Flaky',              desc: 'Intermittent — passes on retry; race or fixture issue.' },
                { id: 'PRODUCT_BUG',       label: 'Product Bug',        desc: 'Regression in the product under test.' },
                { id: 'INFRASTRUCTURE',    label: 'Infrastructure',     desc: 'Environment / network / platform failure.' },
                { id: 'TEST_DATA',         label: 'Test Data',          desc: 'Bad fixture, missing seed, stale snapshot.' },
                { id: 'AUTOMATION_DEFECT', label: 'Automation Defect',  desc: 'Test code is broken, not the product.' },
              ] as const).map(c => (
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

// Keep a few Phase-2 imports referenced so re-introducing them doesn't trip
// unused-imports.
void FileText; void TestTube
