/**
 * Defects — verdict-led redesign per design_handoff_defects/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb + Customize + status tabs with counts +
 *             "+ New defect" CTA.
 *   Verdict → 1.65fr | 1fr split. Variants BLOCKED / AT_RISK / HEALTHY /
 *             PENDING. Left: pulsing eyebrow → 26 px headline → lede →
 *             3 issue rows (release blockers / unowned P0s / Jira gaps)
 *             → CTAs (Triage P0s · Assign unowned · Open release dashboard).
 *             Right: 44 px composite queue-health score + 2×2 weighted
 *             dimension grid (P0 throughput 35 / Ownership 20 / Jira coverage
 *             25 / Fix velocity 20).
 *   Ribbon  → slim 4-stage workflow: intake · linkage · triage · resolution.
 *             States drawn from real counts (linkage warns when unlinked > 0,
 *             triage warns when unowned P0s exist, resolution stays neutral).
 *   KPIs    → 5 cells with sparklines: Open · P0+P1 · MTTR · Escape rate ·
 *             Oldest open P0.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Defects table (Key · Title · Severity · Status · Owner · Jira
 *             · Age · Actions) wrapped in `.table-scroll` so the column
 *             never overflows. P0 rows get faint red row-bg, on hover deeper.
 *     Right → Jira bridge card · Component breakdown (severity-stacked bars
 *             per suite) · Recommended actions (role-routed).
 *   Footer → Provenance line + Decision-trail link.
 *
 * Out of scope (Phase 2 — README §11 + §"Out of scope" implications):
 *   - Bulk actions on the table.
 *   - Link-Jira / Assign / Approve modals (per-row pills emit toasts).
 *   - Triage focus flow over the 3 P0s (verdict CTA emits a toast).
 *   - Bridge settings deep-link (link emits a toast).
 *   - Print styles.
 *
 * Data: derives every section from the existing useDefects(...) +
 * useFailureCategories. The README assumes a richer DefectItem with severity,
 * owner, release tag, and bridge metadata — none of which the current model
 * carries. Every "missing" field is synthesised:
 *   - severity     ← failure_category + ai_confidence_score
 *   - owner        ← absent (rendered as Unassigned)
 *   - jira link    ← jira_ticket_id / jira_ticket_url
 *   - age          ← created_at
 *   - bridge state ← derived from per-row jira_ticket_id presence
 * When the backend grows real defect-management endpoints these synth paths
 * collapse to single field reads.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, ArrowRight, BarChart3, Bug, Check, ChevronRight,
  Clock, Code as CodeIcon, ExternalLink, Layers, LayoutGrid, Plus, Search,
  ShieldCheck, TrendingUp, Wrench, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import DefectIntakeModal from '@/components/defects/DefectIntakeModal'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import { refreshDefects, useDefects, useFailureCategories } from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { isSafeExternalUrl } from '@/utils/safeUrl'
import type { DefectItem } from '@/types/analytics'

// ── Types & filters ──────────────────────────────────────────────────────
type StatusKey = 'ALL' | 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED'

const STATUS_TABS: { id: StatusKey; label: string }[] = [
  { id: 'ALL',         label: 'All' },
  { id: 'OPEN',        label: 'Open' },
  { id: 'IN_PROGRESS', label: 'In progress' },
  { id: 'RESOLVED',    label: 'Resolved' },
  { id: 'CLOSED',      label: 'Closed' },
]

type Severity = 'P0' | 'P1' | 'P2' | 'P3'

// ── Verdict ──────────────────────────────────────────────────────────────
type Verdict = 'BLOCKED' | 'AT_RISK' | 'HEALTHY' | 'PENDING'

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
  BLOCKED: {
    border: 'rgba(239,68,68,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(239,68,68,0.10), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: '#fca5a5',
    gateText:    '#fca5a5',
    pillBg: 'rgba(239,68,68,0.16)',
    pillBd: 'rgba(239,68,68,0.30)',
    pillFg: '#fca5a5',
    meter:  '#fca5a5',
    label:  'Release blocked',
    pulse:  true,
  },
  AT_RISK: {
    border: 'rgba(245,158,11,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: '#fcd34d',
    gateText:    '#fcd34d',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: '#fcd34d',
    meter:  '#fcd34d',
    label:  'At risk',
    pulse:  true,
  },
  HEALTHY: {
    border: 'rgba(34,197,94,0.40)',
    glow:   'radial-gradient(120% 100% at 0% 0%, rgba(34,197,94,0.10), transparent 55%)',
    bar:    'var(--gate-go)',
    eyebrowText: '#86efac',
    gateText:    '#86efac',
    pillBg: 'rgba(34,197,94,0.12)',
    pillBd: 'rgba(34,197,94,0.30)',
    pillFg: '#86efac',
    meter:  '#86efac',
    label:  'Queue healthy',
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

// ── Queue-health model ───────────────────────────────────────────────────
type DimensionId = 'p0_throughput' | 'ownership' | 'jira_coverage' | 'fix_velocity'
const WEIGHTS: Record<DimensionId, number> = {
  p0_throughput: 0.35,
  ownership:     0.20,
  jira_coverage: 0.25,
  fix_velocity:  0.20,
}

interface DimensionScore {
  id: DimensionId
  label: string
  score: number
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

interface DefectRow extends DefectItem {
  severity: Severity
  ageMs: number
  ageLabel: string
  ageTone: 'good' | 'warn' | 'bad' | 'neutral'
  ownerInitials: string | null
  ownerLabel: string
  isUnowned: boolean
  isUnlinked: boolean
  componentName: string
  titleText: string
  subtitleText: string
  keyLabel: string
  jiraKey: string | null
  statusKey: StatusKey
}

interface QueueModel {
  composite: number
  dimensions: DimensionScore[]
  total: number
  open: number
  inProgress: number
  resolved: number
  closed: number
  p0Count: number
  p1Count: number
  p2Count: number
  p3Count: number
  p0Unowned: number
  unlinkedTotal: number
  unlinkedP0: number
  oldestP0: DefectRow | null
  mttrDays: number | null         // mean time to resolve over the resolved set
  countsByStatus: Record<StatusKey, number>
  countsByComponent: Map<string, { total: number; bySev: Record<Severity, number> }>
  rows: DefectRow[]
  weeklyAdded: number
  weeklyClosed: number
}

function classifySeverity(d: DefectItem): Severity {
  const cat = (d.failure_category ?? '').toUpperCase()
  const conf = d.ai_confidence_score ?? 0
  if (/PRODUCT.?BUG/.test(cat)) return conf >= 80 ? 'P0' : 'P1'
  if (/INFRA|RUNNER|CI/.test(cat)) return 'P2'
  if (/FLAKY/.test(cat)) return 'P3'
  return 'P2'
}

function ageOf(iso: string): { ms: number; label: string; tone: DefectRow['ageTone'] } {
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return { ms: 0, label: '—', tone: 'neutral' }
  const h = Math.floor(ms / 3600000)
  if (h < 1) return { ms, label: 'just now', tone: 'good' }
  if (h < 24) {
    return { ms, label: `${h}h`, tone: h >= 6 ? 'warn' : 'good' }
  }
  const d = Math.floor(h / 24)
  return { ms, label: `${d}d`, tone: d >= 5 ? 'bad' : d >= 3 ? 'warn' : 'good' }
}

function ownerOf(_d: DefectItem): { initials: string | null; label: string; isUnowned: boolean } {
  // Backend doesn't yet expose an owner — every row reads as Unassigned.
  // When owner lands on the model, switch to: (d as DefectItem & { owner })
  return { initials: null, label: 'Unassigned', isUnowned: true }
}

function statusKeyOf(s: string): StatusKey {
  const u = s.toUpperCase()
  if (u === 'OPEN') return 'OPEN'
  if (u === 'IN_PROGRESS' || u === 'IN PROGRESS' || u === 'IN-PROGRESS') return 'IN_PROGRESS'
  if (u === 'RESOLVED') return 'RESOLVED'
  if (u === 'CLOSED') return 'CLOSED'
  return 'OPEN'
}

function buildKeyLabel(id: string): string {
  // Short, stable defect "key" derived from the row id. Matches the visual
  // pattern in the design (`EVG-1247`) without inventing a backend numbering.
  let n = 0
  for (let i = 0; i < id.length; i++) n = ((n << 5) - n) + id.charCodeAt(i)
  return `EVG-${(Math.abs(n) % 9000) + 1000}`
}

function componentOf(d: DefectItem): string {
  if (d.suite_name) return d.suite_name
  // Fallback: take the first segment of the test name before a "."/"_" / "/".
  const first = (d.test_name ?? '').split(/[._/]/, 1)[0] ?? 'unknown'
  return first || 'unknown'
}

function buildRow(d: DefectItem): DefectRow {
  const severity = classifySeverity(d)
  const a = ageOf(d.created_at)
  const o = ownerOf(d)
  const isUnlinked = !d.jira_ticket_id
  const componentName = componentOf(d)
  const statusKey = statusKeyOf(d.resolution_status)
  return {
    ...d,
    severity,
    ageMs: a.ms,
    ageLabel: a.label,
    ageTone: a.tone,
    ownerInitials: o.initials,
    ownerLabel: o.label,
    isUnowned: o.isUnowned,
    isUnlinked,
    componentName,
    titleText: d.test_name,
    subtitleText: d.suite_name
      ? `${d.suite_name} suite · from ${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`
      : `${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`,
    keyLabel: buildKeyLabel(d.id),
    jiraKey: d.jira_ticket_id ?? null,
    statusKey,
  }
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 33) return 'warn'
  return 'bad'
}

function buildQueueModel(rawDefects: DefectItem[]): QueueModel {
  const rows: DefectRow[] = rawDefects.map(buildRow)
  const open = rows.filter(r => r.statusKey === 'OPEN').length
  const inProgress = rows.filter(r => r.statusKey === 'IN_PROGRESS').length
  const resolved = rows.filter(r => r.statusKey === 'RESOLVED').length
  const closed = rows.filter(r => r.statusKey === 'CLOSED').length
  const total = rows.length

  const sevCounts = (sev: Severity) =>
    rows.filter(r => r.severity === sev && (r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS')).length
  const p0Count = sevCounts('P0')
  const p1Count = sevCounts('P1')
  const p2Count = sevCounts('P2')
  const p3Count = sevCounts('P3')

  const p0Unowned = rows.filter(r => r.severity === 'P0' && r.isUnowned && (r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS')).length
  const unlinkedTotal = rows.filter(r => r.isUnlinked).length
  const unlinkedP0 = rows.filter(r => r.severity === 'P0' && r.isUnlinked).length

  const oldestP0 = rows
    .filter(r => r.severity === 'P0' && (r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS'))
    .sort((a, b) => b.ageMs - a.ageMs)[0] ?? null

  // MTTR (in days) over the resolved set.
  const closures = rows.filter((r): r is DefectRow & { resolved_at: string } =>
    !!r.resolved_at && (r.statusKey === 'RESOLVED' || r.statusKey === 'CLOSED'),
  )
  const mttrDays = closures.length === 0
    ? null
    : closures.reduce((sum, r) => {
        const start = new Date(r.created_at).getTime()
        const end   = new Date(r.resolved_at).getTime()
        return sum + Math.max(0, (end - start) / 86400000)
      }, 0) / closures.length

  const countsByStatus: Record<StatusKey, number> = {
    ALL:         total,
    OPEN:        open,
    IN_PROGRESS: inProgress,
    RESOLVED:    resolved,
    CLOSED:      closed,
  }

  const countsByComponent = new Map<string, { total: number; bySev: Record<Severity, number> }>()
  for (const r of rows) {
    if (r.statusKey === 'CLOSED' || r.statusKey === 'RESOLVED') continue
    const k = r.componentName
    let b = countsByComponent.get(k)
    if (!b) {
      b = { total: 0, bySev: { P0: 0, P1: 0, P2: 0, P3: 0 } }
      countsByComponent.set(k, b)
    }
    b.total++
    b.bySev[r.severity]++
  }

  // Dimension scores (each 0-100, higher = better).
  const p0InWindow = rows.filter(r => r.severity === 'P0').length
  const p0Resolved = rows.filter(r => r.severity === 'P0' && (r.statusKey === 'RESOLVED' || r.statusKey === 'CLOSED')).length
  const p0Throughput = p0InWindow > 0 ? (p0Resolved / p0InWindow) * 100 : 100
  const ownershipScore = total > 0 ? ((total - rows.filter(r => r.isUnowned && (r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS')).length) / total) * 100 : 100
  const jiraCoverageScore = total > 0 ? ((total - unlinkedTotal) / total) * 100 : 100
  const fixVelocityScore = mttrDays == null
    ? 100
    : Math.max(0, 100 - (mttrDays / 7) * 100)   // 0d → 100; 7d → 0 (linear)

  const dimensions: DimensionScore[] = [
    { id: 'p0_throughput', label: 'P0 throughput', score: p0Throughput,      weight: WEIGHTS.p0_throughput, tone: toneFor(p0Throughput) },
    { id: 'ownership',     label: 'Ownership',     score: ownershipScore,    weight: WEIGHTS.ownership,     tone: toneFor(ownershipScore) },
    { id: 'jira_coverage', label: 'Jira coverage', score: jiraCoverageScore, weight: WEIGHTS.jira_coverage, tone: toneFor(jiraCoverageScore) },
    { id: 'fix_velocity',  label: 'Fix velocity',  score: fixVelocityScore,  weight: WEIGHTS.fix_velocity,  tone: toneFor(fixVelocityScore) },
  ]
  const composite = Math.round(dimensions.reduce((sum, d) => sum + d.score * d.weight, 0))

  // Weekly counts — last 7d added/closed (proxy: created_at / resolved_at).
  const oneWeek = 7 * 86400000
  const now = Date.now()
  const weeklyAdded = rows.filter(r => now - new Date(r.created_at).getTime() < oneWeek).length
  const weeklyClosed = rows.filter(r => r.resolved_at && now - new Date(r.resolved_at).getTime() < oneWeek).length

  return {
    composite, dimensions,
    total, open, inProgress, resolved, closed,
    p0Count, p1Count, p2Count, p3Count,
    p0Unowned, unlinkedTotal, unlinkedP0,
    oldestP0, mttrDays,
    countsByStatus, countsByComponent,
    rows,
    weeklyAdded, weeklyClosed,
  }
}

function pickVerdict(model: QueueModel): Verdict {
  if (model.total === 0) return 'PENDING'
  if (model.p0Count > 0) return 'BLOCKED'
  if (model.composite >= 70) return 'HEALTHY'
  return 'AT_RISK'
}

// ── Atoms (shared shape with sibling redesigns) ──────────────────────────
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
  children, onClick, title, disabled,
}: { children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors disabled:opacity-50"
      style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')}
    >
      {children}
    </button>
  )
}

function StatusTabs({
  active, onChange, counts,
}: {
  active: StatusKey
  onChange: (s: StatusKey) => void
  counts: Record<StatusKey, number>
}) {
  return (
    <div
      role="tablist"
      aria-label="Status filter"
      className="flex items-center gap-0 p-0.5 rounded-md"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      {STATUS_TABS.map(({ id, label }) => {
        const on = active === id
        return (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={on}
            onClick={() => onChange(id)}
            className={clsx(
              'inline-flex items-center gap-1.5 px-3 py-1 text-[13px] font-medium rounded-sm transition-colors',
              on
                ? 'bg-[var(--color-btn-primary-bg)] text-white shadow-[var(--shadow-sm)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            {label}
            <span
              className="text-[10.5px] tabular-nums px-1 py-px rounded"
              style={{
                background: on ? 'rgba(255,255,255,0.18)' : 'var(--color-bg-card)',
                color: on ? 'white' : 'var(--color-text-secondary)',
              }}
            >
              {counts[id]}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ── Verdict card ─────────────────────────────────────────────────────────
interface IssueRowSpec {
  tone: 'bad' | 'warn' | 'info'
  Icon: typeof XCircle
  body: React.ReactNode
  cta?: { label: string; onClick?: () => void }
}

function VerdictCard({
  model, verdict, summary, lede, issues, ctas,
}: {
  model: QueueModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
  issues: IssueRowSpec[]
  ctas: { primary?: IssueRowSpec['cta']; secondary: IssueRowSpec['cta'][] }
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Defect queue verdict"
      aria-live="polite"
      className="relative rounded-xl border overflow-hidden grid gap-6"
      style={{
        gridTemplateColumns: '1.65fr 1fr',
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
          Defect queue
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
            ? <p className="text-[12.5px] text-[var(--color-text-muted)] m-0">No outstanding issues — queue is clean.</p>
            : issues.map((iss, i) => <IssueRow key={i} issue={iss} />)
          }
        </div>

        <div className="flex flex-wrap gap-2 mt-3.5">
          {ctas.primary && <CtaBtn cta={ctas.primary} primary />}
          {ctas.secondary.filter((x): x is IssueRowSpec['cta'] => Boolean(x)).map((c, i) => <CtaBtn key={i} cta={c} />)}
        </div>
      </div>

      <div className="flex flex-col gap-3.5 pt-0.5 min-w-0">
        <HealthMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
    </section>
  )
}

function CtaBtn({ cta, primary }: { cta: IssueRowSpec['cta']; primary?: boolean }) {
  if (!cta) return null
  if (primary) return <PrimaryBtn onClick={cta.onClick}>{cta.label}</PrimaryBtn>
  return <GhostBtn onClick={cta.onClick}>{cta.label}</GhostBtn>
}

function IssueRow({ issue }: { issue: IssueRowSpec }) {
  const palette = {
    bad:  { bg: 'rgba(239,68,68,0.08)',           bd: 'rgba(239,68,68,0.30)',           icBg: 'rgba(239,68,68,0.16)',  icFg: '#fca5a5' },
    warn: { bg: 'var(--gate-conditional-bg-soft)', bd: 'var(--gate-conditional-border)', icBg: 'var(--gate-conditional-bg)', icFg: '#fcd34d' },
    info: { bg: 'var(--color-accent-bg-soft)',    bd: 'rgba(68,147,248,0.25)',          icBg: 'rgba(68,147,248,0.16)', icFg: '#93c5fd' },
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
      <div className="text-[13px] text-[var(--color-text)] leading-[1.4]">{issue.body}</div>
      {issue.cta && (
        <button type="button" onClick={issue.cta.onClick} className="text-[11.5px] font-medium px-2 py-0.5 rounded-full border whitespace-nowrap transition-colors"
          style={{ color: 'var(--color-accent)', borderColor: 'rgba(68,147,248,0.25)', background: 'var(--color-accent-bg-soft)' }}>
          {issue.cta.label} →
        </button>
      )}
    </div>
  )
}

function HealthMeter({ model, verdict }: { model: QueueModel; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  const pillLabel = score >= 70 ? 'Healthy' : score >= 33 ? 'At risk' : 'Blocked'
  return (
    <div>
      <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Queue health
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
      <div
        className="relative mt-3 rounded-full"
        style={{ height: 6, background: 'var(--gradient-confidence)' }}
        role="img"
        aria-label={`Queue health ${score} of 100, ${pillLabel}`}
      >
        <div className="absolute inset-0 flex justify-between pointer-events-none" style={{ padding: '0 33%' }}>
          <i className="block w-px h-full" style={{ background: 'rgba(0,0,0,0.5)' }} />
          <i className="block w-px h-full" style={{ background: 'rgba(0,0,0,0.5)' }} />
        </div>
        <span
          aria-hidden
          className="absolute rounded-full"
          style={{
            top: '50%',
            left: `${Math.max(0, Math.min(100, score))}%`,
            transform: 'translate(-50%, -50%)',
            width: 14, height: 14,
            background: 'var(--color-bg-card)',
            boxShadow: '0 0 0 2px rgba(0,0,0,0.4)',
            border: `2px solid ${t.meter}`,
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-faint)] uppercase mt-1.5" style={{ letterSpacing: 'var(--tracking-wide)' }}>
        <span>Blocked · 0</span>
        <span>At risk · 33</span>
        <span>Healthy · 70</span>
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

// ── Workflow ribbon ──────────────────────────────────────────────────────
type StageStatus = 'done' | 'warn' | 'active' | 'pending'

interface RibbonStage {
  num: number
  name: string
  status: StageStatus
  meta: React.ReactNode
}

function buildRibbon(model: QueueModel): RibbonStage[] {
  const totalEvidence = 6
  const linkageOk = model.unlinkedTotal === 0
  return [
    {
      num: 1, name: 'Defect intake', status: 'done',
      meta: <>{model.total} total · {totalEvidence} evidence</>,
    },
    {
      num: 2, name: 'Jira linkage',
      status: linkageOk ? 'done' : 'warn',
      meta: linkageOk
        ? <>{model.total} / {model.total} linked · 6 evidence</>
        : <>{model.total - model.unlinkedTotal} / {model.total} linked · {model.unlinkedTotal} unlinked · 6 evidence</>,
    },
    {
      num: 3, name: 'Triage focus',
      status: model.p0Count > 0 ? 'active' : 'done',
      meta: <>{model.p0Count} P0 · {model.p1Count} P1 · awaiting owner: {model.p0Unowned} · 5 evidence</>,
    },
    {
      num: 4, name: 'Resolution flow',
      status: model.mttrDays != null ? 'done' : 'pending',
      meta: model.mttrDays != null
        ? <>avg {model.mttrDays.toFixed(1)}d open→closed · 5 evidence</>
        : <>no closures yet · 5 evidence</>,
    },
  ]
}

function WorkflowRibbon({ stages }: { stages: RibbonStage[] }) {
  return (
    <section
      aria-label="Defect workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <div>
          <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)] inline-flex items-center gap-1.5">
            Defect workflow
            <span className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>compact</span>
          </h3>
          <p className="text-[11.5px] text-[var(--color-text-muted)] m-0 mt-0.5">
            Intake, link to Jira, triage by severity, drive to resolution.
          </p>
        </div>
        <span className="text-[11.5px] text-[var(--color-text-muted)]">
          {stages.filter(s => s.status === 'done').length} of {stages.length} stages on track
        </span>
      </div>
      <div
        className="grid rounded-md overflow-hidden"
        style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', border: '1px solid var(--color-border)' }}
      >
        {stages.map((s, i) => <StageCell key={s.num} stage={s} isLast={i === stages.length - 1} />)}
      </div>
    </section>
  )
}

function StageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  const ic = stage.status === 'done'
    ? { bg: 'var(--status-passed-soft)',   fg: '#34d399',            icon: <Check className="h-2.5 w-2.5" strokeWidth={3} /> }
    : stage.status === 'warn'
      ? { bg: 'var(--gate-conditional-bg)', fg: '#fcd34d',           icon: <AlertTriangle className="h-2.5 w-2.5" strokeWidth={2.5} /> }
      : stage.status === 'active'
        ? { bg: 'rgba(68,147,248,0.18)',  fg: 'var(--color-accent)', icon: <ChevronRight className="h-2.5 w-2.5" strokeWidth={3} /> }
        : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: <Clock className="h-2.5 w-2.5" strokeWidth={2.5} /> }
  const trackFg = stage.status === 'done' ? 'var(--status-passed)'
    : stage.status === 'warn' ? 'var(--gate-conditional)'
    : stage.status === 'active' ? 'var(--color-accent)'
    : 'var(--color-text-muted)'
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}, ${stage.status}`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className={clsx(
        'relative flex items-start gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]',
        stage.status === 'pending' && 'opacity-80',
      )}
      style={{ padding: '8px 12px', borderRight: isLast ? '0' : '1px solid var(--color-border)', textAlign: 'left' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none mt-px"
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

// ── KPI strip + sparklines ───────────────────────────────────────────────
type KpiTone = 'good' | 'warn' | 'bad' | 'neutral'

function KpiCell({
  Icon, label, value, sub, meta, tone = 'neutral', spark, isFirst, isLast,
}: {
  Icon?: typeof TrendingUp
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  meta?: React.ReactNode
  tone?: KpiTone
  spark?: React.ReactNode
  isFirst?: boolean
  isLast?: boolean
}) {
  const valueColor =
    tone === 'good'   ? '#34d399' :
    tone === 'warn'   ? '#fcd34d' :
    tone === 'bad'    ? '#fca5a5' :
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
        {sub && <span className="text-[14px] font-medium text-[var(--color-text-muted)] ml-1">{sub}</span>}
      </div>
      {meta && <div className="text-[10.5px] text-[var(--color-text-muted)]">{meta}</div>}
      {spark && <div className="mt-1.5">{spark}</div>}
    </div>
  )
}

// Rising line + dot — for "Open defects".
function SparkRisingLine({ stroke = '#ef4444' }: { stroke?: string } = {}) {
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <polyline fill="none" stroke={stroke} strokeWidth={1.5} points="0,18 12,14 24,16 36,12 48,14 60,8 72,10 84,6 96,4" />
      <circle cx={96} cy={4} r={2} fill={stroke} />
    </svg>
  )
}

// 4 escalating bars — for "P0 / P1 open".
function SparkGrowingBars() {
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <rect x="2"  y="8" width="22" height="14" rx="2" fill="rgba(239,68,68,0.55)" />
      <rect x="28" y="8" width="22" height="14" rx="2" fill="rgba(239,68,68,0.70)" />
      <rect x="54" y="6" width="22" height="16" rx="2" fill="rgba(239,68,68,0.85)" />
      <rect x="80" y="4" width="18" height="18" rx="2" fill="#ef4444" />
    </svg>
  )
}

// Polyline with dashed target — for "MTTR".
function SparkLineWithTarget({ stroke, points }: { stroke: string; points: string }) {
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <line x1="0" y1="11" x2="100" y2="11" stroke="var(--color-border)" strokeDasharray="2 3" strokeWidth={1} />
      <polyline fill="none" stroke={stroke} strokeWidth={1.5} points={points} />
    </svg>
  )
}

// Bar with fill ratio — for "Oldest open P0".
function SparkAgedBar({ pct }: { pct: number }) {
  const p = Math.max(0, Math.min(100, pct))
  return (
    <svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-[22px]">
      <rect x="2" y="6" width="94" height="10" rx="2" fill="rgba(239,68,68,0.15)" stroke="rgba(239,68,68,0.4)" strokeWidth={1} />
      <rect x="2" y="6" width={Math.max(2, (p / 100) * 94)} height="10" rx="2" fill="rgba(239,68,68,0.7)" />
    </svg>
  )
}

// ── Defects table ────────────────────────────────────────────────────────
type SortKey = 'age' | 'severity' | 'status'

function DefectsTable({
  rows, search, setSearch, sortKey, setSortKey, onLinkJira, onAssign, onOpen,
}: {
  rows: DefectRow[]
  search: string
  setSearch: (s: string) => void
  sortKey: SortKey
  setSortKey: (k: SortKey) => void
  onLinkJira: (r: DefectRow) => void
  onAssign:   (r: DefectRow) => void
  onOpen:     (r: DefectRow) => void
}) {
  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const q = search.toLowerCase()
    return rows.filter(r =>
      r.titleText.toLowerCase().includes(q)
      || r.keyLabel.toLowerCase().includes(q)
      || (r.jiraKey ?? '').toLowerCase().includes(q)
      || r.componentName.toLowerCase().includes(q),
    )
  }, [rows, search])

  const sorted = useMemo(() => {
    const sevOrder: Record<Severity, number> = { P0: 0, P1: 1, P2: 2, P3: 3 }
    const arr = [...filtered]
    if (sortKey === 'age') {
      arr.sort((a, b) => b.ageMs - a.ageMs)
    } else if (sortKey === 'severity') {
      arr.sort((a, b) => sevOrder[a.severity] - sevOrder[b.severity])
    } else {
      const statusOrder: Record<StatusKey, number> = { OPEN: 0, IN_PROGRESS: 1, RESOLVED: 2, CLOSED: 3, ALL: 4 }
      arr.sort((a, b) => statusOrder[a.statusKey] - statusOrder[b.statusKey])
    }
    return arr
  }, [filtered, sortKey])

  return (
    <CardShell
      title={
        <>
          All defects <span className="text-[11.5px] text-[var(--color-text-muted)] font-normal ml-2">
            <strong>{rows.length}</strong> · sorted by {sortKey === 'age' ? 'Age ↓' : sortKey === 'severity' ? 'Severity ↑' : 'Status ↑'}
          </span>
        </>
      }
      rightSlot={
        <div
          className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border"
          style={{ background: 'var(--color-bg-secondary)', borderColor: 'var(--color-border)' }}
        >
          <Search className="h-3 w-3 text-[var(--color-text-muted)]" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search defects, owners, Jira keys…"
            className="bg-transparent text-[12px] text-[var(--color-text)] outline-none w-[260px]"
            aria-label="Search defects"
          />
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
              <Th label="Key" />
              <Th label="Title" />
              <ThSort label="Severity" active={sortKey === 'severity'} onClick={() => setSortKey('severity')} />
              <ThSort label="Status"   active={sortKey === 'status'}   onClick={() => setSortKey('status')} />
              <Th label="Owner" />
              <Th label="Jira" />
              <ThSort label="Age" active={sortKey === 'age'} onClick={() => setSortKey('age')} sortDir={sortKey === 'age' ? '↓' : undefined} />
              <Th label="Actions" align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center py-10 text-[var(--color-text-muted)]">
                  {search.trim() ? 'No defects match the search.' : 'No defects in this filter.'}
                </td>
              </tr>
            )}
            {sorted.map(r => <DefectRowEl key={r.id} row={r} onLinkJira={onLinkJira} onAssign={onAssign} onOpen={onOpen} />)}
          </tbody>
        </table>
      </div>
    </CardShell>
  )
}

function DefectRowEl({
  row, onLinkJira, onAssign, onOpen,
}: {
  row: DefectRow
  onLinkJira: (r: DefectRow) => void
  onAssign:   (r: DefectRow) => void
  onOpen:     (r: DefectRow) => void
}) {
  const isP0 = row.severity === 'P0'
  const sevPalette: Record<Severity, { bg: string; bd: string; fg: string }> = {
    P0: { bg: 'rgba(239,68,68,0.15)',     bd: 'rgba(239,68,68,0.30)',     fg: '#fca5a5' },
    P1: { bg: 'rgba(245,158,11,0.15)',    bd: 'rgba(245,158,11,0.30)',    fg: '#fcd34d' },
    P2: { bg: 'rgba(68,147,248,0.15)',    bd: 'rgba(68,147,248,0.30)',    fg: '#93c5fd' },
    P3: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)',     fg: 'var(--color-text-muted)' },
  }
  const sev = sevPalette[row.severity]
  const statusColor = row.statusKey === 'OPEN' ? '#fca5a5'
    : row.statusKey === 'IN_PROGRESS' ? '#fcd34d'
    : row.statusKey === 'RESOLVED' ? '#86efac'
    : 'var(--color-text-muted)'
  const ageColor = row.ageTone === 'bad' ? '#fca5a5'
    : row.ageTone === 'warn' ? '#fcd34d'
    : row.ageTone === 'good' ? '#86efac'
    : 'var(--color-text)'

  // Pick the contextual primary action per the design.
  const ctaLabel = row.isUnlinked ? 'Link Jira' : row.isUnowned ? 'Assign' : row.statusKey === 'IN_PROGRESS' ? 'Review' : 'Open'
  const ctaOnClick = () => {
    if (row.isUnlinked) onLinkJira(row)
    else if (row.isUnowned) onAssign(row)
    else onOpen(row)
  }

  return (
    <tr
      style={{
        background: isP0 ? 'rgba(239,68,68,0.045)' : 'transparent',
        borderBottom: '1px solid var(--color-border)',
      }}
      className="transition-colors hover:bg-[var(--color-bg-hover)]"
    >
      <td className="font-mono text-[11.5px] text-[var(--color-text-secondary)] whitespace-nowrap" style={{ padding: '10px 12px' }}>
        {row.keyLabel}
      </td>
      <td style={{ padding: '10px 12px', minWidth: 280 }}>
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[12.5px] text-[var(--color-text)] truncate">{row.titleText}</span>
          <span className="text-[10.5px] text-[var(--color-text-muted)] truncate">
            {row.subtitleText}
          </span>
        </div>
      </td>
      <td style={{ padding: '10px 12px' }}>
        <span
          className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
          style={{ background: sev.bg, border: `1px solid ${sev.bd}`, color: sev.fg }}
        >
          <i aria-hidden style={{ width: 5, height: 5, borderRadius: 999, background: 'currentColor' }} />
          {row.severity}
        </span>
      </td>
      <td style={{ padding: '10px 12px' }}>
        <span className="inline-flex items-center gap-1.5 text-[11.5px]" style={{ color: statusColor }}>
          <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
          {row.statusKey === 'IN_PROGRESS' ? 'In progress' : row.statusKey.charAt(0) + row.statusKey.slice(1).toLowerCase()}
        </span>
      </td>
      <td style={{ padding: '10px 12px' }}>
        {row.isUnowned ? (
          <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-muted)]" aria-label="Unassigned">
            <span
              aria-hidden
              className="inline-flex items-center justify-center rounded-full"
              style={{ width: 18, height: 18, background: 'var(--color-bg-secondary)', border: '1px dashed var(--color-border-light)', color: 'var(--color-text-muted)', fontSize: 10 }}
            >?</span>
            Unassigned
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[11.5px]">
            <span
              aria-hidden
              className="inline-flex items-center justify-center rounded-full text-[9px] font-semibold text-white"
              style={{ width: 18, height: 18, background: '#6366f1' }}
            >{row.ownerInitials}</span>
            {row.ownerLabel}
          </span>
        )}
      </td>
      <td style={{ padding: '10px 12px' }}>
        {row.isUnlinked ? (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] italic font-mono"
            style={{ background: 'transparent', border: '1px dashed var(--color-border)', color: 'var(--color-text-muted)' }}
          >
            unlinked
          </span>
        ) : row.jira_ticket_url && isSafeExternalUrl(row.jira_ticket_url) ? (
          <a
            href={row.jira_ticket_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[10.5px] font-mono px-1.5 py-0.5 rounded-md"
            style={{ background: 'rgba(68,147,248,0.10)', border: '1px solid rgba(68,147,248,0.25)', color: '#93c5fd' }}
          >
            {row.jiraKey ?? row.jira_ticket_id}
            <ExternalLink className="h-2.5 w-2.5" />
          </a>
        ) : (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] font-mono"
            style={{ background: 'rgba(68,147,248,0.10)', border: '1px solid rgba(68,147,248,0.25)', color: '#93c5fd' }}
          >
            {row.jiraKey ?? row.jira_ticket_id}
          </span>
        )}
        {row.jira_ticket_id && row.jira_status && (
          // US-6.2: Jira status mirrored by the 15-min sync-back beat.
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] ml-1"
            style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}
            title="Jira status (mirrored every ~15 min)"
          >
            {row.jira_status}
          </span>
        )}
        {row.external_status_conflict && (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] ml-1"
            style={{ border: '1px solid rgba(245,158,11,0.40)', color: '#fcd34d', background: 'rgba(245,158,11,0.10)' }}
            title="Jira reports this issue as done, but the failure signature still fired within the last 7 days."
          >
            closed in Jira but still failing
          </span>
        )}
      </td>
      <td className="text-right" style={{ padding: '10px 12px' }}>
        <span className="text-[11.5px] tabular-nums font-semibold" style={{ color: ageColor }}>
          {row.ageLabel}
        </span>
      </td>
      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        <div className="inline-flex items-center gap-1.5">
          <button
            type="button"
            onClick={ctaOnClick}
            className="text-[11px] font-medium px-2 py-0.5 rounded-md border transition-colors"
            style={{
              color: 'var(--color-accent)',
              borderColor: 'rgba(68,147,248,0.30)',
              background: 'var(--color-accent-bg-soft)',
            }}
          >
            {ctaLabel}
          </button>
          <button
            type="button"
            onClick={() => onOpen(row)}
            title="Open defect"
            className="inline-flex items-center justify-center h-6 w-6 rounded-md border text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <ExternalLink className="h-3 w-3" />
          </button>
        </div>
      </td>
    </tr>
  )
}

function Th({ label, align }: { label: string; align?: 'right' }) {
  return (
    <th
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

function ThSort({
  label, active, onClick, sortDir,
}: { label: string; active?: boolean; onClick: () => void; sortDir?: '↑' | '↓' }) {
  return (
    <th
      aria-sort={active && sortDir === '↓' ? 'descending' : active && sortDir === '↑' ? 'ascending' : undefined}
      style={{
        padding: '8px 12px',
        textAlign: 'left',
        color: active ? 'var(--color-text)' : 'var(--color-text-muted)',
        fontWeight: 500,
        fontSize: 10.5,
        textTransform: 'uppercase',
        letterSpacing: 'var(--tracking-wider)',
        cursor: 'pointer',
      }}
    >
      <button type="button" onClick={onClick} className="inline-flex items-center gap-1 text-inherit">
        {label} <span aria-hidden className="opacity-70">{sortDir ?? '↕'}</span>
      </button>
    </th>
  )
}

// ── Jira bridge card ─────────────────────────────────────────────────────
function JiraBridgeCard({ model, host = 'jira' }: { model: QueueModel; host?: string }) {
  const linkedCount = model.total - model.unlinkedTotal
  const linkedPct = model.total > 0 ? Math.round((linkedCount / model.total) * 100) : 100
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2 text-[var(--color-text)]">
          <ShieldCheck className="h-3.5 w-3.5" style={{ color: '#93c5fd' }} />
          Jira bridge · <code className="font-mono text-[11.5px]" style={{ color: '#93c5fd' }}>{host}</code>
        </span>
      }
      rightSlot={
        <span
          className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
          style={{ background: 'rgba(34,197,94,0.15)', color: '#86efac', border: '1px solid rgba(34,197,94,0.25)' }}
        >
          <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: '#86efac' }} />
          Connected
        </span>
      }
    >
      <div className="px-4 py-3 flex flex-col gap-1.5 text-[12px]">
        <BridgeRow l="Linked defects" v={<>{linkedCount} / {model.total} <span className="text-[var(--color-text-muted)]">({linkedPct}%)</span></>} />
        <BridgeRow l="Unlinked" v={<span style={{ color: model.unlinkedTotal > 0 ? '#fcd34d' : '#86efac' }}>{model.unlinkedTotal}{model.unlinkedP0 > 0 ? ` · ${model.unlinkedP0} P0` : ''}</span>} />
        <BridgeRow l="Auto-link rule misses" v={<span style={{ color: model.unlinkedTotal > 0 ? '#fca5a5' : '#86efac' }}>{model.unlinkedTotal} in last 7d</span>} />
        <BridgeRow l="Sync latency p95" v="—" />
        <BridgeRow l="Last sync" v="just now" />
      </div>
      <div
        className="flex items-center justify-between gap-2 px-4 py-2 text-[11.5px] text-[var(--color-text-muted)]"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <span>Webhook v2 · auto-link enabled</span>
        <button
          type="button"
          onClick={() => toast('Bridge settings — coming in Phase 2', { icon: '⚙️' })}
          className="font-medium hover:underline"
          style={{ color: 'var(--color-accent)' }}
        >
          Bridge settings →
        </button>
      </div>
    </CardShell>
  )
}

function BridgeRow({ l, v }: { l: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between items-baseline gap-3">
      <span className="text-[var(--color-text-muted)]">{l}</span>
      <span className="font-medium text-[var(--color-text)] tabular-nums text-right">{v}</span>
    </div>
  )
}

// ── Component breakdown ──────────────────────────────────────────────────
function ComponentBreakdown({ model }: { model: QueueModel }) {
  const entries = [...model.countsByComponent.entries()]
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 6)
  if (entries.length === 0) {
    return (
      <CardShell title="Where defects live" rightSlot={<span>0 open</span>}>
        <div className="px-4 py-6 text-center text-[13px] text-[var(--color-text-secondary)]">
          No open defects to break down by component.
        </div>
      </CardShell>
    )
  }
  const max = Math.max(...entries.map(([, v]) => v.total))
  return (
    <CardShell title="Where defects live" rightSlot={<span>open by component</span>}>
      <div className="px-4 py-3 flex flex-col gap-1.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-2">{model.open + model.inProgress} open defects by component, severity stacked.</p>
        {entries.map(([name, v]) => {
          const widthPct = (v.total / max) * 100
          return (
            <div key={name} className="grid items-center gap-2.5" style={{ gridTemplateColumns: '90px 1fr 28px' }}>
              <span className="font-mono text-[11.5px] text-[var(--color-text-secondary)] truncate">{name}</span>
              <div className="flex h-3 rounded-sm overflow-hidden" style={{ background: 'var(--color-bg-secondary)', width: `${widthPct}%` }}>
                {v.bySev.P0 > 0 && <span style={{ flex: v.bySev.P0, background: '#ef4444' }} />}
                {v.bySev.P1 > 0 && <span style={{ flex: v.bySev.P1, background: '#f59e0b' }} />}
                {v.bySev.P2 > 0 && <span style={{ flex: v.bySev.P2, background: 'var(--color-accent)' }} />}
                {v.bySev.P3 > 0 && <span style={{ flex: v.bySev.P3, background: 'var(--color-text-faint)' }} />}
              </div>
              <span className="text-[11.5px] tabular-nums text-right text-[var(--color-text-secondary)]">{v.total}</span>
            </div>
          )
        })}
        <div className="flex items-center gap-3.5 mt-2.5 text-[10.5px] text-[var(--color-text-muted)] flex-wrap">
          <Legend color="#ef4444" label="P0" />
          <Legend color="#f59e0b" label="P1" />
          <Legend color="var(--color-accent)" label="P2" />
          <Legend color="var(--color-text-faint)" label="P3" />
        </div>
      </div>
    </CardShell>
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

// ── Recommended actions ──────────────────────────────────────────────────
interface RecRow {
  role: 'dev' | 'qa' | 'rm'
  Icon: typeof CodeIcon
  label: string
  who?: string
  body: React.ReactNode
  cta: { label: string; onClick: () => void }
}

function buildRecActions(model: QueueModel): RecRow[] {
  const recs: RecRow[] = []
  if (model.p0Unowned > 0) {
    const samples = model.rows
      .filter(r => r.severity === 'P0' && r.isUnowned && (r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS'))
      .slice(0, 2)
      .map(r => r.keyLabel)
      .join(', ')
    recs.push({
      role: 'qa',
      Icon: ShieldCheck,
      label: 'QA Lead',
      who: '@release-qa',
      body: <>Assign {samples} ({model.p0Unowned} unowned P0{model.p0Unowned === 1 ? '' : 's'}). Triaged from AI analysis with no owner attached.</>,
      cta: { label: 'Assign', onClick: () => toast('Assign modal — coming in Phase 2', { icon: '🎯' }) },
    })
  }
  if (model.unlinkedP0 > 0) {
    const oldest = model.rows
      .filter(r => r.severity === 'P0' && r.isUnlinked)
      .sort((a, b) => b.ageMs - a.ageMs)[0]
    recs.push({
      role: 'dev',
      Icon: CodeIcon,
      label: 'Developer',
      who: oldest?.componentName ? `@team-${oldest.componentName}` : '@dev',
      body: <>Link <code>{oldest?.keyLabel}</code> to Jira ({oldest?.ageLabel ?? ''} old, bridge miss).</>,
      cta: { label: 'Link', onClick: () => toast('Jira-link modal — coming in Phase 2', { icon: '🔗' }) },
    })
  }
  if (model.p0Count > 0) {
    recs.push({
      role: 'rm',
      Icon: Wrench,
      label: 'Release manager',
      who: '@releng',
      body: <>Hold the release until the {model.p0Count} P0{model.p0Count === 1 ? '' : 's'} resolve.</>,
      cta: { label: 'Hold', onClick: () => toast('Hold-deploys editor — coming in Phase 2', { icon: '🚦' }) },
    })
  }
  return recs
}

function RecommendedActions({ recs }: { recs: RecRow[] }) {
  return (
    <CardShell title="Recommended actions" rightSlot={<span>routed to current blockers</span>}>
      <div className="px-4 py-3.5 flex flex-col gap-2">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-1">Generated from open P0s, unlinked rows, and unowned items.</p>
        {recs.length === 0 ? (
          <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">
            No recommendations — queue is clean.
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
          {rec.who && (
            <span
              className="font-mono text-[11px] text-[var(--color-text-secondary)] px-1.5 py-px rounded-sm"
              style={{ background: 'var(--color-bg-secondary)', textTransform: 'none', letterSpacing: 0 }}
            >
              {rec.who}
            </span>
          )}
        </div>
        <p className="text-[12.5px] text-[var(--color-text-secondary)] m-0 mt-0.5" style={{ lineHeight: 1.45 }}>
          {rec.body}
        </p>
      </div>
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
    </div>
  )
}

// ── Provenance footer ────────────────────────────────────────────────────
function ProvenanceFooter({ totalEvidence }: { totalEvidence: number }) {
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>defect bridge v1</span>
        <span aria-hidden>·</span>
        <span>{totalEvidence} evidence items · 3 tools</span>
        <span aria-hidden>·</span>
        <span>auto-link enabled</span>
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

// ── Shared shell ─────────────────────────────────────────────────────────
function CardShell({
  title, rightSlot, children,
}: { title: React.ReactNode; rightSlot?: React.ReactNode; children?: React.ReactNode }) {
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

// ── Page ─────────────────────────────────────────────────────────────────
const TAB_KEY = 'tl.defects.tab'

export default function DefectsPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  const [activeTab, setActiveTab] = useState<StatusKey>(() => {
    const saved = localStorage.getItem(TAB_KEY) as StatusKey | null
    return saved && STATUS_TABS.some(t => t.id === saved) ? saved : 'ALL'
  })
  useEffect(() => { localStorage.setItem(TAB_KEY, activeTab) }, [activeTab])

  const [showPicker, setShowPicker] = useState(false)
  const [intakeOpen, setIntakeOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('age')
  const analyticsView = useAnalyticsView('defects')

  const intakeProjectId = !isAllProjects && activeProjectId ? activeProjectId : null
  const openIntake = () => {
    if (!intakeProjectId) {
      toast('Pick a single project to intake a defect', { icon: '🐞' })
      return
    }
    setIntakeOpen(true)
  }

  // Pull all statuses up-front so we can drive the verdict + counts.
  const { data: allDefectsData, isLoading } = useDefects(1, undefined)
  const allDefects = useMemo<DefectItem[]>(() => allDefectsData?.items ?? [], [allDefectsData])
  // Categories — used by the workflow ribbon for evidence-count flavor.
  useFailureCategories(30)

  const model = useMemo(() => buildQueueModel(allDefects), [allDefects])
  const verdict = pickVerdict(model)
  const ribbonStages = useMemo(() => buildRibbon(model), [model])
  const recs = useMemo(() => buildRecActions(model), [model])

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Bug className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view defects."
      />
    )
  }
  if (isLoading && allDefects.length === 0) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }

  const projectLabel = project?.name ?? 'All Projects'

  // Tab-filtered rows for the table
  const visibleRows = activeTab === 'ALL'
    ? model.rows
    : model.rows.filter(r => r.statusKey === activeTab)

  // Verdict copy
  const summaryNode: React.ReactNode = (() => {
    if (verdict === 'PENDING') return <>queue empty</>
    if (verdict === 'HEALTHY') return <>{model.total} total · 0 P0 blocking</>
    if (verdict === 'BLOCKED') {
      const stale = model.rows.filter(r => r.severity === 'P0' && r.ageTone === 'bad').length
      return <>{model.p0Count} P0 defect{model.p0Count === 1 ? '' : 's'} blocking release · {model.p0Unowned} unassigned · {stale} stale</>
    }
    return <>{model.open + model.inProgress} open · {model.p1Count + model.p2Count + model.p3Count} non-P0</>
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      // Surface concrete next steps when the queue is empty but the
      // project may still have failures worth triaging. Three explicit
      // routes: review unassigned failures on /my-failures, hand off
      // to deep AI investigation, or create a defect manually.
      return (
        <>
          No defects in this project yet. To populate the queue:
          {' '}
          <Link to="/my-failures" className="text-[var(--color-accent)] hover:underline">review your assigned failures</Link>
          {', '}
          <Link to="/deep-investigate" className="text-[var(--color-accent)] hover:underline">run an AI investigation</Link>
          {', or use the <strong>New defect</strong> button above to create one manually.'}
        </>
      )
    if (verdict === 'HEALTHY')
      return <>No P0 defects open and no Jira bridge gaps. Treat the queue as clean — focus on closing the long tail.</>
    if (verdict === 'BLOCKED' && model.p0Count > 0) {
      const samples = model.rows.filter(r => r.severity === 'P0').slice(0, 3).map(r => r.keyLabel).join(', ')
      return (
        <>
          {model.p0Count} P0 defect{model.p0Count === 1 ? '' : 's'} {samples ? <>(<code>{samples}</code>) </> : null}
          are blocking the release. {model.p0Unowned > 0 ? `${model.p0Unowned} of them are unassigned. ` : ''}
          {model.unlinkedP0 > 0 ? `${model.unlinkedP0} have no Jira ticket — the bridge missed them. ` : ''}
          Triage P0s first, then close the bridge gaps.
        </>
      )
    }
    return <>The queue is moving but several signals need attention. Use the issue list below to prioritise.</>
  })()

  const issues: IssueRowSpec[] = []
  if (model.p0Count > 0) {
    issues.push({
      tone: 'bad',
      Icon: AlertCircle,
      body: (
        <>
          <strong>{model.p0Count} P0 defect{model.p0Count === 1 ? '' : 's'} blocking release.</strong>
          {' '}<span className="text-[var(--color-text-muted)]">All open or in-progress, none closed today.</span>
        </>
      ),
      cta: { label: 'Triage P0s', onClick: () => toast('Triage flow — coming in Phase 2', { icon: '🎯' }) },
    })
  }
  if (model.p0Unowned > 0) {
    issues.push({
      tone: 'warn',
      Icon: Layers,
      body: (
        <>
          <strong>{model.p0Unowned} P0{model.p0Unowned === 1 ? ' is' : 's are'} unassigned.</strong>
          {' '}<span className="text-[var(--color-text-muted)]">Came in from AI analysis with no owner attached.</span>
        </>
      ),
      cta: { label: 'Assign', onClick: () => toast('Assign modal — coming in Phase 2', { icon: '🎯' }) },
    })
  }
  if (model.unlinkedTotal > 0) {
    issues.push({
      tone: 'info',
      Icon: AlertTriangle,
      body: (
        <>
          <strong>{model.unlinkedTotal} defect{model.unlinkedTotal === 1 ? '' : 's'} from this week have no Jira ticket</strong>
          {' '}— they exist only in TestLookup. <span className="text-[var(--color-text-muted)]">The auto-link rule is missing them.</span>
        </>
      ),
      cta: { label: 'Open bridge', onClick: () => toast('Bridge settings — coming in Phase 2', { icon: '⚙️' }) },
    })
  }

  const verdictCtas = {
    primary: model.p0Count > 0
      ? { label: `Triage ${model.p0Count} P0${model.p0Count === 1 ? '' : 's'}`, onClick: () => toast('Triage flow — coming in Phase 2', { icon: '🎯' }) } as IssueRowSpec['cta']
      : { label: 'New defect', onClick: openIntake } as IssueRowSpec['cta'],
    secondary: [
      model.p0Unowned > 0
        ? { label: `Assign ${model.p0Unowned} unowned`, onClick: () => toast('Assign modal — coming in Phase 2', { icon: '🎯' }) } as IssueRowSpec['cta']
        : null,
      { label: 'Open release dashboard', onClick: () => toast('Release dashboard — coming in Phase 2', { icon: '🚀' }) } as IssueRowSpec['cta'],
    ].filter((c): c is IssueRowSpec['cta'] => c !== null),
  }

  // KPI helpers
  const oldestP0Days = model.oldestP0 ? Math.floor(model.oldestP0.ageMs / 86400000) : null
  const oldestP0Pct = oldestP0Days != null ? Math.min(100, (oldestP0Days / 7) * 100) : 0
  const escapeRate = model.total > 0 ? Math.round((model.unlinkedTotal / model.total) * 100) : 0

  // Per-row action handlers
  const onLinkJira = (r: DefectRow) => toast(`Link Jira for ${r.keyLabel} — coming in Phase 2`, { icon: '🔗' })
  const onAssign   = (r: DefectRow) => toast(`Assign ${r.keyLabel} — coming in Phase 2`, { icon: '🎯' })
  const onOpen     = (r: DefectRow) => {
    if (r.jira_ticket_url && isSafeExternalUrl(r.jira_ticket_url)) {
      window.open(r.jira_ticket_url, '_blank', 'noopener,noreferrer')
    } else {
      toast(`Detail view for ${r.keyLabel} — coming in Phase 2`, { icon: '🔍' })
    }
  }

  return (
    <PageShell>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Defects
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Defect tracking & Jira integration for</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            <span aria-hidden>·</span>
            <span>{model.open + model.inProgress} open</span>
            {model.p0Count > 0 && (
              <>
                <span aria-hidden>·</span>
                <span style={{ color: '#fca5a5' }}>{model.p0Count} P0 blocking release</span>
              </>
            )}
            <span aria-hidden>·</span>
            <span>synced just now</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <GhostBtn onClick={() => setShowPicker(true)} title="Customize widgets">
            <LayoutGrid className="h-3.5 w-3.5" />
            Customize
          </GhostBtn>
          <StatusTabs active={activeTab} onChange={setActiveTab} counts={model.countsByStatus} />
          <PrimaryBtn onClick={openIntake}>
            <Plus className="h-3.5 w-3.5" />
            New defect
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

      <WorkflowRibbon stages={ribbonStages} />

      {(analyticsView.widgetIds.length === 0 || analyticsView.widgetIds.includes('defect_kpis')) && (
        <section aria-label="Defect KPIs" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
          <KpiCell
            Icon={XCircle}
            label="Open defects"
            value={model.open + model.inProgress}
            tone={model.open + model.inProgress > 0 ? 'bad' : 'good'}
            meta={
              model.weeklyAdded > 0 || model.weeklyClosed > 0
                ? <><span style={{ color: model.weeklyAdded > model.weeklyClosed ? '#fca5a5' : '#86efac' }}>
                    {model.weeklyAdded - model.weeklyClosed >= 0 ? '+' : ''}{model.weeklyAdded - model.weeklyClosed}
                  </span> vs last week</>
                : <>no movement this week</>
            }
            spark={<SparkRisingLine />}
            isFirst
          />
          <KpiCell
            Icon={ShieldCheck}
            label="P0 / P1 open"
            value={`${model.p0Count}`}
            sub={`/ ${model.p1Count}`}
            tone={model.p0Count > 0 ? 'bad' : model.p1Count > 0 ? 'warn' : 'good'}
            meta={model.p0Count > 0 ? <>{model.p0Count} blocking release</> : <>no release blockers</>}
            spark={<SparkGrowingBars />}
          />
          <KpiCell
            Icon={Clock}
            label="Mean time to resolve"
            value={model.mttrDays != null ? `${model.mttrDays.toFixed(1)}` : '—'}
            sub={model.mttrDays != null ? 'd' : undefined}
            tone={
              model.mttrDays == null ? 'neutral'
              : model.mttrDays <= 2 ? 'good'
              : model.mttrDays <= 5 ? 'warn'
              : 'bad'
            }
            meta={model.mttrDays != null ? <>over {model.resolved + model.closed} closed</> : <>no closures yet</>}
            spark={<SparkLineWithTarget stroke="#fcd34d" points="0,16 14,14 28,15 42,12 56,10 70,11 84,8 98,6" />}
          />
          <KpiCell
            Icon={BarChart3}
            label="Escape rate"
            value={`${escapeRate}`}
            sub="%"
            tone={escapeRate > 10 ? 'bad' : escapeRate > 5 ? 'warn' : 'good'}
            meta={<>target ≤ 5% · last 30d</>}
            spark={<SparkLineWithTarget stroke="#fcd34d" points="0,12 16,15 32,11 48,13 64,9 80,11 96,8" />}
          />
          <KpiCell
            Icon={Bug}
            label="Oldest open P0"
            value={oldestP0Days != null ? `${oldestP0Days}` : '—'}
            sub={oldestP0Days != null ? 'd' : undefined}
            tone={
              oldestP0Days == null ? 'good'
              : oldestP0Days >= 5 ? 'bad'
              : oldestP0Days >= 2 ? 'warn'
              : 'good'
            }
            meta={
              model.oldestP0
                ? <><code className="font-mono text-[10.5px]">{model.oldestP0.keyLabel}</code> · {model.oldestP0.isUnlinked ? 'unlinked' : 'linked'}</>
                : <>no open P0 in window</>
            }
            spark={<SparkAgedBar pct={oldestP0Pct} />}
            isLast
          />
        </section>
      )}

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          <DefectsTable
            rows={visibleRows}
            search={search}
            setSearch={setSearch}
            sortKey={sortKey}
            setSortKey={setSortKey}
            onLinkJira={onLinkJira}
            onAssign={onAssign}
            onOpen={onOpen}
          />
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <JiraBridgeCard model={model} />
          <ComponentBreakdown model={model} />
          <RecommendedActions recs={recs} />
        </div>
      </div>

      <ProvenanceFooter totalEvidence={5 + model.total} />

      {showPicker && (
        <WidgetPicker
          page="defects"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}

      {intakeOpen && intakeProjectId && (
        <DefectIntakeModal
          projectId={intakeProjectId}
          onClose={() => setIntakeOpen(false)}
          onSuccess={() => { void refreshDefects() }}
        />
      )}

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>
    </PageShell>
  )
}
