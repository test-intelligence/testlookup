/**
 * Defects — verdict-led redesign per design_handoff_defects/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb + saved views + status tabs with counts +
 *             "+ New defect" CTA.
 *   Verdict → 1.65fr | 1fr split. Variants BLOCKED / AT_RISK / HEALTHY /
 *             PENDING. Left: pulsing eyebrow → 26 px headline → lede →
 *             issue rows (release blockers / Jira gaps).
 *             Right: 44 px composite queue-health score + weighted dimension
 *             grid (P0 throughput 35 / Jira coverage 25 / Fix velocity 20,
 *             re-normalised over their sum of 80 → 44 / 31 / 25 %). There is
 *             no Ownership dimension: the backend has no owner field.
 *   KPIs    → 5 cells with sparklines: Open · P0+P1 · MTTR · Escape rate ·
 *             Oldest open P0.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Defects table (Key · Title · Severity · Status · Jira · Age ·
 *             Actions) wrapped in `.table-scroll` so the column
 *             never overflows. P0 rows get faint red row-bg, on hover deeper.
 *             The only row action is opening the linked Jira ticket.
 *     Right → Jira bridge card · Component breakdown (severity-stacked bars
 *             per suite).
 *
 * UX redesign P2: a control renders only when it does something real, so
 * there are no Link-Jira / Assign / Triage buttons until those flows exist.
 *
 * Data: derives every section from the existing useDefects(...). A
 * useFailureCategories() call sat here too; its result was never
 * destructured, so it fed nothing. Removed once the release filter made it
 * re-fetch on every release change to no effect.
 * The README assumes a richer DefectItem with severity,
 * owner, release tag, and bridge metadata — none of which the current model
 * carries. Severity is synthesised; owner is NOT (nothing renders it):
 *   - severity     ← failure_category + ai_confidence_score
 *   - key          ← jira_ticket_id, else the defect id's first 8 characters
 *   - jira link    ← jira_ticket_id / jira_ticket_url
 *   - age          ← created_at
 *   - bridge state ← derived from per-row jira_ticket_id presence
 * When the backend grows real defect-management endpoints these synth paths
 * collapse to single field reads.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, BarChart3, Bug, Clock, ExternalLink, Plus,
  Search, ShieldCheck, TrendingUp, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DefectIntakeModal from '@/components/defects/DefectIntakeModal'
import GaugeBar from '@/components/charts/GaugeBar'
import type { GaugeBands, GaugeTick, GaugeTone } from '@/components/charts/gaugeBar.model'
import { refreshDefects, useDefects } from '@/hooks/useMetrics'
import { jiraBridgeState, useIntegrationsConfig, type JiraBridgeState } from '@/hooks/useIntegrationsConfig'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { SavedView } from '@/services/savedViewsService'
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
    border: 'color-mix(in srgb, var(--status-failed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: 'var(--status-failed)',
    gateText:    'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Release blocked',
    pulse:  true,
  },
  AT_RISK: {
    border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText:    'var(--status-broken)',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'At risk',
    pulse:  true,
  },
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
type DimensionId = 'p0_throughput' | 'jira_coverage' | 'fix_velocity'
// The design's weights. Ownership (0.20) was a fourth: with no owner field
// on the backend it scored every open defect as unowned, so it is gone, and
// the composite divides by the remaining weights' sum to stay on 0-100.
const WEIGHTS: Record<DimensionId, number> = {
  p0_throughput: 0.35,
  jira_coverage: 0.25,
  fix_velocity:  0.20,
}
const WEIGHT_SUM = Object.values(WEIGHTS).reduce((a, b) => a + b, 0)

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

function statusKeyOf(s: string): StatusKey {
  const u = s.toUpperCase()
  if (u === 'OPEN') return 'OPEN'
  if (u === 'IN_PROGRESS' || u === 'IN PROGRESS' || u === 'IN-PROGRESS') return 'IN_PROGRESS'
  if (u === 'RESOLVED') return 'RESOLVED'
  if (u === 'CLOSED') return 'CLOSED'
  return 'OPEN'
}

/** A real identifier for the row: its Jira ticket when linked, otherwise the
 *  defect's own id, shortened. It used to be `EVG-####`, a hash of the id
 *  dressed as a ticket number that did not exist anywhere. */
function buildKeyLabel(d: DefectItem): string {
  return d.jira_ticket_id || d.id.slice(0, 8)
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
  const isUnlinked = !d.jira_ticket_id
  const componentName = componentOf(d)
  const statusKey = statusKeyOf(d.resolution_status)
  return {
    ...d,
    severity,
    ageMs: a.ms,
    ageLabel: a.label,
    ageTone: a.tone,
    isUnlinked,
    componentName,
    titleText: d.test_name,
    subtitleText: d.suite_name
      ? `${d.suite_name} suite · from ${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`
      : `${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`,
    keyLabel: buildKeyLabel(d),
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
  const jiraCoverageScore = total > 0 ? ((total - unlinkedTotal) / total) * 100 : 100
  const fixVelocityScore = mttrDays == null
    ? 100
    : Math.max(0, 100 - (mttrDays / 7) * 100)   // 0d → 100; 7d → 0 (linear)

  // Each dimension carries its RE-NORMALISED weight (design weight / sum), so
  // the tiles' percentages add up to 100 and the composite is their weighted
  // mean on 0-100.
  const dimensions: DimensionScore[] = [
    { id: 'p0_throughput', label: 'P0 throughput', score: p0Throughput,      weight: WEIGHTS.p0_throughput / WEIGHT_SUM, tone: toneFor(p0Throughput) },
    { id: 'jira_coverage', label: 'Jira coverage', score: jiraCoverageScore, weight: WEIGHTS.jira_coverage / WEIGHT_SUM, tone: toneFor(jiraCoverageScore) },
    { id: 'fix_velocity',  label: 'Fix velocity',  score: fixVelocityScore,  weight: WEIGHTS.fix_velocity / WEIGHT_SUM,  tone: toneFor(fixVelocityScore) },
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
    unlinkedTotal, unlinkedP0,
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
}

interface Cta { label: string; onClick: () => void }

function VerdictCard({
  model, verdict, summary, lede, issues, cta,
}: {
  model: QueueModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
  issues: IssueRowSpec[]
  cta?: Cta
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

        {cta && (
          <div className="flex flex-wrap gap-2 mt-3.5">
            <PrimaryBtn onClick={cta.onClick}>{cta.label}</PrimaryBtn>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3.5 pt-0.5 min-w-0">
        <HealthMeter model={model} verdict={verdict} />
        <DimensionGrid dimensions={model.dimensions} />
      </div>
    </section>
  )
}

function IssueRow({ issue }: { issue: IssueRowSpec }) {
  const palette = {
    bad:  { bg: 'color-mix(in srgb, var(--status-failed) 8%, transparent)',           bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',           icBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',  icFg: 'var(--status-failed)' },
    warn: { bg: 'var(--gate-conditional-bg-soft)', bd: 'var(--gate-conditional-border)', icBg: 'var(--gate-conditional-bg)', icFg: 'var(--status-broken)' },
    info: { bg: 'var(--color-accent-bg-soft)',    bd: 'color-mix(in srgb, var(--color-accent) 25%, transparent)',          icBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', icFg: 'var(--color-accent)' },
  }[issue.tone]
  const Icon = issue.Icon
  return (
    <div
      className="grid gap-2.5 items-center rounded-md border"
      style={{ gridTemplateColumns: '22px 1fr', padding: '10px 12px', background: palette.bg, borderColor: palette.bd }}
    >
      <span className="inline-flex items-center justify-center rounded-md" style={{ width: 22, height: 22, background: palette.icBg, color: palette.icFg }}>
        <Icon className="h-3 w-3" />
      </span>
      <div className="text-[13px] text-[var(--color-text)] leading-[1.4]">{issue.body}</div>
    </div>
  )
}

/** The queue meter's scale: the band edges the pill uses (Healthy starts at 70, not 66). */
const HEALTH_TICKS: readonly GaugeTick[] = [
  { value: 0, label: 'Blocked' },
  { value: 33, label: 'At risk' },
  { value: 70, label: 'Healthy' },
  { value: 100 },
]

const VERDICT_GAUGE_TONE: Record<Verdict, GaugeTone> = {
  BLOCKED: 'bad',
  AT_RISK: 'warn',
  HEALTHY: 'good',
  PENDING: 'neutral',
}

export function HealthMeter({ model, verdict }: { model: QueueModel; verdict: Verdict }) {
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
      {/* PENDING has no score: the header says "—", so the meter is an empty
          track ("not measured"), never a marker parked at 0. */}
      <div className="mt-3">
        <GaugeBar
          variant="marker"
          gradient="health"
          tone={VERDICT_GAUGE_TONE[verdict]}
          value={verdict === 'PENDING' ? null : score}
          label="Queue health"
          valueText={`${score} of 100, ${pillLabel}`}
          ticks={HEALTH_TICKS}
        />
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
    tone === 'good'   ? 'var(--status-passed)' :
    tone === 'warn'   ? 'var(--status-broken)' :
    tone === 'bad'    ? 'var(--status-failed)' :
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

/**
 * How long the oldest open P0 has been open, on a one-week scale: the tone
 * bands are the KPI's own (2 and 5 days), lower is better.
 */
const OLDEST_P0_DOMAIN = [0, 7] as const
const OLDEST_P0_BANDS: GaugeBands = { direction: 'lower-is-better', thresholds: [2, 5] }

/**
 * The five defect KPIs. Owner decision OD-1 (Wave 2.5): a KPI glyph draws a
 * REAL series or a real scalar, or nothing. Four of these drew literal point
 * strings and fixed bars whatever the queue held; there is no defect history
 * to draw instead (`/analytics/defects` is a paged list with no window, and
 * `chart-data` has no defect metric), so they are gone. "Oldest open P0" is a
 * real number of days: it keeps a `GaugeBar`, drawn only when a P0 is open.
 */
export function DefectKpiStrip({ model }: { model: QueueModel }) {
  const oldestP0Days = model.oldestP0 ? Math.floor(model.oldestP0.ageMs / 86400000) : null
  const escapeRate = model.total > 0 ? Math.round((model.unlinkedTotal / model.total) * 100) : 0
  const oldestP0Gauge = oldestP0Days != null ? (
    <GaugeBar
      value={oldestP0Days}
      domain={OLDEST_P0_DOMAIN}
      tone={OLDEST_P0_BANDS}
      size="sm"
      label="Oldest open P0, days open on a one-week scale"
      valueText={`${oldestP0Days} ${oldestP0Days === 1 ? 'day' : 'days'} open`}
    />
  ) : undefined
  return (
    <section aria-label="Defect KPIs" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
      <KpiCell
        Icon={XCircle}
        label="Open defects"
        value={model.open + model.inProgress}
        tone={model.open + model.inProgress > 0 ? 'bad' : 'good'}
        meta={
          model.weeklyAdded > 0 || model.weeklyClosed > 0
            ? <><span style={{ color: model.weeklyAdded > model.weeklyClosed ? 'var(--status-failed)' : 'var(--status-passed)' }}>
                {model.weeklyAdded - model.weeklyClosed >= 0 ? '+' : ''}{model.weeklyAdded - model.weeklyClosed}
              </span> vs last week</>
            : <>no movement this week</>
        }
        isFirst
      />
      <KpiCell
        Icon={ShieldCheck}
        label="P0 / P1 open"
        value={`${model.p0Count}`}
        sub={`/ ${model.p1Count}`}
        tone={model.p0Count > 0 ? 'bad' : model.p1Count > 0 ? 'warn' : 'good'}
        meta={model.p0Count > 0 ? <>{model.p0Count} blocking release</> : <>no release blockers</>}
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
      />
      <KpiCell
        Icon={BarChart3}
        label="Escape rate"
        value={`${escapeRate}`}
        sub="%"
        tone={escapeRate > 10 ? 'bad' : escapeRate > 5 ? 'warn' : 'good'}
        meta={<>target ≤ 5% · last 30d</>}
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
        spark={oldestP0Gauge}
        isLast
      />
    </section>
  )
}

// ── Defects table ────────────────────────────────────────────────────────
type SortKey = 'age' | 'severity' | 'status'

function DefectsTable({
  rows, search, setSearch, sortKey, setSortKey,
}: {
  rows: DefectRow[]
  search: string
  setSearch: (s: string) => void
  sortKey: SortKey
  setSortKey: (k: SortKey) => void
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
            placeholder="Search defects, Jira keys…"
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
              <Th label="Jira" />
              <ThSort label="Age" active={sortKey === 'age'} onClick={() => setSortKey('age')} sortDir={sortKey === 'age' ? '↓' : undefined} />
              <Th label="Actions" align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center py-10 text-[var(--color-text-muted)]">
                  {search.trim() ? 'No defects match the search.' : 'No defects in this filter.'}
                </td>
              </tr>
            )}
            {sorted.map(r => <DefectRowEl key={r.id} row={r} />)}
          </tbody>
        </table>
      </div>
    </CardShell>
  )
}

function DefectRowEl({ row }: { row: DefectRow }) {
  const isP0 = row.severity === 'P0'
  const sevPalette: Record<Severity, { bg: string; bd: string; fg: string }> = {
    P0: { bg: 'color-mix(in srgb, var(--status-failed) 15%, transparent)',     bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',     fg: 'var(--status-failed)' },
    P1: { bg: 'color-mix(in srgb, var(--status-broken) 15%, transparent)',    bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',    fg: 'var(--status-broken)' },
    P2: { bg: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',    bd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',    fg: 'var(--color-accent)' },
    P3: { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)',     fg: 'var(--color-text-muted)' },
  }
  const sev = sevPalette[row.severity]
  const statusColor = row.statusKey === 'OPEN' ? 'var(--status-failed)'
    : row.statusKey === 'IN_PROGRESS' ? 'var(--status-broken)'
    : row.statusKey === 'RESOLVED' ? 'var(--status-passed)'
    : 'var(--color-text-muted)'
  const ageColor = row.ageTone === 'bad' ? 'var(--status-failed)'
    : row.ageTone === 'warn' ? 'var(--status-broken)'
    : row.ageTone === 'good' ? 'var(--status-passed)'
    : 'var(--color-text)'

  // The one real row action: open the linked Jira ticket. A row without a
  // safe ticket URL has nothing to open, so it renders no action at all
  // (no "Link Jira" / "Assign" button until those flows exist).
  const jiraHref = row.jira_ticket_url && isSafeExternalUrl(row.jira_ticket_url)
    ? row.jira_ticket_url
    : null

  return (
    <tr
      style={{
        background: isP0 ? 'color-mix(in srgb, var(--status-failed) 4%, transparent)' : 'transparent',
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
        {row.isUnlinked ? (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] italic font-mono"
            style={{ background: 'transparent', border: '1px dashed var(--color-border)', color: 'var(--color-text-muted)' }}
          >
            unlinked
          </span>
        ) : jiraHref ? (
          <a
            href={jiraHref}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[10.5px] font-mono px-1.5 py-0.5 rounded-md"
            style={{ background: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--color-accent) 25%, transparent)', color: 'var(--color-accent)' }}
          >
            {row.jiraKey ?? row.jira_ticket_id}
            <ExternalLink className="h-2.5 w-2.5" />
          </a>
        ) : (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] font-mono"
            style={{ background: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--color-accent) 25%, transparent)', color: 'var(--color-accent)' }}
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
            style={{ border: '1px solid color-mix(in srgb, var(--status-broken) 40%, transparent)', color: 'var(--status-broken)', background: 'color-mix(in srgb, var(--status-broken) 10%, transparent)' }}
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
        {jiraHref && (
          <a
            href={jiraHref}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Open ${row.jiraKey ?? row.keyLabel} in Jira`}
            className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-md border transition-colors"
            style={{
              color: 'var(--color-accent)',
              borderColor: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
              background: 'var(--color-accent-bg-soft)',
            }}
          >
            Open <ExternalLink className="h-3 w-3" />
          </a>
        )}
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
const BRIDGE_BADGE: Record<JiraBridgeState, { label: string; color: string }> = {
  connected:      { label: 'Connected',      color: 'var(--status-passed)' },
  not_configured: { label: 'Not configured', color: 'var(--color-text-muted)' },
  unknown:        { label: 'Unknown',        color: 'var(--status-broken)' },
}

function JiraBridgeCard({ model }: { model: QueueModel }) {
  const { config } = useIntegrationsConfig()
  const state = jiraBridgeState(config)
  const badge = BRIDGE_BADGE[state]
  // The host is whatever the workspace is pointed at. It used to be the
  // literal string 'jira' whether or not anything was configured.
  const host = config?.jira_domain ?? '—'
  const linkedCount = model.total - model.unlinkedTotal
  const linkedPct = model.total > 0 ? Math.round((linkedCount / model.total) * 100) : 100
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2 text-[var(--color-text)]">
          <ShieldCheck className="h-3.5 w-3.5" style={{ color: 'var(--color-accent)' }} />
          Jira bridge · <code className="font-mono text-[11.5px]" style={{ color: 'var(--color-accent)' }}>{host}</code>
        </span>
      }
      rightSlot={
        <span
          className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
          style={{ background: `color-mix(in srgb, ${badge.color} 15%, transparent)`, color: badge.color, border: `1px solid color-mix(in srgb, ${badge.color} 25%, transparent)` }}
        >
          <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: badge.color }} />
          {badge.label}
        </span>
      }
    >
      {state !== 'connected' && (
        <p className="px-4 pt-3 pb-0 m-0 text-[12px] text-[var(--color-text-secondary)]">
          {state === 'not_configured'
            ? <>No Jira workspace is configured, so nothing here is syncing. The link columns below reflect ticket ids already stored on these defects. <Link to="/settings/integrations" style={{ color: 'var(--color-accent)' }}>Configure Jira →</Link></>
            : <>Integration settings are readable by QA leads and admins only, so this view cannot confirm whether Jira is connected.</>}
        </p>
      )}
      <div className="px-4 py-3 flex flex-col gap-1.5 text-[12px]">
        <BridgeRow l="Linked defects" v={<>{linkedCount} / {model.total} <span className="text-[var(--color-text-muted)]">({linkedPct}%)</span></>} />
        <BridgeRow l="Unlinked" v={<span style={{ color: model.unlinkedTotal > 0 ? 'var(--status-broken)' : 'var(--status-passed)' }}>{model.unlinkedTotal}{model.unlinkedP0 > 0 ? ` · ${model.unlinkedP0} P0` : ''}</span>} />
      </div>
      <div
        className="flex items-center justify-between gap-2 px-4 py-2 text-[11.5px] text-[var(--color-text-muted)]"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <span>{state === 'connected' ? `Project key ${config?.jira_default_project_key ?? '—'}` : 'Not syncing'}</span>
        <Link
          to="/settings/integrations"
          className="font-medium hover:underline"
          style={{ color: 'var(--color-accent)' }}
        >
          Bridge settings →
        </Link>
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
                {v.bySev.P0 > 0 && <span style={{ flex: v.bySev.P0, background: 'var(--status-failed)' }} />}
                {v.bySev.P1 > 0 && <span style={{ flex: v.bySev.P1, background: 'var(--status-broken)' }} />}
                {v.bySev.P2 > 0 && <span style={{ flex: v.bySev.P2, background: 'var(--color-accent)' }} />}
                {v.bySev.P3 > 0 && <span style={{ flex: v.bySev.P3, background: 'var(--color-text-faint)' }} />}
              </div>
              <span className="text-[11.5px] tabular-nums text-right text-[var(--color-text-secondary)]">{v.total}</span>
            </div>
          )
        })}
        <div className="flex items-center gap-3.5 mt-2.5 text-[10.5px] text-[var(--color-text-muted)] flex-wrap">
          <Legend color="var(--status-failed)" label="P0" />
          <Legend color="var(--status-broken)" label="P1" />
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

  // P1: this page's saved views. Defects are project-wide (no release, no
  // window, no suite): a view keeps the status tab.
  const storedDays = useTimeWindowStore(s => s.days)
  const viewExtra = useMemo(() => ({ defects: { tab: activeTab } }), [activeTab])
  const applyViewExtra = useCallback((view: SavedView) => {
    const saved = view.filters?.defects
    const tab = saved && typeof saved === 'object' ? (saved as { tab?: unknown }).tab : undefined
    if (STATUS_TABS.some(t => t.id === tab)) setActiveTab(tab as StatusKey)
  }, [])
  const viewsMenu = useReportViewsMenu({
    route: '/defects',
    windowDays: storedDays,
    windowOptions: null,
    release: false,
    extraFilters: viewExtra,
    applyExtra: applyViewExtra,
  })

  const [intakeOpen, setIntakeOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('age')

  const intakeProjectId = !isAllProjects && activeProjectId ? activeProjectId : null
  const openIntake = () => {
    if (!intakeProjectId) {
      toast('Pick a single project to intake a defect', { icon: '🐞' })
      return
    }
    setIntakeOpen(true)
  }

  // Pull all statuses up-front so we can drive the verdict + counts.
  // `error` is read alongside `data`: a failed fetch leaves `allDefects`
  // empty, which the verdict below reads as PENDING and renders as
  // "queue empty" -- an all-clear produced by never having looked.
  const { data: allDefectsData, isLoading, error: defectsError, mutate: retryDefects } =
    useDefects(1, undefined)
  const allDefects = useMemo<DefectItem[]>(() => allDefectsData?.items ?? [], [allDefectsData])
  const model = useMemo(() => buildQueueModel(allDefects), [allDefects])
  const verdict = pickVerdict(model)

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
  if (defectsError && !allDefectsData) {
    return (
      <DataUnavailable
        error={defectsError}
        onRetry={() => void retryDefects()}
        testId="defects-data-unavailable"
      />
    )
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
      return <>{model.p0Count} P0 defect{model.p0Count === 1 ? '' : 's'} blocking release · {stale} stale</>
    }
    return <>{model.open + model.inProgress} open · {model.p1Count + model.p2Count + model.p3Count} non-P0</>
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      // Surface concrete next steps when the queue is empty but the
      // project may still have failures worth triaging. Three explicit
      // routes: review your failures on /my-failures, hand off
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
          are blocking the release.{' '}
          {/* The count only: nothing here knows why a ticket is missing. */}
          {model.unlinkedP0 > 0 ? `${model.unlinkedP0} ${model.unlinkedP0 === 1 ? 'has' : 'have'} no Jira ticket. ` : ''}
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
          {' '}<span className="text-[var(--color-text-muted)]">All open or in progress.</span>
        </>
      ),
    })
  }
  if (model.unlinkedTotal > 0) {
    issues.push({
      tone: 'info',
      Icon: AlertTriangle,
      body: (
        <>
          {/* No "from this week" (the count is every unlinked defect in the
              list) and no claim about an auto-link rule the page cannot see. */}
          <strong>{model.unlinkedTotal} defect{model.unlinkedTotal === 1 ? ' has' : 's have'} no Jira ticket</strong>
          {' '}— {model.unlinkedTotal === 1 ? 'it exists' : 'they exist'} only in TestLookup.
        </>
      ),
    })
  }

  // The verdict's one real action. While P0s are open there is no triage
  // flow to start, so the card shows no button rather than a stub.
  const verdictCta: Cta | undefined = model.p0Count > 0
    ? undefined
    : { label: 'New defect', onClick: openIntake }

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
                <span style={{ color: 'var(--status-failed)' }}>{model.p0Count} P0 blocking release</span>
              </>
            )}
            {/* This line already says "blocking release", so a reader with one
                selected in the header takes these counts as that release's.
                They are the project's: `/analytics/defects` has no release
                dimension, and a defect raised against an earlier release can
                still be open now. */}
            <AllReleasesBadge reason="Defects are tracked per project, not per release — one raised against an earlier release can still be open now. Whether a defect blocks a particular release is decided by the release gate." />
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
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
        cta={verdictCta}
      />

      <DefectKpiStrip model={model} />

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          <DefectsTable
            rows={visibleRows}
            search={search}
            setSearch={setSearch}
            sortKey={sortKey}
            setSortKey={setSortKey}
          />
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <JiraBridgeCard model={model} />
          <ComponentBreakdown model={model} />
        </div>
      </div>

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
