/**
 * Defects — the UX redesign's page template (P3, `02-design-spec.md` §2; the
 * page has no §5 row), at 1440 x 900:
 *
 *   Header  → `PageHeader` (compact, **?** = the failure-analysis topic's
 *             "promoting to a defect"), actions in this order: Views
 *             (secondary) · the window (7 / 30 / 90 d: the KPI sparklines,
 *             Time to resolve and Escape rate) · "Only this release" (only
 *             with ONE release picked in the top bar; `?scope=release`) · the
 *             suite · "New defect" (primary). Still no toolbar row (the fold
 *             budget, W06 §0.17); the status filter belongs to the table and
 *             sits in the table's header beside the search.
 *   Verdict → `StatusBanner` in the "Defect queue verdict" landmark: the
 *             queue state (Release blocked / At risk / Queue healthy /
 *             Pending), the P0s, the defects with no Jira ticket, the queue
 *             health score, this week's net change (the trend's last 7 days).
 *             "All releases" while the toggle is off and a release is picked;
 *             "Affecting {release}" while it is on.
 *   KPIs    → `KpiStrip` ("Defect KPIs"): Open · P0 / P1 open · Time to
 *             resolve (each with the window's sparkline, values from the
 *             trend's totals over EVERY defect in scope) · Escape rate
 *             (DEF-03, "Not measured" below the sample floor) · Oldest open
 *             P0 (its one real gauge).
 *   PRIMARY → the defects table (`data-primary`): Key · Title · Severity ·
 *             Status · Owner · Jira · Age · Actions, filtered by the status
 *             tabs (the `Tabs` primitive since P6; state in localStorage and
 *             saved views, not `?tab=`) and the search in its header. A row
 *             does not expand; its actions are "Open in Jira" (every role)
 *             and the "Defect actions" menu (QA engineer and above: status,
 *             assignee, Jira link — `DefectRowActions`), whose answer
 *             replaces the row in place. `?defect=<id>` (the activity feed's
 *             link) highlights and scrolls to that row.
 *   Tabs    → `?tab=`: "Where defects live" (open defects by component,
 *             severity stacked) · "Jira bridge".
 *   Below   → Disclosure "How queue health is computed": the health meter and
 *             its four weighted dimensions (Ownership is back: the list has
 *             owners since W06 DEF-02).
 *
 * UX redesign P2: a control renders only when it does something real.
 *
 * Data: the rows come from useDefects(...) (every defect in scope, capped at
 * 1,000); the KPI values and lines from useDefectTrend (server totals, so the
 * cap never shows in a tile), the escape rate from useDefectEscapeRate.
 *   - severity     ← the server's `priority` (P0-P3), else the stored
 *                    severity, else failure_category + ai_confidence_score
 *   - key          ← jira_ticket_id, else the defect id's first 8 characters
 *   - owner        ← assignee_name, else owner_team (and where it came from)
 *   - jira link    ← jira_ticket_id / jira_ticket_url (safe URLs only)
 *   - age          ← created_at
 *   - bridge state ← derived from per-row jira_ticket_id presence
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Bug, ExternalLink, Plus, Search, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import StatusBanner, { type BannerFact, type BannerState } from '@/components/ui/StatusBanner'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import WindowPicker from '@/components/ui/WindowPicker'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import Disclosure from '@/components/ui/Disclosure'
import { helpTopicParam } from '@/components/help/helpTopics'
import DefectIntakeModal from '@/components/defects/DefectIntakeModal'
import DefectRowActions from '@/components/defects/DefectRowActions'
import GaugeBar from '@/components/charts/GaugeBar'
import Sparkline, { type SparklineTone } from '@/components/charts/Sparkline'
import type { GaugeBands, GaugeTick, GaugeTone } from '@/components/charts/gaugeBar.model'
import { refreshDefects, useDefects } from '@/hooks/useMetrics'
import { refreshDefectInsights, useDefectEscapeRate, useDefectTrend } from '@/hooks/useDefectInsights'
import { jiraBridgeState, useIntegrationsConfig, type JiraBridgeState } from '@/hooks/useIntegrationsConfig'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { SavedView } from '@/services/savedViewsService'
import { isSafeExternalUrl } from '@/utils/safeUrl'
import { formatNumber } from '@/utils/formatters'
import type {
  DefectEscapeRateResponse,
  DefectItem,
  DefectResponse,
  DefectScopeQuery,
  DefectTrendResponse,
} from '@/types/analytics'

/** The page's help topic (the header's **?**). */
// Topic and section ("promoting to a defect"): PageHeader opens `topic#anchor`.
const HELP_TOPIC = helpTopicParam('/defects')

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

/** The verdict's words and hues: the banner's title and state, and the health meter in its Disclosure. */
interface VerdictTheme {
  label: string
  /** The banner's state (its pill word and hue). */
  banner: BannerState
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  BLOCKED: {
    label:  'Release blocked',
    banner: 'no_go',
    pillBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
  },
  AT_RISK: {
    label:  'At risk',
    banner: 'warn',
    pillBg: 'var(--gate-conditional-bg)',
    pillBd: 'var(--gate-conditional-border)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
  },
  HEALTHY: {
    label:  'Queue healthy',
    banner: 'ok',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
  },
  PENDING: {
    label:  'Pending',
    banner: 'pending',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
  },
}

// ── Queue-health model ───────────────────────────────────────────────────
type DimensionId = 'p0_throughput' | 'jira_coverage' | 'fix_velocity' | 'ownership'
// The design's weights. Ownership was dropped while the backend had no owner
// field (it scored every open defect as unowned); W06 DEF-02 gives every row
// its assignee or team, so it is back. The weights sum to 1, so the
// re-normalisation below leaves them as designed.
const WEIGHTS: Record<DimensionId, number> = {
  p0_throughput: 0.35,
  jira_coverage: 0.25,
  fix_velocity:  0.20,
  ownership:     0.20,
}
const WEIGHT_SUM = Object.values(WEIGHTS).reduce((a, b) => a + b, 0)

/** The disclosure's sentence on what the Ownership dimension counts. */
const OWNERSHIP_SENTENCE =
  "Ownership counts open defects with an assignee or a team (their own, or one the project's ownership rules give them)."

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
  /** The row's status word: "Invalid" and the legacy "Closed" share the Closed tab. */
  statusLabel: string
  /** Has an assignee or a team (the Ownership dimension). */
  isOwned: boolean
  /** The list row as the server sent it (the row menu PATCHes it; `severity` above is the P-level). */
  source: DefectItem
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

// The defects.severity column's values, in the page's P0-P3 vocabulary (the
// intake maps P0-P3 onto them; analytics_service._SEVERITY_FROM_PRIORITY).
const SEVERITY_FROM_STORED: Record<string, Severity> = { CRITICAL: 'P0', HIGH: 'P1', MEDIUM: 'P2', LOW: 'P3' }

const PRIORITIES: readonly Severity[] = ['P0', 'P1', 'P2', 'P3']

/**
 * The row's P-level. W06: the server derives it (`defect_status.priority_sql`,
 * the same rule the KPI totals and the trend read), so `priority` wins; the
 * rule below is the fallback for a row without it (an old fixture, a cached
 * payload) and stays byte-for-byte the server's (one case table is asserted
 * on both sides).
 */
function severityOf(d: DefectItem): Severity {
  const server = (d.priority ?? '').toUpperCase() as Severity
  return PRIORITIES.includes(server) ? server : classifySeverity(d)
}

function classifySeverity(d: DefectItem): Severity {
  // The defect's own severity when it has one: the P0-P3 a person chose in
  // New defect, or the one its creator set. It was always synthesised from
  // the category, so a P3 product bug read P1 (browser E2E pass, 2026-10-08).
  const stored = SEVERITY_FROM_STORED[(d.severity ?? '').toUpperCase()]
  if (stored) return stored
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

/**
 * The tab a status word belongs to. INVALID (a person's "not a bug", or a
 * review rejection) is closed: it sits under Closed with the label "Invalid".
 * An unknown word still reads as Open — a drifted word must stay visible in a
 * queue, never vanish from every tab.
 */
function statusKeyOf(s: string): StatusKey {
  const u = (s ?? '').toUpperCase()
  if (u === 'OPEN') return 'OPEN'
  if (u === 'IN_PROGRESS' || u === 'IN PROGRESS' || u === 'IN-PROGRESS') return 'IN_PROGRESS'
  if (u === 'RESOLVED') return 'RESOLVED'
  if (u === 'CLOSED' || u === 'INVALID') return 'CLOSED'
  return 'OPEN'
}

const STATUS_LABEL: Record<Exclude<StatusKey, 'ALL'>, string> = {
  OPEN: 'Open',
  IN_PROGRESS: 'In progress',
  RESOLVED: 'Resolved',
  CLOSED: 'Closed',
}

function statusLabelOf(s: string, key: StatusKey): string {
  if ((s ?? '').toUpperCase() === 'INVALID') return 'Invalid'
  return key === 'ALL' ? 'Open' : STATUS_LABEL[key]
}

const isActive = (r: { statusKey: StatusKey }) => r.statusKey === 'OPEN' || r.statusKey === 'IN_PROGRESS'

/** Where a team that is not the defect's own came from (the Owner cell's muted line). */
const OWNER_SOURCE_LINE: Record<string, string> = {
  rule: 'from ownership rules',
  component_map: 'from the component map',
  test_label: "from the test's owner label",
}

/** The Owner cell's lines: the assignee over their team, else the team over its source, else "Unassigned". */
function ownerLines(d: DefectItem): { primary: string | null; secondary: string | null } {
  const name = d.assignee_name?.trim() || null
  const team = d.owner_team?.trim() || null
  if (name) return { primary: name, secondary: team }
  if (team) return { primary: team, secondary: (d.owner_source && OWNER_SOURCE_LINE[d.owner_source]) || null }
  return { primary: null, secondary: null }
}

/**
 * What the Jira cell says for an unlinked row whose approval tried to file a
 * ticket (W06 DEF-06), instead of "unlinked"; `null` keeps "unlinked".
 */
function filingLabel(d: DefectItem): string | null {
  switch (d.jira_filing_status) {
    case 'not_filed':
      if (d.jira_filing_reason === 'offline_mode') return 'not filed (offline)'
      if (d.jira_filing_reason === 'disabled') return 'not filed (Jira off)'
      if (d.jira_filing_reason === 'not_configured') return 'not filed (Jira not configured)'
      return 'not filed'
    case 'failed':
      return 'filing failed'
    case 'unknown':
      return 'filing unconfirmed'
    default:
      return null
  }
}

/** A real identifier for the row: its Jira ticket when linked, otherwise the
 *  defect's own id, shortened. It used to be `EVG-####`, a hash of the id
 *  dressed as a ticket number that did not exist anywhere. */
function buildKeyLabel(d: DefectItem): string {
  return d.jira_ticket_id || d.id.slice(0, 8)
}

function componentOf(d: DefectItem): string {
  if (d.component?.trim()) return d.component.trim()
  if (d.suite_name) return d.suite_name
  // Fallback: take the first segment of the test name before a "."/"_" / "/".
  const first = (d.test_name ?? '').split(/[._/]/, 1)[0] ?? 'unknown'
  return first || 'unknown'
}

function buildRow(d: DefectItem): DefectRow {
  const severity = severityOf(d)
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
    // Its own title first: a defect without a test read its category
    // ("product bug") in place of the title typed for it.
    titleText: d.title?.trim() || d.test_name || (d.failure_category || 'Untitled defect').toLowerCase().replace(/_/g, ' '),
    subtitleText: d.suite_name
      ? `${d.suite_name} suite · from ${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`
      : `${(d.failure_category || 'analysis').toLowerCase().replace(/_/g, ' ')}`,
    keyLabel: buildKeyLabel(d),
    jiraKey: d.jira_ticket_id ?? null,
    statusKey,
    statusLabel: statusLabelOf(d.resolution_status, statusKey),
    isOwned: Boolean(d.assignee_name?.trim() || d.owner_team?.trim()),
    source: d,
  }
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 33) return 'warn'
  return 'bad'
}

/**
 * `serverMttr` (W06 D-18, one MTTR on the page): the trend's windowed mean
 * time to resolve, once it has loaded — the tile and the Fix-velocity
 * dimension then read the same number. `null` inside it is "nothing resolved
 * in the window". Absent, the model derives one from the loaded rows.
 */
function buildQueueModel(rawDefects: DefectItem[], serverMttr?: { days: number | null }): QueueModel {
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
  const rowMttr = closures.length === 0
    ? null
    : closures.reduce((sum, r) => {
        const start = new Date(r.created_at).getTime()
        const end   = new Date(r.resolved_at).getTime()
        return sum + Math.max(0, (end - start) / 86400000)
      }, 0) / closures.length
  const mttrDays = serverMttr ? serverMttr.days : rowMttr

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
  // Ownership: of the ACTIVE rows (a closed defect needs no owner), the share
  // with an assignee or a team; nothing active → nothing unowned → 100.
  const activeRows = rows.filter(isActive)
  const ownershipScore = activeRows.length > 0
    ? (activeRows.filter(r => r.isOwned).length / activeRows.length) * 100
    : 100

  // Each dimension carries its RE-NORMALISED weight (design weight / sum), so
  // the tiles' percentages add up to 100 and the composite is their weighted
  // mean on 0-100.
  const dimensions: DimensionScore[] = [
    { id: 'p0_throughput', label: 'P0 throughput', score: p0Throughput,      weight: WEIGHTS.p0_throughput / WEIGHT_SUM, tone: toneFor(p0Throughput) },
    { id: 'jira_coverage', label: 'Jira coverage', score: jiraCoverageScore, weight: WEIGHTS.jira_coverage / WEIGHT_SUM, tone: toneFor(jiraCoverageScore) },
    { id: 'fix_velocity',  label: 'Fix velocity',  score: fixVelocityScore,  weight: WEIGHTS.fix_velocity / WEIGHT_SUM,  tone: toneFor(fixVelocityScore) },
    { id: 'ownership',     label: 'Ownership',     score: ownershipScore,    weight: WEIGHTS.ownership / WEIGHT_SUM,     tone: toneFor(ownershipScore) },
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

// ── The status filter (the table's, in its header) ───────────────────────
/**
 * The table's status filter: the `Tabs` primitive (UX redesign P6), each tab
 * with its count. Its state stays the page's own — remembered in
 * localStorage and kept by a saved view — not `?tab=`, which belongs to the
 * section tabs under the table.
 */
function StatusTabs({
  active, onChange, counts,
}: {
  active: StatusKey
  onChange: (s: StatusKey) => void
  counts: Record<StatusKey, number>
}) {
  const items: TabItem<StatusKey>[] = STATUS_TABS.map(({ id, label }) => ({ id, label, count: counts[id] }))
  return <Tabs items={items} value={active} onChange={onChange} ariaLabel="Status filter" />
}

// ── "How queue health is computed" (a Disclosure below the table) ─────────
// The verdict card's right half: the composite meter and its four weighted
// dimensions, one column each.
function QueueHealthDetails({ model, verdict }: { model: QueueModel; verdict: Verdict }) {
  return (
    <div data-queue-health="" className="flex flex-col gap-3.5">
      <HealthMeter model={model} verdict={verdict} />
      <p data-ownership-note="" className="m-0 text-[12px] text-[var(--color-text-muted)]">{OWNERSHIP_SENTENCE}</p>
      <DimensionGrid dimensions={model.dimensions} />
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
    <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${dimensions.length}, minmax(0, 1fr))` }}>
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

// ── KPI strip ────────────────────────────────────────────────────────────

/**
 * How long the oldest open P0 has been open, on a one-week scale: the tone
 * bands are the KPI's own (2 and 5 days), lower is better.
 */
const OLDEST_P0_DOMAIN = [0, 7] as const
const OLDEST_P0_BANDS: GaugeBands = { direction: 'lower-is-better', thresholds: [2, 5] }

/** The window options: the KPI sparklines, Time to resolve and Escape rate. The table lists every defect. */
const DEFECT_WINDOWS = [7, 30, 90] as const
const WINDOW_TITLE = 'Window for the KPI sparklines, Time to resolve and Escape rate. The table lists every defect.'
/** U+2212, the closed count's sign in a point's hover text. */
const MINUS = '−'

/**
 * A tile's trend line over the window, or — with fewer than two measured
 * points — the words for why there is none (Overview's pattern). Counts run
 * from 0 to the series' top: a zoomed [min, max] turns 40 → 42 into a cliff.
 */
function trendSpark(
  label: string,
  days: number,
  series: readonly (number | null)[],
  tone: SparklineTone,
  pointTitles: readonly (string | null)[],
  format?: (v: number) => string,
): ReactNode {
  const measured = series.filter((v): v is number => v !== null && Number.isFinite(v))
  if (measured.length >= 2) {
    return (
      <Sparkline
        series={series}
        label={`${label} per day, last ${days} days`}
        tone={tone}
        domain={[0, Math.max(1, ...measured)]}
        format={format}
        pointTitles={pointTitles}
        area
        className="w-24"
      />
    )
  }
  const hint = `${days}d · not enough data for a trend line`
  return (
    <span data-spark-hint="" title={hint} className="block max-w-[9.5rem] text-right text-[11px] leading-tight text-[var(--color-text-faint)]">
      {hint}
    </span>
  )
}

/** The escape-rate request as the strip reads it (SWR's three fields). */
interface EscapeState {
  data?: DefectEscapeRateResponse
  error?: unknown
  isLoading?: boolean
}

/**
 * DEF-03's tile: the share of the window's defects that affect a shipped
 * release and were filed after it shipped. Below the server's sample floor it
 * reads "Not measured" with the server's reason — never 0 %.
 */
function EscapeRateTile({ escape }: { escape: EscapeState }) {
  const data = escape.data
  let value = '—'
  let notMeasured = false
  let hint = 'Defects filed after a release they affect had shipped.'
  if (data) {
    // "Not measured" is the tile's small line under a "—" value, not the 24 px
    // value itself: as the value it ran into the tile's padding at 1280 px on
    // Windows' fonts, and CI's DejaVu is wider.
    notMeasured = !(data.measured && data.rate != null)
    value = notMeasured ? '—' : `${(data.rate as number).toFixed(1)}%`
    const lead = data.measured
      ? `Defects filed after a release they affect had shipped: ${data.escaped} of ${data.attributed} reported in the last ${data.window_days} days that affect a shipped release.`
      : (data.reason ?? `Fewer than ${data.sample_floor} defects reported in the last ${data.window_days} days affect a shipped release.`)
    hint = `${lead} Production outcomes in the window: ${data.outcomes.incidents} incident(s), ${data.outcomes.rollbacks} rollback(s).`
  } else if (escape.error) {
    hint = 'The escape rate could not be loaded.'
  }
  return (
    <div data-escape-measured={data ? String(data.measured && data.rate != null) : undefined} className="min-w-0 h-full">
      <MetricCard
        compact
        icon={null}
        title="Escape rate"
        hint={hint}
        loading={!data && !escape.error && Boolean(escape.isLoading)}
        metric={notMeasured ? { value, trend_text: 'Not measured', trend_direction: 'none' } : { value }}
      />
    </div>
  )
}

/**
 * The five defect KPIs (a `KpiStrip` of compact `MetricCard`s, UX redesign
 * P3). Owner decision OD-1 (Wave 2.5): a KPI glyph draws a REAL series or a
 * real scalar, or nothing. W06 DEF-04 gives the page a real history
 * (`/analytics/defects/trend`): Open, P0 / P1 and Time to resolve draw the
 * window's line and read their VALUES from its totals, which count every
 * defect in scope (the list stops at 1,000). While the trend loads or fails
 * they show the loaded rows' values and no line. "Oldest open P0" is a real
 * number of days: it keeps a `GaugeBar` beside its value, drawn only when a
 * P0 is open. "Escape rate" (DEF-03) replaced "Unlinked to Jira", whose share
 * is still the banner's "No Jira ticket" fact and the Jira bridge tab.
 *
 * Values only (P3): a tile with a caption line is ~99 px and put the table one
 * pixel inside the fold budget. The captions that were data moved to the
 * banner (this week's net change, the P0s blocking the release, the oldest
 * open P0's key).
 */
export function DefectKpiStrip({
  model,
  trend,
  escape = {},
}: {
  model: QueueModel
  /** The window's trend, once loaded (absent while it loads or after it failed). */
  trend?: DefectTrendResponse
  escape?: EscapeState
}) {
  const oldestP0Days = model.oldestP0 ? Math.floor(model.oldestP0.ageMs / 86400000) : null
  const totals = trend?.totals
  const points = trend?.points ?? []
  const n = trend?.window_days ?? points.length
  const openValue = totals ? formatNumber(totals.open) : formatNumber(model.open + model.inProgress)
  const p0p1Value = totals
    ? `${formatNumber(totals.p0_open)} / ${formatNumber(totals.p1_open)}`
    : `${model.p0Count} / ${model.p1Count}`
  const flow = (p: DefectTrendResponse['points'][number]) => `${p.date}: +${p.opened} opened, ${MINUS}${p.closed} closed`
  const openSpark = trend
    ? trendSpark('Open defects', n, points.map(p => p.open_backlog), 'neutral', points.map(p => `${flow(p)}, ${p.open_backlog} open`))
    : undefined
  const p0p1Spark = trend
    ? trendSpark(
        'P0 / P1 open',
        n,
        points.map(p => p.p0p1_open),
        (totals?.p0_open ?? 0) > 0 ? 'bad' : 'neutral',
        points.map(p => `${flow(p)}, ${p.p0p1_open} P0/P1 open`),
      )
    : undefined
  const mttrSpark = trend
    ? trendSpark(
        'Time to resolve',
        n,
        points.map(p => p.mttr_days),
        'neutral',
        points.map(p => (p.mttr_days != null
          ? `${p.date}: ${p.mttr_days.toFixed(1)}d to resolve (7-day mean)`
          : `${p.date}: nothing resolved in the 7 days`)),
        v => `${v.toFixed(1)}d`,
      )
    : undefined
  const oldestP0Gauge = oldestP0Days != null ? (
    <GaugeBar
      value={oldestP0Days}
      domain={OLDEST_P0_DOMAIN}
      tone={OLDEST_P0_BANDS}
      size="sm"
      width={88}
      label="Oldest open P0, days open on a one-week scale"
      valueText={`${oldestP0Days} ${oldestP0Days === 1 ? 'day' : 'days'} open`}
    />
  ) : undefined
  return (
    <section aria-label="Defect KPIs">
      <KpiStrip>
        {[
          <MetricCard key="open" compact icon={null} title="Open defects" metric={{ value: openValue }} sparkline={openSpark} />,
          <MetricCard key="p0p1" compact icon={null} title="P0 / P1 open" metric={{ value: p0p1Value }} sparkline={p0p1Spark} />,
          <MetricCard
            key="mttr"
            compact
            icon={null}
            title="Time to resolve"
            hint={trend
              ? `Mean time to resolve, over the defects resolved in the last ${n} days`
              : 'Mean time to resolve, over the defects resolved'}
            metric={{ value: model.mttrDays != null ? `${model.mttrDays.toFixed(1)}d` : '—' }}
            sparkline={mttrSpark}
          />,
          <EscapeRateTile key="escape" escape={escape} />,
          <MetricCard
            key="oldest"
            compact
            icon={null}
            title="Oldest open P0"
            metric={{ value: oldestP0Days != null ? `${oldestP0Days}d` : '—' }}
            sparkline={oldestP0Gauge}
          />,
        ]}
      </KpiStrip>
    </section>
  )
}

// ── Defects table ────────────────────────────────────────────────────────
type SortKey = 'age' | 'severity' | 'status'

function DefectsTable({
  rows, search, setSearch, sortKey, setSortKey, statusFilter, emptyQueue, highlightId, onUpdated, rowRef,
}: {
  rows: DefectRow[]
  search: string
  setSearch: (s: string) => void
  sortKey: SortKey
  setSortKey: (k: SortKey) => void
  /** The status tabs: the table's own filter, in its header beside the search. */
  statusFilter: React.ReactNode
  /** What the body says when the project has no defect at all (how to get one), instead of "no defects in this filter". */
  emptyQueue?: React.ReactNode
  /** The `?defect=` row, marked until another row is acted on. */
  highlightId?: string | null
  /** The row menu's answer (the server's row after a PATCH). */
  onUpdated: (row: DefectItem) => void
  /** Registers the highlighted row's element, so the page can scroll to it. */
  rowRef?: (el: HTMLTableRowElement | null) => void
}) {
  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const q = search.toLowerCase()
    return rows.filter(r =>
      r.titleText.toLowerCase().includes(q)
      || r.keyLabel.toLowerCase().includes(q)
      || (r.jiraKey ?? '').toLowerCase().includes(q)
      || r.componentName.toLowerCase().includes(q)
      // DEF-02 (D-15): owners are searchable instead of a separate owner chip.
      || (r.assignee_name ?? '').toLowerCase().includes(q)
      || (r.owner_team ?? '').toLowerCase().includes(q),
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
        <>
          {statusFilter}
          <div
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border"
            style={{ background: 'var(--color-bg-secondary)', borderColor: 'var(--color-border)' }}
          >
            <Search className="h-3 w-3 text-[var(--color-text-muted)]" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search defects, Jira keys, owners…"
              className="bg-transparent text-[12px] text-[var(--color-text)] outline-none w-[220px]"
              aria-label="Search defects"
            />
          </div>
        </>
      }
    >
      <div className="overflow-x-auto">
        <table aria-label="Defects" className="w-full text-[12.5px]">
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
                  {search.trim() ? 'No defects match the search.' : emptyQueue ?? 'No defects in this filter.'}
                </td>
              </tr>
            )}
            {sorted.map(r => (
              <DefectRowEl
                key={r.id}
                row={r}
                highlighted={highlightId === r.id}
                onUpdated={onUpdated}
                rowRef={highlightId === r.id ? rowRef : undefined}
              />
            ))}
          </tbody>
        </table>
      </div>
    </CardShell>
  )
}

/**
 * DEF-02's Owner cell: the assignee over their team, else the team over where
 * it came from (nothing for the defect's own), else a muted "Unassigned".
 * Free text from people and rules, so React text only; truncated at 160 px
 * with the whole of it on hover.
 */
function OwnerCell({ row }: { row: DefectRow }) {
  const { primary, secondary } = ownerLines(row)
  if (!primary) {
    return <span data-owner="none" className="text-[11.5px] text-[var(--color-text-muted)]">Unassigned</span>
  }
  const full = secondary ? `${primary} · ${secondary}` : primary
  return (
    <div data-owner={row.assignee_name?.trim() ? 'assignee' : 'team'} className="flex flex-col gap-0.5 min-w-0 max-w-[160px]" title={full}>
      <span className="text-[12px] text-[var(--color-text)] truncate">{primary}</span>
      {secondary && <span className="text-[10.5px] text-[var(--color-text-muted)] truncate">{secondary}</span>}
    </div>
  )
}

function DefectRowEl({
  row, highlighted = false, onUpdated, rowRef,
}: {
  row: DefectRow
  highlighted?: boolean
  onUpdated: (row: DefectItem) => void
  rowRef?: (el: HTMLTableRowElement | null) => void
}) {
  const isP0 = row.severity === 'P0'
  const filing = row.isUnlinked ? filingLabel(row) : null
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

  // "Open in Jira" for a row with a safe ticket URL (every role); the
  // "Defect actions" menu beside it renders for a QA engineer and above
  // (`DefectRowActions` decides, and needs the row's project).
  const jiraHref = row.jira_ticket_url && isSafeExternalUrl(row.jira_ticket_url)
    ? row.jira_ticket_url
    : null

  return (
    <tr
      ref={rowRef}
      data-defect-row={row.id}
      data-highlighted={highlighted ? 'true' : undefined}
      style={{
        background: highlighted
          ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)'
          : isP0 ? 'color-mix(in srgb, var(--status-failed) 4%, transparent)' : 'transparent',
        borderBottom: '1px solid var(--color-border)',
      }}
      className="transition-colors hover:bg-[var(--color-bg-hover)]"
    >
      <td
        className="font-mono text-[11.5px] text-[var(--color-text-secondary)] whitespace-nowrap"
        // The `?defect=` row's accent left border (on the cell: a <tr> draws no border of its own here).
        style={{ padding: '10px 12px', boxShadow: highlighted ? 'inset 3px 0 0 var(--color-accent)' : undefined }}
      >
        {row.keyLabel}
      </td>
      {/* w-full max-w-0: the title takes the room the other columns leave and
          truncates in it. Without it a nowrap title sized its column to the
          whole text, and at 1280 px a "not filed (offline)" Jira chip pushed
          Actions out of the card (W06 local demo). */}
      <td className="w-full max-w-0" style={{ padding: '10px 12px', minWidth: 240 }}>
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
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11.5px]" style={{ color: statusColor }}>
          <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
          {row.statusLabel}
        </span>
      </td>
      <td style={{ padding: '10px 12px' }}>
        <OwnerCell row={row} />
      </td>
      <td style={{ padding: '10px 12px' }}>
        {row.isUnlinked ? (
          // DEF-06: an approval that tried to file a ticket says what happened, the reason on hover.
          <span
            data-jira-filing={filing ? row.jira_filing_status ?? undefined : undefined}
            title={filing ? row.jira_filing_reason ?? undefined : undefined}
            className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] italic font-mono whitespace-nowrap"
            style={{
              background: 'transparent',
              border: `1px dashed ${filing && row.jira_filing_status !== 'not_filed' ? 'color-mix(in srgb, var(--status-broken) 45%, transparent)' : 'var(--color-border)'}`,
              color: filing && row.jira_filing_status !== 'not_filed' ? 'var(--status-broken)' : 'var(--color-text-muted)',
            }}
          >
            {filing ?? 'unlinked'}
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
        <span className="whitespace-nowrap text-[11.5px] tabular-nums font-semibold" style={{ color: ageColor }}>
          {row.ageLabel}
        </span>
      </td>
      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        <div className="inline-flex items-center justify-end gap-1.5">
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
          <DefectRowActions defect={row.source} onUpdated={onUpdated} />
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

/** The sections under the table (`?tab=`): two small cards that sat beside it. */
type SectionTab = 'components' | 'jira'
const SECTION_TABS: readonly TabItem<SectionTab>[] = [
  { id: 'components', label: 'Where defects live' },
  { id: 'jira', label: 'Jira bridge' },
]
const SECTION_TAB_IDS: readonly SectionTab[] = SECTION_TABS.map(t => t.id)

/** `?scope=release`: the "Only this release" toggle (URL-synced, and saved in a view as `defects.scope`). */
const SCOPE_PARAM = 'scope'
const SCOPE_RELEASE = 'release'
type ScopeMode = 'all' | 'release'
/** `?defect=<id>`: the activity feed's link to one defect (`events.py` `_ENTITY_ROUTES`). */
const DEFECT_PARAM = 'defect'

const RELEASE_SCOPE_TITLE =
  'Defects that affect the selected release: asserted impact, else where the defect was found. The release gate counts the same set.'
const ALL_RELEASES_REASON =
  'Defects are tracked per project, not per release — one raised against an earlier release can still be open now. ' +
  '"Only this release" narrows the page to the defects that affect the selected release, the set its release gate counts.'

/** What a saved view keeps beside the scope: the status tab and the release toggle. */
function savedDefectsExtra(view: SavedView): { tab?: StatusKey; scope?: ScopeMode } {
  const saved = view.filters?.defects
  if (!saved || typeof saved !== 'object') return {}
  const { tab, scope } = saved as { tab?: unknown; scope?: unknown }
  return {
    tab: STATUS_TABS.some(t => t.id === tab) ? (tab as StatusKey) : undefined,
    scope: scope === 'release' || scope === 'all' ? scope : undefined,
  }
}

/** The release's name for the scope chip: the list's envelope, else the trend's, when it names THAT release. */
function scopedReleaseName(releaseId: string, ...metas: (DefectResponse['meta'] | DefectTrendResponse['meta'])[]): string | null {
  for (const meta of metas) {
    const release = meta?.scope?.releases?.find(r => r.id === releaseId)
    if (release?.name) return release.name
  }
  return null
}

export default function DefectsPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  const [activeTab, setActiveTab] = useState<StatusKey>(() => {
    const saved = localStorage.getItem(TAB_KEY) as StatusKey | null
    return saved && STATUS_TABS.some(t => t.id === saved) ? saved : 'ALL'
  })
  useEffect(() => { localStorage.setItem(TAB_KEY, activeTab) }, [activeTab])
  const [sectionTab, setSectionTab] = useTabParam(SECTION_TAB_IDS, 'components')
  const [searchParams, setSearchParams] = useSearchParams()

  // DEF-05: "Only this release" (`?scope=release`). It applies only while ONE
  // release is picked in the top bar (`useReleaseScope` is null in All
  // projects mode and for a release of another project); otherwise the page is
  // today's deliberate all-releases view, and the three defect hooks send no
  // release at all.
  const releaseId = useReleaseScope()
  const scopeOn = searchParams.get(SCOPE_PARAM) === SCOPE_RELEASE
  const affecting = scopeOn && releaseId !== null
  const setScopeOn = useCallback((on: boolean) => {
    setSearchParams((current) => {
      const out = new URLSearchParams(current)
      if (on) out.set(SCOPE_PARAM, SCOPE_RELEASE)
      else out.delete(SCOPE_PARAM)
      return out
    }, { replace: true })
  }, [setSearchParams])

  // DEF-04: the window (the KPI sparklines, Time to resolve and Escape rate;
  // the table lists every defect). The global window, snapped to 7 / 30 / 90.
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, DEFECT_WINDOWS)

  // DEF-05: the suite (the shared single-suite pattern); it narrows the list
  // and the trend, never the escape rate (counted per release).
  const { selectedSuite, setSelectedSuite, suiteFilter, suiteNames } = usePageSuiteFilter()
  const { options: suiteOptions } = useSuiteOptions()

  // P1 saved views: the top-bar release, the window, the suite, and beside
  // them the status tab and the release toggle.
  const viewExtra = useMemo(
    () => ({ defects: { tab: activeTab, scope: (scopeOn ? 'release' : 'all') satisfies ScopeMode } }),
    [activeTab, scopeOn],
  )
  const applyViewExtra = useCallback((view: SavedView) => {
    const { tab, scope } = savedDefectsExtra(view)
    if (tab) setActiveTab(tab)
    setScopeOn(scope === 'release')
  }, [setScopeOn])
  const viewsMenu = useReportViewsMenu({
    route: '/defects',
    windowDays: days,
    windowOptions: DEFECT_WINDOWS,
    suite: { names: suiteNames, set: setSelectedSuite },
    release: true,
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

  // The one scope the three defect hooks share (the escape rate takes the
  // release only). Toggle off → no release_id / release_match at all.
  const releaseIds = useMemo(() => (affecting && releaseId ? [releaseId] : undefined), [affecting, releaseId])
  const listScope = useMemo<DefectScopeQuery>(
    () => ({ releaseIds, releaseMatch: releaseIds ? 'affects' : undefined, suiteName: suiteFilter }),
    [releaseIds, suiteFilter],
  )

  // Pull all statuses up-front so we can drive the verdict + counts.
  // `error` is read alongside `data`: a failed fetch leaves `allDefects`
  // empty, which the verdict below reads as PENDING and renders as
  // "queue empty" -- an all-clear produced by never having looked.
  const { data: allDefectsData, isLoading, error: defectsError, mutate: mutateDefects } =
    useDefects(undefined, listScope)
  const { data: trend } = useDefectTrend({ ...listScope, days })
  const escape = useDefectEscapeRate({ days, releaseIds })
  const allDefects = useMemo<DefectItem[]>(() => allDefectsData?.items ?? [], [allDefectsData])
  const serverMttr = trend ? trend.totals.mttr_days : undefined
  const model = useMemo(
    () => buildQueueModel(allDefects, serverMttr === undefined ? undefined : { days: serverMttr }),
    [allDefects, serverMttr],
  )
  const verdict = pickVerdict(model)

  // `?defect=<id>`: every status, no search, the row marked and scrolled to.
  // The mark stays until another row is acted on.
  const focusId = searchParams.get(DEFECT_PARAM)
  const [highlightId, setHighlightId] = useState<string | null>(null)
  // Adjusted while rendering when the link changes (React's pattern for
  // "reset state when a prop changes"), not in an effect: no extra commit.
  const [seenFocusId, setSeenFocusId] = useState<string | null>(null)
  if (focusId && focusId !== seenFocusId) {
    setSeenFocusId(focusId)
    setActiveTab('ALL')
    setSearch('')
    setHighlightId(focusId)
  }
  const scrolledFor = useRef<string | null>(null)
  const highlightRowRef = useCallback((el: HTMLTableRowElement | null) => {
    if (!el || !highlightId || scrolledFor.current === highlightId) return
    scrolledFor.current = highlightId
    el.scrollIntoView?.({ block: 'center' })
  }, [highlightId])

  // DEF-01: the row menu's answer replaces the row in place (the server's
  // row, so its tab, owner and Jira cell move at once), then everything
  // revalidates; the trend and the escape rate are re-read too.
  const replaceRow = useCallback((row: DefectItem) => {
    setHighlightId(current => (current && current !== row.id ? null : current))
    void mutateDefects(
      current => (current ? { ...current, items: current.items.map(i => (i.id === row.id ? row : i)) } : current),
      { revalidate: true },
    )
    void refreshDefectInsights()
  }, [mutateDefects])

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
        onRetry={() => void mutateDefects()}
        testId="defects-data-unavailable"
      />
    )
  }

  const theme = VERDICT_THEME[verdict]

  // Tab-filtered rows for the table
  const visibleRows = activeTab === 'ALL'
    ? model.rows
    : model.rows.filter(r => r.statusKey === activeTab)

  // The banner's headline after the verdict word (short: the banner is one line).
  const summaryNode: React.ReactNode = (() => {
    if (verdict === 'PENDING') return <>queue empty</>
    if (verdict === 'HEALTHY') return <>{model.total} total · 0 P0 blocking</>
    if (verdict === 'BLOCKED') {
      const stale = model.rows.filter(r => r.severity === 'P0' && r.ageTone === 'bad').length
      return <>{model.p0Count} P0 blocking release · {stale} stale</>
    }
    return <>{model.open + model.inProgress} open · {model.p1Count + model.p2Count + model.p3Count} non-P0</>
  })()

  // The facts: the verdict card's issue rows and its lede, as numbers. The
  // unlinked count with no reason for it (nothing here knows why a ticket is
  // missing), no auto-link rule. "Net this week" is the trend's last 7 days
  // once it has loaded (every defect in scope), else the loaded rows'.
  const netThisWeek = trend ? trend.totals.net_last_7d : model.weeklyAdded - model.weeklyClosed
  const bannerFacts: BannerFact[] = [
    {
      label: 'No Jira ticket',
      value: <>{model.unlinkedTotal}{model.unlinkedP0 > 0 ? ` (${model.unlinkedP0} P0)` : ''}</>,
    },
    model.oldestP0
      ? {
          label: 'Oldest open P0',
          value: <><code className="font-mono text-[12px]">{model.oldestP0.keyLabel}</code> · {model.oldestP0.ageLabel}</>,
        }
      : { label: 'Net this week', value: `${netThisWeek > 0 ? '+' : ''}${netThisWeek}` },
    { label: 'Queue health', value: verdict === 'PENDING' ? '—' : `${model.composite}/100` },
  ]

  const releaseName = affecting && releaseId ? scopedReleaseName(releaseId, allDefectsData?.meta, trend?.meta) : null
  const capped = allDefectsData ? allDefectsData.items.length < allDefectsData.total : false
  const highlightMissing = Boolean(highlightId && allDefectsData && !model.rows.some(r => r.id === highlightId))

  return (
    <PageShell className="space-y-4">
      <PageHeader
        compact
        title="Defects"
        helpTopic={HELP_TOPIC}
        actions={
          <>
            {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
            <span title={WINDOW_TITLE} className="inline-flex">
              <WindowPicker options={DEFECT_WINDOWS} />
            </span>
            {releaseId && (
              <button
                type="button"
                data-testid="defects-release-scope"
                aria-pressed={scopeOn}
                title={RELEASE_SCOPE_TITLE}
                onClick={() => setScopeOn(!scopeOn)}
                className="inline-flex items-center rounded-full border px-2.5 py-1 text-[12.5px] transition-colors"
                style={{
                  background: scopeOn ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
                  borderColor: scopeOn ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
                  color: scopeOn ? 'var(--color-accent)' : 'var(--color-text-muted)',
                }}
              >
                Only this release
              </button>
            )}
            <SuiteFilterSelect
              value={selectedSuite}
              onChange={setSelectedSuite}
              options={suiteOptions}
              allLabel="All suites"
            />
            <button
              type="button"
              onClick={openIntake}
              className="btn-primary inline-flex items-center gap-1 !px-3 !py-1.5 text-[13px]"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              New defect
            </button>
          </>
        }
      />

      <section aria-label="Defect queue verdict" aria-live="polite">
        <StatusBanner
          state={theme.banner}
          title={
            <span data-verdict={verdict} className="inline-flex flex-wrap items-baseline gap-x-1.5">
              <span>{theme.label}</span>{' '}
              <span aria-hidden="true" className="text-[var(--color-text-muted)]">·</span>{' '}
              <span className="font-normal">{summaryNode}</span>{' '}
              {/* While "Only this release" is off, the counts are every release's (a defect raised against an
                  earlier release can still be open now), so a picked release gets the "All releases" badge.
                  While it is on, the chip names the release the page is narrowed to. */}
              {affecting ? (
                <span
                  data-defects-scope-chip=""
                  title={RELEASE_SCOPE_TITLE}
                  className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
                  style={{
                    background: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
                    borderColor: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
                    color: 'var(--color-accent)',
                  }}
                >
                  Affecting {releaseName ?? 'the selected release'}
                </span>
              ) : (
                <AllReleasesBadge reason={ALL_RELEASES_REASON} />
              )}
            </span>
          }
          facts={bannerFacts}
        />
      </section>

      <DefectKpiStrip model={model} trend={trend} escape={escape} />

      {allDefectsData && capped && (
        <p data-defects-capped="" className="text-[12px] text-[var(--color-text-muted)]">
          {trend
            ? `The table and the tabs cover the newest ${allDefectsData.items.length} of ${allDefectsData.total} defects; the KPI tiles count all of them.`
            : `The counts and the table cover the newest ${allDefectsData.items.length} of ${allDefectsData.total} defects.`}
        </p>
      )}
      {allDefectsData && highlightMissing && (
        <p data-defect-missing="" className="text-[12px] text-[var(--color-text-muted)]">
          That defect is not among the newest {allDefectsData.items.length} loaded.
        </p>
      )}

      <section data-primary="" aria-label="All defects">
        <DefectsTable
          rows={visibleRows}
          search={search}
          setSearch={setSearch}
          sortKey={sortKey}
          setSortKey={setSortKey}
          statusFilter={<StatusTabs active={activeTab} onChange={setActiveTab} counts={model.countsByStatus} />}
          highlightId={highlightId}
          onUpdated={replaceRow}
          rowRef={highlightRowRef}
          emptyQueue={model.total === 0 ? (
            // Concrete next steps when the queue is empty but the project may
            // still have failures worth triaging: review your failures, hand
            // off to deep AI investigation, or create a defect manually.
            <>
              No defects in this project yet. To populate the queue:{' '}
              <Link to="/my-failures" className="text-[var(--color-accent)] hover:underline">review your assigned failures</Link>
              {', '}
              <Link to="/deep-investigate" className="text-[var(--color-accent)] hover:underline">run an AI investigation</Link>
              {', or use '}<strong>New defect</strong>{' above to create one manually.'}
            </>
          ) : undefined}
        />
      </section>

      <div data-defects-sections="">
        <Tabs items={SECTION_TABS} value={sectionTab} onChange={setSectionTab} ariaLabel="Defect sections" />
        <div
          role="tabpanel"
          aria-label={sectionTab === 'jira' ? 'Jira bridge' : 'Where defects live'}
          data-tab-panel={sectionTab}
          className="pt-3 grid gap-3.5"
          style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}
        >
          {sectionTab === 'components' ? <ComponentBreakdown model={model} /> : <JiraBridgeCard model={model} />}
        </div>
      </div>

      <Disclosure
        title="How queue health is computed"
        summary={verdict === 'PENDING' ? 'not measured' : `queue health ${model.composite} / 100`}
      >
        <QueueHealthDetails model={model} verdict={verdict} />
      </Disclosure>

      {intakeOpen && intakeProjectId && (
        <DefectIntakeModal
          projectId={intakeProjectId}
          onClose={() => setIntakeOpen(false)}
          onSuccess={() => {
            void refreshDefects()
            void refreshDefectInsights()
          }}
        />
      )}

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>
    </PageShell>
  )
}
