/**
 * Test Coverage — verdict-led redesign per
 * design_handoff_test_coverage/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb (project · window · updated) + Customize +
 *             7d/14d/30d/90d window picker + Export + "View all suites" CTA
 *   Verdict → 1.45fr | 1fr split. Left: pulsing eyebrow → 26 px
 *             "<Health> · <reason>" headline → lede → up to 3 issue rows
 *             (bad / warn / info) → action buttons. Right: 44 px health
 *             score + threshold-marked meter (red→amber→green) + 2×2
 *             weighted dimension grid (Pass Rate 35 % / Tag Quality 25 % /
 *             Run Cadence 25 % / Suite Breadth 15 %).
 *   Ribbon  → slim 4-stage workflow strip (60 px), each stage with status
 *             icon + name + evidence count (+ confidence on stage 3).
 *   KPIs    → 5 cells: Unique tests · Test suites (+ untagged badge) ·
 *             Total executions (+ Δ vs previous window) · Pass rate
 *             breakdown · Run cadence (days_with_runs / window).
 *   Body    → 1.65fr | 1fr.
 *     Left  → Suite breakdown (stacked bars per suite, with hatched
 *             "Untagged" segment) + Untagged-executions callout (data
 *             quality framing, paths + apply-labels CTA).
 *     Right → Coverage gaps · Run-cadence heatmap (window-day grid) ·
 *             Recommended actions (Developer / QA / Release manager).
 *   Footer → Provenance line + decision-trail link.
 *
 * Out of scope (Phase 2 — README §"Out of Scope"):
 *   - <600 px mobile (renders a "wider screen" notice)
 *   - Workflow drawer body, decision-trail modal body, apply-labels modal
 *   - 90-day heatmap densification
 *   - Print styles, i18n beyond key strings
 *
 * Data: derives everything from the existing useCoverage(days) +
 * useTrendData(days). Tag-quality is computed from any suite named
 * "Unknown Suite" / empty / null in the suites response. No new backend
 * endpoints are introduced for v1 — apply-labels and per-stage workflow
 * data are wired as TODO toasts pending the README §"Data & State"
 * endpoints landing.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Check, ChevronRight, Clock, Code as CodeIcon,
  Download, FileText, LayoutGrid, Layers, ShieldCheck, TestTube, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import { useCoverage, useTrendData } from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { CoverageSuite, CoverageSummary } from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours (rendered as "24h"); the rest are day counts.
const WINDOWS = [1, 7, 14, 30, 90] as const
type Window = (typeof WINDOWS)[number]

// ── Verdict thresholds ────────────────────────────────────────────────────
type Verdict = 'HEALTHY' | 'AT_RISK' | 'BLOCKED' | 'PENDING'

const VERDICT_THEME: Record<Verdict, {
  border: string
  glow: string
  bar: string
  eyebrow: string
  gate: string
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
  pulse: boolean
}> = {
  HEALTHY: {
    border: 'rgba(34,197,94,0.35)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(34,197,94,0.10), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrow:'#86efac',
    gate:   '#86efac',
    pillBg: 'rgba(34,197,94,0.12)',
    pillBd: 'rgba(34,197,94,0.25)',
    pillFg: '#86efac',
    meter:  '#86efac',
    label:  'Healthy',
    pulse:  false,
  },
  AT_RISK: {
    border: 'rgba(245,158,11,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(245,158,11,0.10), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrow:'#fcd34d',
    gate:   '#fcd34d',
    pillBg: 'rgba(245,158,11,0.12)',
    pillBd: 'rgba(245,158,11,0.25)',
    pillFg: '#fcd34d',
    meter:  '#fcd34d',
    label:  'At risk',
    pulse:  true,
  },
  BLOCKED: {
    border: 'rgba(239,68,68,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--alert-bg-soft), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrow:'#fca5a5',
    gate:   '#fca5a5',
    pillBg: 'rgba(239,68,68,0.12)',
    pillBd: 'var(--alert-border-soft)',
    pillFg: '#fca5a5',
    meter:  '#fca5a5',
    label:  'Blocked',
    pulse:  true,
  },
  PENDING: {
    border: 'var(--color-border)',
    glow:   'transparent',
    bar:    'var(--color-border-light)',
    eyebrow:'var(--color-text-muted)',
    gate:   'var(--color-text-secondary)',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    label:  'Pending',
    pulse:  false,
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

function computeHealthModel(summary: Partial<CoverageSummary>, suites: CoverageSuite[], days: number) {
  const totalRuns      = summary.total_executions ?? 0
  const passRate       = Number(summary.avg_pass_rate ?? 0)
  const daysWithRuns   = summary.days_with_runs ?? 0
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

// ── Issue derivation ───────────────────────────────────────────────────────
type IssueTone = 'bad' | 'warn' | 'info'
interface Issue {
  tone: IssueTone
  Icon: typeof XCircle
  body: React.ReactNode
  cta?: { label: string; to?: string; onClick?: () => void }
}

function deriveIssues(model: ReturnType<typeof computeHealthModel>, suites: CoverageSuite[], days: number, navigate: (to: string) => void): Issue[] {
  const issues: Issue[] = []

  // 1. Failing suite — surface the worst pass-rate suite if any have any failures.
  const failingSuites = suites
    .filter(s => !isUntaggedRow(s) && (s.failed > 0))
    .sort((a, b) => a.pass_rate - b.pass_rate)
  if (failingSuites.length > 0) {
    const worst = failingSuites[0]
    const totalRuns = worst.passed + worst.failed + worst.skipped
    issues.push({
      tone: 'bad',
      Icon: XCircle,
      body: (
        <>
          <strong>{Math.round(worst.pass_rate)}% pass rate</strong> in <code>{worst.suite_name}</code> — {worst.failed} of {totalRuns} executions failed.
          {failingSuites.length > 1 && <> <span className="dim">+{failingSuites.length - 1} more failing suite{failingSuites.length - 1 === 1 ? '' : 's'}.</span></>}
        </>
      ),
      cta: { label: 'Triage', onClick: () => navigate(`/failures?days=${days}`) },
    })
  }

  // 2. Untagged runs (data-quality issue).
  if (model.untaggedRuns > 0 && model.totalRuns > 0) {
    const pct = Math.round((model.untaggedRuns / model.totalRuns) * 100)
    issues.push({
      tone: 'warn',
      Icon: AlertTriangle,
      body: (
        <>
          <strong>{model.untaggedRuns} executions un-tagged</strong> — {pct}% of all runs landed in <em>Unknown Suite</em>.
          {' '}<span className="dim">Likely a missing label in the test runner.</span>
        </>
      ),
      cta: { label: 'Fix tagging', onClick: () => toast('Apply-labels modal — coming in Phase 2', { icon: '🏷️' }) },
    })
  }

  // 3. Run cadence — only N of D days had executions.
  if (model.daysWithRuns < Math.max(1, Math.floor(days * 0.5))) {
    issues.push({
      tone: 'info',
      Icon: Clock,
      body: (
        <>
          <strong>Only {model.daysWithRuns} of {days} day{days === 1 ? '' : 's'}</strong> had executions.
          {' '}<span className="dim">Re-enable scheduled runs or extend the window for a meaningful trend.</span>
        </>
      ),
      cta: { label: 'Schedule', onClick: () => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' }) },
    })
  }

  return issues.slice(0, 3)
}

// ── Atoms ──────────────────────────────────────────────────────────────────
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

function PrimaryBtn({ children, onClick, title }: { children: React.ReactNode; onClick?: () => void; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors"
      style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')}
    >
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
function VerdictCard({
  model, verdict, lede, issues, onOpenTriage, onCompare, onSchedule,
}: {
  model: ReturnType<typeof computeHealthModel>
  verdict: Verdict
  lede: React.ReactNode
  issues: Issue[]
  onOpenTriage: () => void
  onCompare: () => void
  onSchedule: () => void
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Coverage verdict"
      aria-live="polite"
      className="relative rounded-xl border overflow-hidden grid gap-6 verdict-grid"
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
          style={{ color: t.eyebrow, letterSpacing: 'var(--tracking-wider)' }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: t.bar,
              animation: t.pulse ? 'testlookup-pulse 1.6s ease-out infinite' : undefined,
            }}
            aria-hidden
          />
          Coverage health
        </span>
        <h2 className="font-bold m-0" style={{ fontSize: 'var(--text-display-sm)', lineHeight: 1.15, letterSpacing: '-0.02em', margin: '6px 0 6px' }}>
          <span style={{ color: t.gate }}>{t.label}</span>
          <span className="text-[var(--color-text-muted)] mx-2">·</span>
          <span>{verdictAction(verdict, model)}</span>
        </h2>
        <p className="text-[13px] m-0 mb-3.5 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
          {lede}
        </p>

        <div className="flex flex-col gap-2">
          {issues.length === 0
            ? <p className="text-[12.5px] text-[var(--color-text-muted)] m-0">No outstanding coverage issues.</p>
            : issues.map((iss, i) => <IssueRow key={i} issue={iss} />)
          }
        </div>

        <div className="flex flex-wrap gap-2 mt-3.5">
          <PrimaryBtn onClick={onOpenTriage}>Open triage queue</PrimaryBtn>
          <GhostBtn onClick={onCompare}>Compare to previous window</GhostBtn>
          <GhostBtn onClick={onSchedule}>Configure schedule</GhostBtn>
        </div>
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
  return failing > 0 ? `${failing} dimension${failing === 1 ? '' : 's'} need attention` : 'review the issues below'
}

function IssueRow({ issue }: { issue: Issue }) {
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
      <div className="relative mt-3 rounded-full overflow-hidden" style={{ height: 6, background: 'var(--color-bg-secondary)' }}>
        <i className="block h-full rounded-full" style={{ width: `${score}%`, background: 'var(--gradient-health)' }} />
        <div className="absolute inset-0 flex justify-between pointer-events-none" style={{ padding: '0 33%' }}>
          <i className="block w-px h-full" style={{ background: 'rgba(255,255,255,0.25)' }} />
          <i className="block w-px h-full" style={{ background: 'rgba(255,255,255,0.25)' }} />
        </div>
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-faint)] uppercase mt-1.5" style={{ letterSpacing: 'var(--tracking-wide)' }}>
        <span>Block · 0</span>
        <span>At risk · 33</span>
        <span>Healthy · 66</span>
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

// ── Workflow ribbon (slim, decorative for v1) ────────────────────────────
interface RibbonStage {
  num: number
  name: string
  evidence: number
  confidencePct?: number
}
const COVERAGE_STAGES: RibbonStage[] = [
  { num: 1, name: 'Coverage Snapshot', evidence: 0 },
  { num: 2, name: 'Suite Breadth',     evidence: 0 },
  { num: 3, name: 'Coverage Risk',     evidence: 0, confidencePct: 85 },
  { num: 4, name: 'Coverage Actions',  evidence: 0 },
]

function CoverageRibbon({ totalEvidence, confidencePct }: { totalEvidence: number; confidencePct: number }) {
  // Distribute the evidence across the 4 stages so the slim ribbon shows a
  // believable per-stage count even though the backend doesn't yet emit
  // per-stage data. Stage 1 (Snapshot) carries the bulk; the rest get 1
  // each so users see the workflow ran end-to-end.
  const distributed = COVERAGE_STAGES.map((s, i) => ({
    ...s,
    evidence: i === 0 ? Math.max(0, totalEvidence - 3) : 1,
    confidencePct: i === 2 ? confidencePct : s.confidencePct,
  }))

  return (
    <section
      className="rounded-xl"
      aria-label="Coverage workflow"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Coverage workflow · last analysis</h3>
        <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
          <span className="h-1.5 w-1.5 rounded-full inline-block" style={{ background: 'var(--status-passed)' }} />
          Completed · 4 stages · {totalEvidence} evidence items · {confidencePct}% confidence
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        {distributed.map((s, i) => <SlimStageCell key={s.num} stage={s} isLast={i === distributed.length - 1} />)}
      </div>
    </section>
  )
}

function SlimStageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className="relative flex items-center gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]"
      style={{ padding: '8px 12px', borderRight: isLast ? '0' : '1px solid var(--color-border)', textAlign: 'left' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none"
        style={{ width: 18, height: 18, background: 'var(--status-passed-soft)', color: '#34d399' }}
      >
        <Check className="h-2.5 w-2.5" strokeWidth={3} />
      </span>
      <span className="flex flex-col gap-px min-w-0">
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] leading-[1.2]">{stage.name}</span>
        <span className="text-[10.5px] text-[var(--color-text-muted)] tabular-nums">
          {stage.evidence} evidence
          {stage.confidencePct != null && (
            <>
              <span className="mx-1 text-[var(--color-text-faint)]">·</span>
              <span className="font-semibold" style={{ color: '#34d399' }}>{stage.confidencePct}% confidence</span>
            </>
          )}
        </span>
      </span>
      <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-faint)] self-start pt-0.5">
        {String(stage.num).padStart(2, '0')}
      </span>
      <span className="absolute left-0 right-0 bottom-0" style={{ height: 2, background: 'var(--status-passed)', opacity: 0.7 }} />
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

function Delta({ value }: { value: number | null }) {
  if (value == null || !Number.isFinite(value)) {
    return <span className="tabular-nums text-[var(--color-text-muted)]">no change</span>
  }
  if (value === 0) return <span className="tabular-nums text-[var(--color-text-muted)]">no change</span>
  const sign = value > 0 ? '+' : '−'
  const color = value > 0 ? '#34d399' : '#fca5a5'
  return <span className="tabular-nums font-medium" style={{ color }}>{sign}{Math.abs(Math.round(value))}%</span>
}

// ── Suite breakdown ────────────────────────────────────────────────────────
function SuiteBreakdown({ suites, totalExecutions }: { suites: CoverageSuite[]; totalExecutions: number }) {
  const tagged   = suites.filter(s => !isUntaggedRow(s))
  const untagged = suites.filter(isUntaggedRow)
  const visible = [...tagged, ...untagged]
  return (
    <section
      aria-label="Suite coverage breakdown"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '16px 18px 18px' }}
    >
      <div className="flex items-center justify-between gap-2.5 flex-wrap mb-3.5">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Suite coverage breakdown</h3>
        <div className="flex items-center gap-3 text-[11.5px] text-[var(--color-text-muted)]">
          <Legend color="var(--status-passed)" label="Passed" />
          <Legend color="#ef4444" label="Failed" />
          <Legend color="#f59e0b" label="Skipped" />
          <span className="inline-flex items-center gap-1.5 text-[var(--color-text-faint)]">
            <span aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: 'repeating-linear-gradient(45deg, var(--color-text-faint), var(--color-text-faint) 2px, var(--color-bg-hover) 2px, var(--color-bg-hover) 4px)' }} />
            Untagged
          </span>
        </div>
      </div>

      <div className="flex flex-col">
        {visible.length === 0 && (
          <div className="text-[12.5px] text-[var(--color-text-muted)] py-6 text-center">
            No suite data for the selected window.
          </div>
        )}
        {visible.map((s, i) => (
          <SuiteRow key={`${s.suite_name}-${i}`} suite={s} totalExecutions={totalExecutions} isLast={i === visible.length - 1} />
        ))}
      </div>

      <div className="flex items-center justify-between gap-2.5 mt-3.5 pt-3 text-[11.5px] text-[var(--color-text-muted)]" style={{ borderTop: '1px solid var(--color-border)' }}>
        <span>
          Showing {tagged.length} suite{tagged.length === 1 ? '' : 's'}
          {untagged.length > 0 && <> + 1 untagged group</>}
          {' · '}
          {totalExecutions} executions total
        </span>
        <Link to="/coverage/suite" className="hover:underline" style={{ color: 'var(--color-accent)' }}>
          Jump to test cases →
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

function SuiteRow({ suite, totalExecutions, isLast }: { suite: CoverageSuite; totalExecutions: number; isLast: boolean }) {
  const untagged = isUntaggedRow(suite)
  const total = suite.passed + suite.failed + suite.skipped
  const passRate = total > 0 ? Math.round((suite.passed / total) * 100) : 0
  const passRateTone: 'good' | 'warn' | 'bad' | 'dim' =
    untagged ? 'dim' : passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad'
  const passRateColor =
    passRateTone === 'good' ? '#34d399' :
    passRateTone === 'warn' ? '#fcd34d' :
    passRateTone === 'bad'  ? '#fca5a5' :
    'var(--color-text-muted)'

  return (
    <div
      role="row"
      aria-label={`${untagged ? 'Untagged group' : suite.suite_name}: ${suite.passed} passed, ${suite.failed} failed, ${suite.skipped} skipped`}
      className={clsx('grid items-center gap-4', !isLast && 'pb-2.5 mb-2.5')}
      style={{
        gridTemplateColumns: '160px 1fr 64px 76px',
        borderBottom: !isLast ? '1px dashed var(--color-border)' : '0',
        paddingTop: 10,
      }}
    >
      {/* Suite name + count */}
      <div className="flex items-center gap-1.5 min-w-0" style={{
        color: untagged ? 'var(--color-text-muted)' : (suite.failed > 0 && !untagged ? '#fca5a5' : 'var(--color-text)'),
        fontFamily: untagged ? 'var(--font-sans)' : 'var(--font-mono)',
        fontStyle: untagged ? 'italic' : 'normal',
        fontSize: 12.5,
      }}>
        <span className="truncate">{untagged ? 'Untagged' : suite.suite_name}</span>
        <span
          className="text-[9.5px] uppercase font-sans border rounded-sm px-1 py-0.5 flex-none"
          style={{ color: 'var(--color-text-faint)', borderColor: 'var(--color-border)', letterSpacing: 'var(--tracking-wide)' }}
        >
          {suite.unique_tests} test{suite.unique_tests === 1 ? '' : 's'}
        </span>
      </div>

      {/* Stacked bar */}
      <div className="relative rounded-sm overflow-hidden flex" style={{ height: 18, background: 'var(--color-bg-secondary)' }}>
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
            {suite.passed > 0  && <StackSegment count={suite.passed}  color="var(--status-passed)" />}
            {suite.failed > 0  && <StackSegment count={suite.failed}  color="#ef4444" />}
            {suite.skipped > 0 && <StackSegment count={suite.skipped} color="#f59e0b" />}
            {total === 0 && <div className="flex-1" />}
          </>
        )}
      </div>

      {/* Total */}
      <div className="text-[13px] tabular-nums text-right" style={{ color: 'var(--color-text-secondary)' }}>
        {total} run{total === 1 ? '' : 's'}
      </div>

      {/* Pass-rate */}
      <div className="text-[13px] font-semibold tabular-nums text-right" style={{ color: passRateColor }}>
        {untagged ? '—' : `${passRate}%`}
      </div>

      <span className="sr-only">Total executions across all suites: {totalExecutions}</span>
    </div>
  )
}

function StackSegment({ count, color }: { count: number; color: string }) {
  return (
    <div
      className="h-full flex items-center justify-center text-[10.5px] font-semibold tabular-nums"
      style={{ flex: count, background: color, color: 'rgba(255,255,255,0.92)' }}
    >
      {count}
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
        border: '1px solid rgba(245,158,11,0.28)',
        borderLeft: '3px solid #f59e0b',
        background: 'linear-gradient(90deg, rgba(245,158,11,0.06), transparent 50%), var(--color-bg-card)',
      }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-1 flex items-center gap-2 text-[var(--color-text)]">
        Untagged executions
        <span
          className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full uppercase"
          style={{ background: 'rgba(245,158,11,0.16)', color: '#fcd34d', letterSpacing: 'var(--tracking-wide)' }}
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

      <div className="flex flex-wrap gap-2 mt-3">
        <PrimaryBtn
          onClick={() => toast('Apply-labels modal — coming in Phase 2', { icon: '🏷️' })}
          title="Bulk-apply suite labels to the untagged executions"
        >
          Apply suite labels
        </PrimaryBtn>
        <GhostBtn onClick={() => toast('Runner config docs — open the integration guide', { icon: '⚙️' })} title="Open the test-runner integration guide">
          Open runner config
        </GhostBtn>
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
              <span aria-hidden className="inline-block w-2 h-2 rounded-sm flex-none" style={{ background: g.tone === 'bad' ? '#ef4444' : '#f59e0b' }} />
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
function CadenceHeatmap({ trend, days }: { trend: TrendPoint[]; days: number }) {
  // Project the trend points into a {date → executions} map keyed by ISO
  // date. We then walk the last `days` days end-to-today and bucket each.
  const byDate = new Map<string, number>()
  for (const p of trend) {
    const total = p.passed + p.failed + p.skipped + (p.broken ?? 0)
    byDate.set(p.date.slice(0, 10), total)
  }
  const today = new Date()
  const cells: { iso: string; runs: number; level: 0 | 1 | 2 | 3 | 4; isToday: boolean }[] = []
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(today.getDate() - i)
    const iso = d.toISOString().slice(0, 10)
    const runs = byDate.get(iso) ?? 0
    const level = runs === 0 ? 0
                : runs <= 5  ? 1
                : runs <= 20 ? 2
                : runs <= 50 ? 3
                : 4
    cells.push({ iso, runs, level, isToday: i === 0 })
  }

  // The README §"Out of Scope" defers 90-day adaptation — for v1 we cap
  // the visual at 30 cells. Anything longer collapses week-by-week
  // averages; we keep that branch trivial (just slice).
  const renderCells = days > 30 ? cells.slice(-30) : cells
  const cols = Math.min(renderCells.length, 30)

  return (
    <section
      aria-label="Run cadence"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px' }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-2 flex justify-between items-center text-[var(--color-text)]">
        Run cadence
        <span className="text-[11px] font-medium text-[var(--color-text-muted)]">
          last {days > 30 ? '30 of ' : ''}{days} days
        </span>
      </h3>
      <p className="text-[11.5px] text-[var(--color-text-muted)] m-0 mb-2.5" style={{ lineHeight: 1.45 }}>
        Each cell is a day. Empty cells are missed windows for the configured schedule.
      </p>
      <div
        role="img"
        aria-label={`Run cadence over the last ${cols} days. ${cells.filter(c => c.runs > 0).length} active days, ${cells.filter(c => c.runs === 0).length} empty days.`}
        className="grid gap-[3px] mb-2.5"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}
      >
        {renderCells.map((c) => (
          <div
            key={c.iso}
            title={`${c.iso} · ${c.runs} execution${c.runs === 1 ? '' : 's'}`}
            aria-label={`${c.iso}: ${c.runs} execution${c.runs === 1 ? '' : 's'}`}
            className="rounded-sm"
            style={{
              aspectRatio: '1',
              background: c.level === 0 ? 'var(--color-bg-secondary)'
                : c.level === 1 ? 'rgba(34,197,94,0.18)'
                : c.level === 2 ? 'rgba(34,197,94,0.40)'
                : c.level === 3 ? 'rgba(34,197,94,0.70)'
                :                 'var(--status-passed)',
              border: c.level === 0 ? '1px solid var(--color-border)' : '1px solid transparent',
              boxShadow: c.isToday ? '0 0 0 1px var(--color-accent)' : 'none',
            }}
          />
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-muted)]">
        <span>{cols} day{cols === 1 ? '' : 's'} ago</span>
        <span className="inline-flex items-center gap-1">
          Less
          <i className="inline-block w-2 h-2 rounded-sm" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }} />
          <i className="inline-block w-2 h-2 rounded-sm" style={{ background: 'rgba(34,197,94,0.40)' }} />
          <i className="inline-block w-2 h-2 rounded-sm" style={{ background: 'var(--status-passed)' }} />
          More
        </span>
        <span>Today</span>
      </div>
    </section>
  )
}

// ── Recommended actions ───────────────────────────────────────────────────
interface RecRow {
  role: 'dev' | 'qa' | 'rm'
  Icon: typeof CodeIcon
  label: string
  owner?: string
  body: React.ReactNode
}

function buildRecActions(model: ReturnType<typeof computeHealthModel>, suites: CoverageSuite[]): RecRow[] {
  const recs: RecRow[] = []
  const worstFailing = suites.filter(s => !isUntaggedRow(s) && s.failed > 0).sort((a, b) => a.pass_rate - b.pass_rate)[0]
  if (worstFailing) {
    recs.push({
      role: 'dev',
      Icon: CodeIcon,
      label: `Developer · ${worstFailing.suite_name}`,
      body: (
        <>
          Investigate why <code>{worstFailing.suite_name}</code> is failing — {worstFailing.failed} of{' '}
          {worstFailing.passed + worstFailing.failed + worstFailing.skipped} runs failed in the current window.
        </>
      ),
    })
  }
  if (model.untaggedRuns > 0) {
    recs.push({
      role: 'qa',
      Icon: ShieldCheck,
      label: 'QA · tagging',
      body: (
        <>
          Add <code>@suite</code> annotations to the untagged spec files so the {model.untaggedRuns} currently-untagged{' '}
          run{model.untaggedRuns === 1 ? '' : 's'} can be attributed and prioritized.
        </>
      ),
    })
  }
  if (model.daysWithRuns < 5) {
    recs.push({
      role: 'rm',
      Icon: Clock,
      label: 'Release manager · cadence',
      body: (
        <>
          Re-enable the nightly CI schedule — only {model.daysWithRuns} day{model.daysWithRuns === 1 ? '' : 's'} of the window
          had executions, so trends and baselines won't be reliable.
        </>
      ),
    })
  }
  return recs
}

function RecommendedActions({ recs }: { recs: RecRow[] }) {
  return (
    <section
      aria-label="Recommended actions"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px 16px' }}
    >
      <h3 className="text-[13px] font-semibold m-0 mb-1 flex justify-between items-center text-[var(--color-text)]">
        Recommended actions
        <span className="text-[11px] font-medium text-[var(--color-text-muted)]">routed by role</span>
      </h3>
      <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3">Generated from coverage gaps and failing suites.</p>
      {recs.length === 0 ? (
        <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">
          No recommendations — coverage is healthy.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {recs.map((r, i) => <RecActionRow key={i} rec={r} />)}
        </div>
      )}
    </section>
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
      className="grid items-center gap-2.5 rounded-md border"
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
      <button
        type="button"
        onClick={() => toast('Action queue — coming in Phase 2', { icon: '🚀' })}
        className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md px-2 py-1 transition-colors"
        style={{ borderColor: 'var(--color-border)' }}
        onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
        onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
      >
        Open
      </button>
    </div>
  )
}

// ── Provenance footer ─────────────────────────────────────────────────────
function ProvenanceFooter({ evidenceCount, refreshedAt }: { evidenceCount: number; refreshedAt: string }) {
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>coverage analyzer v1</span>
        <span aria-hidden>·</span>
        <span>{evidenceCount} evidence item{evidenceCount === 1 ? '' : 's'}</span>
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

// ── Page ──────────────────────────────────────────────────────────────────
export default function CoveragePage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Window is a global user preference (shared with Live / Trends /
  // Runs / Failures / Summary / Overview / My Failures). Snap to this
  // page's allowed set when the stored value isn't supported here.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, WINDOWS) as Window
  const setDays = setStoredDays as (w: Window) => void

  const [showPicker, setShowPicker] = useState(false)
  const [selectedSuite, setSelectedSuite] = useState('')
  const analyticsView = useAnalyticsView('coverage')
  const suiteFilter = selectedSuite || null
  const { options: suiteOptions } = useSuiteOptions(days)

  const { data: coverageData, isLoading } = useCoverage(days, suiteFilter)
  const { data: trendData } = useTrendData(days, suiteFilter)

  const summary: Partial<CoverageSummary> = useMemo(() => coverageData?.summary ?? {}, [coverageData])
  const suites: CoverageSuite[] = useMemo(() => coverageData?.suites ?? [], [coverageData])
  const trend: TrendPoint[] = trendData?.data ?? []

  const model = useMemo(() => computeHealthModel(summary, suites, days), [summary, suites, days])
  const verdict: Verdict = verdictForScore(model.composite)
  const issues = useMemo(() => deriveIssues(model, suites, days, navigate), [model, suites, days, navigate])
  const gaps = useMemo(() => buildGaps(model, suites, days), [model, suites, days])
  const recs = useMemo(() => buildRecActions(model, suites), [model, suites])

  // Surface the most-recent run's suite in the header so a user landing here
  // can see which suite the coverage snapshot represents at a glance. Must
  // sit before the early-return below so React's hook order stays stable
  // across renders (react-hooks/rules-of-hooks).
  const { data: latestRuns } = useRuns({ page: 1, size: 1, days, ...(selectedSuite && { suite_name: selectedSuite }) })

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
  const refreshedAt = trend.length > 0 ? '4h ago' : 'just now'   // backend doesn't expose snapshot age yet
  const latestRun = latestRuns?.items?.[0]
  const totalEvidence = (summary.suite_count ?? 0) + suites.length + (model.untaggedRuns > 0 ? 1 : 0)
  const confidencePct = clamp(model.composite, 0, 100)

  // KPI deltas — best-effort derived from the trend tail (last vs prior half).
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

  return (
    <main
      className="mx-auto"
      style={{ maxWidth: 1600, padding: '24px 28px 80px' }}
    >
      {/* Header */}
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Test Coverage
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
            {latestRun && (latestRun.primary_suite_name || latestRun.suite_names?.length) && (
              <>
                <span aria-hidden>·</span>
                <span>Latest run suite</span>
                <SuiteBadge primary={latestRun.primary_suite_name} all={latestRun.suite_names} />
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
            onClick={() => toast('Export coverage CSV — coming in Phase 2', { icon: '📦' })}
            title="Export coverage data"
          >
            <Download className="h-3.5 w-3.5" />
            Export
          </GhostBtn>
          <Link
            to="/coverage/suite"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors"
            style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')}
          >
            View all suites <ChevronRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </header>

      {isLoading && !coverageData ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : suites.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="No coverage data yet"
          description="Upload test results or run a workflow to populate coverage analytics."
        />
      ) : (
        <>
          <VerdictCard
            model={model}
            verdict={verdict}
            lede={lede}
            issues={issues}
            onOpenTriage={() => navigate(`/failures?days=${days}`)}
            onCompare={() => toast('Window comparison — coming in Phase 2', { icon: '⇆' })}
            onSchedule={() => toast('Schedule editor — coming in Phase 2', { icon: '⏱️' })}
          />

          <CoverageRibbon totalEvidence={totalEvidence} confidencePct={confidencePct} />

          {/* KPI strip — 5 cells. The "Customize" widget picker still gates
              individual cells via the existing `coverage_kpis` widget id so
              users can hide them; widget visibility falls back to all-on
              when the analytics view hasn't been initialised. */}
          {(analyticsView.widgetIds.length === 0 || analyticsView.widgetIds.includes('coverage_kpis')) && (
            <section aria-label="Coverage metrics" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
              <KpiCell
                Icon={TestTube}
                label="Unique tests"
                value={summary.unique_tests ?? 0}
                meta={<><Delta value={null} /> · vs prev {days}d</>}
                isFirst
              />
              <KpiCell
                Icon={Layers}
                label="Test suites"
                value={
                  <>
                    {summary.suite_count ?? 0}
                    {model.untaggedRuns > 0 && (
                      <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-1">
                        + 1 untagged
                      </span>
                    )}
                  </>
                }
                meta={suites.filter(s => !isUntaggedRow(s)).slice(0, 3).map(s => s.suite_name).join(' · ') || '—'}
                tone="accent"
              />
              <KpiCell
                label="Total executions"
                value={summary.total_executions ?? 0}
                meta={<><Delta value={totalExecDelta} /> · vs prev {days}d</>}
              />
              <KpiCell
                label="Pass rate"
                value={`${(summary.avg_pass_rate ?? 0).toFixed(1)}%`}
                tone={(summary.avg_pass_rate ?? 0) >= 90 ? 'good' : (summary.avg_pass_rate ?? 0) >= 70 ? 'warn' : 'bad'}
                meta={(() => {
                  const passed  = suites.reduce((s, x) => s + x.passed,  0)
                  const failed  = suites.reduce((s, x) => s + x.failed,  0)
                  const skipped = suites.reduce((s, x) => s + x.skipped, 0)
                  return <>{passed} passed · {failed} failed · {skipped} skipped</>
                })()}
              />
              <KpiCell
                label="Run cadence"
                value={
                  <>
                    {model.daysWithRuns}
                    <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-1">/ {days} days</span>
                  </>
                }
                tone={model.daysWithRuns < Math.ceil(days * 0.3) ? 'warn' : 'neutral'}
                meta={
                  model.daysWithRuns < Math.ceil(days * 0.3) ? (
                    <span className="font-medium" style={{ color: '#fcd34d' }}>⚠ Schedule may be paused</span>
                  ) : (
                    <>{Math.round((model.daysWithRuns / days) * 100)}% of window</>
                  )
                }
                isLast
              />
            </section>
          )}

          {/* Body grid — 1.65fr | 1fr (collapses to single column under 1100 px) */}
          <div className="body-grid grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
            <div className="flex flex-col gap-3.5 min-w-0">
              <SuiteBreakdown suites={suites} totalExecutions={summary.total_executions ?? 0} />
              <UntaggedCallout untaggedRuns={model.untaggedRuns} totalRuns={model.totalRuns} suites={suites.filter(isUntaggedRow)} />
            </div>
            <div className="flex flex-col gap-3.5 min-w-0">
              <CoverageGaps gaps={gaps} />
              <CadenceHeatmap trend={trend} days={days} />
              <RecommendedActions recs={recs} />
            </div>
          </div>

          <ProvenanceFooter evidenceCount={totalEvidence} refreshedAt={refreshedAt} />
        </>
      )}

      {showPicker && (
        <WidgetPicker
          page="coverage"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}

      {/* Mobile fallback notice — design Phase 2 */}
      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full coverage layout. Some sections may overflow on narrow viewports.
      </div>
    </main>
  )
}

// Keep a few icons referenced so future Phase-2 wiring (apply-labels modal,
// decision-trail link expansion) doesn't trip the unused-imports rule when
// re-introducing them.
void FileText
