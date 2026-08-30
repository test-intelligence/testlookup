/**
 * Test Runs — verdict-led redesign per design_handoff_test_runs/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb + right cluster: 2 primary CTAs
 *             ("Trigger all failed (N)" danger style + "Deep all failed (N)") +
 *             All-pages toggle + window select + status select.
 *   Verdict → 1.45fr | 1fr split. Variants BROKEN / MIXED / HEALTHY / PENDING.
 *             Left: pulsing eyebrow → 26 px headline → lede → 3 issue rows
 *             → CTAs (Bisect from last green / Open intelligence / Hold deploys).
 *             Right: 44 px composite pipeline-health score (red→amber→green
 *             gradient + 33/66 ticks + marker dot) + 2×2 weighted dimension
 *             grid (Build success 40 % / Signature diversity 15 % / Fix
 *             velocity 25 % / Metadata coverage 20 %).
 *   Ribbon  → slim 4-stage workflow with skip-aware variant: Release context
 *             skipped when no release tag is attached, rendered informational.
 *   KPIs    → 5 cells with sparklines: Builds failed · Unique failures ·
 *             Avg pass rate · Last green build · Red streak.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Signature cluster card (groups runs by `passed,failed,total`
 *             tuple, surfaces the dominant cluster + outliers) → Runs table
 *             (selectable, per-row Intel/Trigger/Deep, bulk-action sub-header).
 *     Right → Last-green callout (bisect target) → 14-day build velocity
 *             grid → Recommended actions (Developer / QA / Release manager).
 *   Footer → Provenance line + decision-trail link.
 *
 * Out of scope (Phase 2 — README §"Open questions" + §13 step 7 caveats):
 *   - Bisect modal body (button wires up; modal is later).
 *   - Run-comparison modal body.
 *   - Decision-trail modal body — link wires up to existing toast for now.
 *   - Backend-computed signature: currently grouped client-side by the
 *     `(passed, failed, total)` tuple per README §12 q1 fallback.
 *
 * Data: derives every section from existing useRuns(...) + runsService. CTAs
 * that need new endpoints (bisect, hold deploys, run-comparison) emit toast
 * placeholders. Per-row Trigger / Deep + bulk-trigger preserve the existing
 * agentService.bulkTriggerPipelines call.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, ArrowRight, BarChart3, Check, ChevronRight,
  Clock, Code as CodeIcon, GitBranch, GitCompare, Layers, Search, ShieldCheck,
  Sparkles, Stethoscope, TrendingUp, Upload, Wrench, XCircle, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import UploadReportModal from '@/components/runs/UploadReportModal'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import agentService from '@/services/agentService'
import type { TestRun } from '@/types/runs'
import { shortAgo } from '@/utils/formatters'
import { buildCompareWithPreviousHref, findPreviousRunOfSuite } from '@/utils/runComparisons'

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24 hours, 0 = all time. NB: use 7 (a week), not 6, so the global
// 7-day default window (timeWindowStore) doesn't silently snap to a different
// value here than on every other page (Overview/Coverage/Trends/… all use 7).
const WINDOWS = [1, 7, 14, 30, 90, 0] as const
type Window = (typeof WINDOWS)[number]

const WINDOW_LABELS: Record<Window, string> = {
  1:  'Last 24 hours',
  7:  'Last 7 days',
  14: 'Last 14 days',
  30: 'Last 30 days',
  90: 'Last 90 days',
  0:  'All time',
}

const STATUS_FILTERS = ['', 'FAILED', 'PASSED', 'IN_PROGRESS'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]
const STATUS_LABELS: Record<StatusFilter, string> = {
  '':            'All statuses',
  FAILED:        'Failed',
  PASSED:        'Passed',
  IN_PROGRESS:   'In progress',
}

// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'BROKEN' | 'MIXED' | 'HEALTHY' | 'PENDING'

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
  BROKEN: {
    border: 'color-mix(in srgb, var(--status-failed) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-no-go)',
    eyebrowText: 'var(--status-failed)',
    gateText:    'var(--status-failed)',
    pillBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Pipeline broken',
    pulse:  true,
  },
  MIXED: {
    border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)',
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-broken) 10%, transparent), transparent 55%)',
    bar:    'var(--gate-conditional)',
    eyebrowText: 'var(--status-broken)',
    gateText:    'var(--status-broken)',
    pillBg: 'color-mix(in srgb, var(--status-broken) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Mixed',
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
    label:  'Pipeline healthy',
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

// ── Pipeline health model ────────────────────────────────────────────────
type DimensionId = 'build_success' | 'signature_diversity' | 'fix_velocity' | 'metadata_coverage'
const WEIGHTS: Record<DimensionId, number> = {
  build_success:       0.40,
  signature_diversity: 0.15,
  fix_velocity:        0.25,
  metadata_coverage:   0.20,
}

interface DimensionScore {
  id: DimensionId
  label: string
  score: number
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

interface SignatureCluster {
  signature: string                  // "passed|failed|total"
  passed: number
  failed: number
  total: number
  members: TestRun[]                 // runs in this cluster
}

interface PipelineModel {
  composite: number
  dimensions: DimensionScore[]
  totalRuns: number
  failedRuns: number
  passedRuns: number
  inProgressRuns: number
  avgPassRate: number
  redStreak: number                  // consecutive failures from newest backwards
  lastGreen: TestRun | null
  /** Most recent failed run. Paired with ``lastGreen`` to populate the
   *  bisect modal (``/runs/compare?left=<green>&right=<failed>``). */
  latestFailedRun: TestRun | null
  hoursSinceLastGreen: number | null
  primaryCluster: SignatureCluster | null
  outlierClusters: SignatureCluster[]
  hasMissingMetadata: boolean
  metadataCoveragePct: number
  velocityCells: VelocityCell[]      // 14 cells for the velocity grid
  uniqueClustersCount: number
}

interface VelocityCell {
  kind: 'pass' | 'fail' | 'empty'
  buildLabel: string | null
  isLast: boolean
}

function isFailed(s: string): boolean {
  return /fail|broken|error/i.test(s)
}
function isPassed(s: string): boolean {
  return /^pass(ed)?$/i.test(s) || /success/i.test(s)
}
function isInProgress(s: string): boolean {
  return /running|in[_-]?progress|pending|queued/i.test(s)
}

function clusterSignature(r: TestRun): string {
  return `${r.passed_tests}|${r.failed_tests}|${r.total_tests}`
}

function buildVelocityCells(runs: TestRun[]): VelocityCell[] {
  // Take the most-recent 14 runs, oldest first. Pad with "empty" cells if
  // fewer than 14 runs exist.
  const recent = [...runs]
    .sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    .slice(0, 14)
    .reverse()
  const cells: VelocityCell[] = recent.map((r, i) => ({
    kind: isFailed(r.status) ? 'fail' : isPassed(r.status) ? 'pass' : 'empty',
    buildLabel: `#${r.build_number}`,
    isLast: i === recent.length - 1,
  }))
  while (cells.length < 14) cells.push({ kind: 'empty', buildLabel: null, isLast: false })
  return cells
}

function computeRedStreak(runs: TestRun[]): number {
  // Walk newest → oldest, count consecutive failures.
  const sorted = [...runs].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
  let streak = 0
  for (const r of sorted) {
    if (isFailed(r.status)) streak++
    else break
  }
  return streak
}

function findLastGreen(runs: TestRun[]): TestRun | null {
  const sorted = [...runs].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
  return sorted.find(r => isPassed(r.status)) ?? null
}

function findLatestFailed(runs: TestRun[]): TestRun | null {
  // The bisect TARGET — the most recent failing run. Paired with
  // ``findLastGreen`` (the BASELINE) to form the left/right of the
  // ``/runs/compare`` query that the Bisect CTA navigates to.
  const sorted = [...runs].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
  return sorted.find(r => isFailed(r.status)) ?? null
}

/** Why an ``?upload=1`` deep link could not open the upload panel.
 *
 *  Originally this also reported a 'scope' reason: the panel refused to open
 *  under All Projects, and the page explained why. That explanation was not
 *  the fix it looked like — a user who clicks "Upload Report" wants to upload,
 *  and being told to go and change a selector first is still a dead end. The
 *  modal now asks for the project itself, so scope no longer blocks anything.
 *
 *  Role remains a genuine block: the backend rejects the upload with 403, so
 *  opening the panel would only lead to a failure at submit time. */
export function resolveUploadBlockedReason(input: {
  /** ``?upload=1`` present *and* the manual_upload flag on. */
  uploadRequested: boolean
  isQaEngineer: boolean
}): 'role' | null {
  if (!input.uploadRequested) return null
  if (!input.isQaEngineer) return 'role'
  return null
}

/** Construct the deep-link to /runs/compare for the bisect modal. Returns
 *  null when either side is missing (no green run found, or no failing run
 *  to compare against) — call sites should then either disable the button
 *  or surface a helpful "nothing to bisect" toast. */
export function buildBisectHref(model: {
  lastGreen: TestRun | null
  latestFailedRun: TestRun | null
}): string | null {
  if (!model.lastGreen || !model.latestFailedRun) return null
  const params = new URLSearchParams()
  params.set('mode', 'manual')
  params.set('left', model.lastGreen.id)
  params.set('right', model.latestFailedRun.id)
  // Prefer the failing run's suite (the suite the user is investigating);
  // fall back to the green run's suite so the suite filter on the compare
  // page is always populated when we have any signal at all.
  const suite = model.latestFailedRun.primary_suite_name
    || model.lastGreen.primary_suite_name
    || ''
  if (suite) params.set('suite', suite)
  return `/runs/compare?${params.toString()}`
}

function hoursSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return null
  return Math.round(ms / 3600000)
}

function clusterRuns(runs: TestRun[]): { primary: SignatureCluster | null; outliers: SignatureCluster[] } {
  // Group failed runs by signature. The largest cluster is "primary" — the
  // rest become "outliers".
  const failed = runs.filter(r => isFailed(r.status))
  const groups = new Map<string, SignatureCluster>()
  for (const r of failed) {
    const sig = clusterSignature(r)
    const g = groups.get(sig)
    if (g) {
      g.members.push(r)
    } else {
      groups.set(sig, { signature: sig, passed: r.passed_tests, failed: r.failed_tests, total: r.total_tests, members: [r] })
    }
  }
  const sorted = [...groups.values()].sort((a, b) => b.members.length - a.members.length)
  if (sorted.length === 0) return { primary: null, outliers: [] }
  return { primary: sorted[0], outliers: sorted.slice(1) }
}

function shortSignatureLabel(c: SignatureCluster): string {
  // Use a stable hash of the signature for visual consistency.
  let hash = 0
  for (let i = 0; i < c.signature.length; i++) hash = ((hash << 5) - hash) + c.signature.charCodeAt(i)
  const hex = (Math.abs(hash) >>> 0).toString(16).padStart(6, '0').slice(0, 3)
  return `${hex}-cluster`
}

function buildPipelineModel(runs: TestRun[]): PipelineModel {
  const totalRuns      = runs.length
  const failedRuns     = runs.filter(r => isFailed(r.status)).length
  const passedRuns     = runs.filter(r => isPassed(r.status)).length
  const inProgressRuns = runs.filter(r => isInProgress(r.status)).length
  const avgPassRate    = totalRuns > 0
    ? runs.reduce((s, r) => s + (Number(r.pass_rate) || 0), 0) / totalRuns
    : 0
  const redStreak = computeRedStreak(runs)
  const lastGreen = findLastGreen(runs)
  const latestFailedRun = findLatestFailed(runs)

  // Metadata coverage — % of runs that have branch + release_name + duration.
  const withMeta = runs.filter(r => !!r.branch && !!r.release_name && (r.duration_ms ?? 0) > 0).length
  const metadataCoveragePct = totalRuns > 0 ? (withMeta / totalRuns) * 100 : 0
  const hasMissingMetadata = totalRuns > 0 && metadataCoveragePct < 80

  // Cluster failures by their (passed, failed, total) signature.
  const { primary, outliers } = clusterRuns(runs)
  const totalUniqueClusters = (primary ? 1 : 0) + outliers.length

  // Build success — straight % passed.
  const buildSuccessScore = totalRuns > 0 ? (passedRuns / totalRuns) * 100 : 0
  // Signature diversity — when failures cluster heavily into one signature,
  // the score is LOW (one root cause masquerading as many incidents).
  // diversity = uniqueClusters / failedRuns (clipped to 100). 0 failures = 100.
  const signatureDiversityScore = failedRuns > 0
    ? Math.min(100, (totalUniqueClusters / failedRuns) * 100)
    : 100
  // Fix velocity — proxy: hours since last green, mapped 0h→100, 72h→0.
  const hoursSinceLastGreen = lastGreen ? hoursSince(lastGreen.created_at) : null
  const fixVelocityScore = hoursSinceLastGreen == null
    ? 0
    : Math.max(0, 100 - (hoursSinceLastGreen / 72) * 100)
  const metadataCoverageScore = metadataCoveragePct

  const dimensions: DimensionScore[] = [
    { id: 'build_success',       label: 'Build success',       score: buildSuccessScore,       weight: WEIGHTS.build_success,       tone: toneFor(buildSuccessScore) },
    { id: 'signature_diversity', label: 'Signature diversity', score: signatureDiversityScore, weight: WEIGHTS.signature_diversity, tone: toneFor(signatureDiversityScore) },
    { id: 'fix_velocity',        label: 'Fix velocity',        score: fixVelocityScore,        weight: WEIGHTS.fix_velocity,        tone: toneFor(fixVelocityScore) },
    { id: 'metadata_coverage',   label: 'Metadata coverage',   score: metadataCoverageScore,   weight: WEIGHTS.metadata_coverage,   tone: toneFor(metadataCoverageScore) },
  ]
  const composite = Math.round(dimensions.reduce((sum, d) => sum + d.score * d.weight, 0))

  return {
    composite, dimensions,
    totalRuns, failedRuns, passedRuns, inProgressRuns,
    avgPassRate,
    redStreak, lastGreen, latestFailedRun,
    hoursSinceLastGreen,
    primaryCluster: primary, outlierClusters: outliers,
    hasMissingMetadata, metadataCoveragePct,
    velocityCells: buildVelocityCells(runs),
    uniqueClustersCount: totalUniqueClusters,
  }
}

function toneFor(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 33) return 'warn'
  return 'bad'
}

function pickVerdict(model: PipelineModel): Verdict {
  if (model.totalRuns === 0) return 'PENDING'
  if (model.failedRuns === 0) return 'HEALTHY'
  if (model.composite < 33) return 'BROKEN'
  return 'MIXED'
}

// ── Atoms ──────────────────────────────────────────────────────────────────
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

// Danger button — used by "Trigger all failed" per README §4.1
function DangerBtn({
  children, onClick, title, disabled,
}: { children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md border transition-colors disabled:opacity-50"
      style={{ background: 'color-mix(in srgb, var(--status-failed) 12%, transparent)', color: 'var(--status-failed)', borderColor: 'color-mix(in srgb, var(--status-failed) 35%, transparent)' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.background = 'color-mix(in srgb, var(--status-failed) 20%, transparent)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'color-mix(in srgb, var(--status-failed) 12%, transparent)')}
    >
      {children}
    </button>
  )
}

function GhostSelect<T extends string | number>({
  value, onChange, options,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
}) {
  return (
    <select
      value={String(value)}
      onChange={(e) => {
        const next = options.find(o => String(o.value) === e.target.value)?.value
        if (next != null) onChange(next as T)
      }}
      className="text-[13px] px-2.5 py-1.5 rounded-md cursor-pointer"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
      }}
    >
      {options.map(o => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
    </select>
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
  model, verdict, summary, lede, issues, ctas,
}: {
  model: PipelineModel
  verdict: Verdict
  summary: React.ReactNode
  lede: React.ReactNode
  issues: IssueRowSpec[]
  ctas: { primary?: IssueRowSpec['cta']; secondary: IssueRowSpec['cta'][] }
}) {
  const t = VERDICT_THEME[verdict]
  return (
    <section
      aria-label="Pipeline verdict"
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
          Pipeline signal
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
    bad:  { bg: 'color-mix(in srgb, var(--status-failed) 8%, transparent)',  bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',  icBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',  icFg: 'var(--status-failed)' },
    warn: { bg: 'color-mix(in srgb, var(--status-broken) 6%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 28%, transparent)', icBg: 'color-mix(in srgb, var(--status-broken) 16%, transparent)', icFg: 'var(--status-broken)' },
    info: { bg: 'color-mix(in srgb, var(--color-accent) 6%, transparent)', bd: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', icBg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', icFg: 'var(--color-accent)' },
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
          style={{ color: 'var(--color-accent)', borderColor: 'color-mix(in srgb, var(--color-accent) 25%, transparent)', background: 'color-mix(in srgb, var(--color-accent) 6%, transparent)' }}>
          {issue.cta.label} →
        </button>
      )}
    </div>
  )
}

function HealthMeter({ model, verdict }: { model: PipelineModel; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  const pillLabel = score >= 66 ? 'Stable' : score >= 33 ? 'At risk' : 'Blocked'
  return (
    <div>
      <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)] mb-1.5" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        Pipeline health
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
        aria-label={`Pipeline health ${score} of 100, ${pillLabel}`}
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
            boxShadow: `0 0 0 2px ${t.meter === 'var(--status-failed)' ? 'color-mix(in srgb, var(--status-failed) 25%, transparent)' : 'rgba(0,0,0,0.4)'}`,
            border: `2px solid ${t.meter}`,
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-faint)] uppercase mt-1.5" style={{ letterSpacing: 'var(--tracking-wide)' }}>
        <span>Blocked · 0</span>
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

// ── Workflow ribbon ───────────────────────────────────────────────────────
type StageDisplay = 'done' | 'skipped'

interface RibbonStage {
  num: number
  name: string
  status: StageDisplay
  meta: React.ReactNode
}

function buildRibbon(model: PipelineModel): RibbonStage[] {
  const totalEvidence = model.totalRuns
  return [
    {
      num: 1, name: 'Run ingestion', status: 'done',
      meta: <>{model.totalRuns} build{model.totalRuns === 1 ? '' : 's'} · {totalEvidence} evidence</>,
    },
    {
      num: 2, name: 'Failure detection', status: 'done',
      meta: <>{model.failedRuns} failure{model.failedRuns === 1 ? '' : 's'} · {model.failedRuns} evidence</>,
    },
    {
      num: 3, name: 'Intelligence handoff', status: 'done',
      meta: model.primaryCluster
        ? <>1 cluster routed · {model.primaryCluster.members.length} evidence</>
        : <>0 clusters routed · 0 evidence</>,
    },
    {
      // README §4.4: Release context skipped when no run carries a release tag.
      num: 4, name: 'Release context',
      status: model.totalRuns > 0 && model.metadataCoveragePct < 50 ? 'skipped' : 'done',
      meta: model.metadataCoveragePct < 50
        ? <span className="italic">no release tag on any build</span>
        : <>{Math.round(model.metadataCoveragePct)}% coverage</>,
    },
  ]
}

function WorkflowRibbon({ stages }: { stages: RibbonStage[] }) {
  const completed = stages.filter(s => s.status === 'done').length
  const evidence = stages.filter(s => s.status === 'done').length * 7
  return (
    <section
      aria-label="Run workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <div>
          <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)] inline-flex items-center gap-1.5">
            Run workflow
            <span className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>compact</span>
          </h3>
          <p className="text-[11.5px] text-[var(--color-text-muted)] m-0 mt-0.5">
            Ingest builds, detect failures, hand off intelligence, keep release context visible.
          </p>
        </div>
        <span className="text-[11.5px] text-[var(--color-text-muted)]">
          {completed} of {stages.length} stages · {evidence} evidence items
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
    ? { bg: 'var(--status-passed-soft)', fg: 'var(--status-passed)', icon: <Check className="h-2.5 w-2.5" strokeWidth={3} /> }
    : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: <ChevronRight className="h-2.5 w-2.5" strokeWidth={3} /> }
  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Stage ${stage.num}: ${stage.name}, ${stage.status}`}
      onClick={() => toast('Workflow stage drawer — coming in Phase 2', { icon: '🪟' })}
      className={clsx(
        'relative flex items-start gap-2.5 transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]',
        stage.status === 'skipped' && 'opacity-80',
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
    </button>
  )
}

// ── KPI strip + sparkline primitives ─────────────────────────────────────
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

// 7 red bars, last one taller — for "Builds failed".
function SparklineRedBars({ count, brighterLast = true }: { count: number; brighterLast?: boolean }) {
  const n = Math.max(1, Math.min(7, count))
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-6">
      <line x1="0" y1="22" x2="100" y2="22" stroke="var(--color-border)" strokeWidth={1} />
      {Array.from({ length: n }).map((_, i) => {
        const x = 3 + i * (97 / n)
        const w = Math.min(11, 97 / n - 2)
        const tall = brighterLast && i === n - 1
        return <rect key={i} x={x} y={tall ? 2 : 6} width={w} height={tall ? 20 : 16} rx={1} fill={tall ? 'var(--status-failed)' : 'var(--status-failed)'} />
      })}
    </svg>
  )
}

// Stacked bar — primary (red) wide, secondary (amber) narrow — for "Unique failures".
function SparklineStackedShare({ primaryPct }: { primaryPct: number }) {
  const p = Math.max(0, Math.min(100, primaryPct))
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-6">
      <rect x="2" y="4" width={Math.max(2, p - 2)} height="16" rx="2" fill="color-mix(in srgb, var(--status-failed) 70%, transparent)" />
      <rect x={p} y="4" width={Math.max(2, 96 - p)} height="16" rx="2" fill="var(--status-broken)" />
    </svg>
  )
}

// Dashed target line + amber series — for "Avg pass rate" (misleading-headline framing).
function SparklineTargetWithDot({ valuePct }: { valuePct: number }) {
  const y = 24 - (Math.max(0, Math.min(100, valuePct)) / 100) * 18
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-6">
      <line x1="0" y1="3" x2="100" y2="3" stroke="color-mix(in srgb, var(--status-passed) 40%, transparent)" strokeDasharray="2 3" strokeWidth={1} />
      <line x1="0" y1={y} x2="100" y2={y} stroke="var(--status-broken)" strokeWidth={1.2} />
      <circle cx={92} cy={y} r={2} fill="var(--status-broken)" />
    </svg>
  )
}

// Two dots connected by a dashed line — for "Last green build".
function SparklineDottedPair({ leftHealthy = true, rightFailing = true }: { leftHealthy?: boolean; rightFailing?: boolean }) {
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-6">
      <line x1="8" y1="12" x2="92" y2="12" stroke="var(--color-border)" strokeDasharray="2 3" strokeWidth={1} />
      <circle cx={8}  cy={12} r={3} fill={leftHealthy ? 'var(--status-passed)' : 'var(--color-text-faint)'} />
      <circle cx={92} cy={12} r={3} fill={rightFailing ? 'color-mix(in srgb, var(--status-failed) 80%, transparent)' : 'var(--status-passed)'} />
    </svg>
  )
}

// 7 red bars — for "Red streak".
function SparklineRedStreak({ count }: { count: number }) {
  const n = Math.max(1, Math.min(7, count))
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" className="block w-full h-6">
      {Array.from({ length: n }).map((_, i) => {
        const x = 2 + i * (96 / n)
        const w = Math.min(12, 96 / n - 2)
        return <rect key={i} x={x} y={6} width={w} height={16} rx={1} fill="var(--status-failed)" />
      })}
    </svg>
  )
}

// ── Signature cluster card ────────────────────────────────────────────────
// Rows shown per page in the failure-signature table. The card sits beside the
// scorecard in a fixed-height row, so an unpaginated cluster stretched the page
// once a window contained more than a handful of matching builds.
export const SIGNATURE_ROWS_PER_PAGE = 8

export function SignatureClusterCard({
  primaryCluster, outlierClusters, totalRuns, onJumpToRow,
}: {
  primaryCluster: SignatureCluster | null
  outlierClusters: SignatureCluster[]
  totalRuns: number
  onJumpToRow: (run: TestRun) => void
}) {
  const [sigPage, setSigPage] = useState(1)

  // Cluster membership changes when the project or time window changes; without
  // a reset a viewer parked on page 3 would land on an empty table.
  //
  // Adjusted during render rather than in an effect: an effect would paint the
  // stale page first and then re-render, and this repo forbids synchronous
  // setState inside useEffect (react-hooks set-state-in-effect, an error here)
  // precisely because of those cascading renders.
  const clusterKey = primaryCluster
    ? `${primaryCluster.signature}:${primaryCluster.members.length}:${outlierClusters.length}`
    : ''
  const [prevClusterKey, setPrevClusterKey] = useState(clusterKey)
  if (prevClusterKey !== clusterKey) {
    setPrevClusterKey(clusterKey)
    setSigPage(1)
  }

  if (!primaryCluster) {
    return (
      <CardShell title="Failure signature" rightSlot={<span>0 clusters</span>}>
        <div className="px-4 py-6 text-center text-[13px] text-[var(--color-text-secondary)]">
          No failures in this window — nothing to cluster.
        </div>
      </CardShell>
    )
  }
  const sigLabel = shortSignatureLabel(primaryCluster)
  const matchCount = primaryCluster.members.length
  const outlierCount = outlierClusters.reduce((s, c) => s + c.members.length, 0)
  const allRows = [
    ...primaryCluster.members.map(m => ({ run: m, isOutlier: false })),
    ...outlierClusters.flatMap(c => c.members.map(m => ({ run: m, isOutlier: true }))),
  ]
  const sigPages = Math.max(1, Math.ceil(allRows.length / SIGNATURE_ROWS_PER_PAGE))
  // Clamp rather than trust state: a shrinking cluster can leave sigPage past
  // the end for the render that happens before the reset effect runs.
  const safePage = Math.min(sigPage, sigPages)
  const pageRows = allRows.slice(
    (safePage - 1) * SIGNATURE_ROWS_PER_PAGE,
    safePage * SIGNATURE_ROWS_PER_PAGE,
  )

  return (
    <CardShell
      title={
        <>
          Failure signature ·{' '}
          <code className="font-mono text-[12px]" style={{ color: 'var(--status-failed)' }}>{sigLabel}</code>
        </>
      }
      rightSlot={<span>single root cause likely</span>}
    >
      <div className="px-4 pt-1 pb-2">
        <p className="text-[12.5px] m-0 mt-0.5 mb-2.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
          {matchCount} of {totalRuns} build{totalRuns === 1 ? '' : 's'} match · same test failing across all of them.
          {' '}Treat them as one regression, not {matchCount + outlierCount} incidents.
        </p>
        <div className="flex flex-col gap-1.5">
          {pageRows.map(({ run, isOutlier }) => (
            <ClusterRow
              key={run.id}
              run={run}
              isOutlier={isOutlier}
              primarySignature={primaryCluster.signature}
              onClick={() => onJumpToRow(run)}
            />
          ))}
        </div>
        {outlierCount > 0 && (
          <p className="text-[11.5px] m-0 mt-3 italic" style={{ color: 'var(--color-text-muted)', lineHeight: 1.5 }}>
            The outlier ran a different test count, with the same single failure pattern. Likely an older Jenkinsfile that included a smoke-test suite later removed. Same failing test underneath — treat it as part of the cluster.
          </p>
        )}
      </div>
      {/* Renders nothing at one page, so small clusters look exactly as before. */}
      <Pagination
        page={safePage}
        pages={sigPages}
        total={allRows.length}
        onChange={setSigPage}
      />
    </CardShell>
  )
}

function ClusterRow({
  run, isOutlier, primarySignature, onClick,
}: {
  run: TestRun
  isOutlier: boolean
  primarySignature: string
  onClick: () => void
}) {
  const pct = Number(run.pass_rate ?? 0)
  const passFlex = run.passed_tests
  const failFlex = run.failed_tests
  const sig = clusterSignature(run)
  const sigDiffers = sig !== primarySignature
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${run.passed_tests} passed, ${run.failed_tests} failed, ${run.total_tests} total`}
      className={clsx(
        'grid items-center gap-3 rounded-md px-2.5 py-2 text-left transition-colors',
        'hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-1px] focus-visible:outline-[var(--color-accent)]',
      )}
      style={{
        gridTemplateColumns: '170px 1fr 60px 70px',
        background: isOutlier ? 'color-mix(in srgb, var(--status-broken) 5%, transparent)' : 'transparent',
      }}
    >
      <span className="text-[12px] tabular-nums truncate text-[var(--color-text-secondary)]">
        <strong className="text-[var(--color-text)]">#{String(run.build_number)}</strong>
        <span className="ml-1.5 font-mono text-[11px]">{run.id.slice(0, 8)}</span>
      </span>
      <div className="flex h-3.5 rounded-sm overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
        {passFlex > 0 && <span style={{ flex: passFlex, background: 'var(--status-passed)' }} />}
        {failFlex > 0 && <span style={{ flex: failFlex, background: 'var(--status-failed)' }} />}
      </div>
      <span
        className="text-[12.5px] font-semibold tabular-nums text-right"
        style={{ color: isOutlier ? 'var(--status-broken)' : pct >= 80 ? 'var(--status-passed)' : pct >= 50 ? 'var(--status-broken)' : 'var(--status-failed)' }}
      >
        {pct.toFixed(1)}%
      </span>
      <span
        className="text-[10.5px] uppercase font-semibold text-center px-1.5 py-0.5 rounded-full justify-self-end"
        style={{
          background: sigDiffers ? 'color-mix(in srgb, var(--status-broken) 16%, transparent)' : 'color-mix(in srgb, var(--status-failed) 15%, transparent)',
          color:      sigDiffers ? 'var(--status-broken)' : 'var(--status-failed)',
          letterSpacing: 'var(--tracking-wide)',
        }}
      >
        {sigDiffers ? 'outlier' : 'match'}
      </span>
    </button>
  )
}

// ── Runs table ────────────────────────────────────────────────────────────
function RunsTable({
  runs, primarySignature, selectedIds, setSelectedIds, onTrigger, onDeep,
  onCompareWithPrevious,
  isQaEngineer,
  page, pages, total, onPageChange,
  datetimeSortDir, onToggleDatetimeSort,
}: {
  runs: TestRun[]
  primarySignature: string | null
  selectedIds: Set<string>
  setSelectedIds: (s: Set<string>) => void
  onTrigger: (id: string) => void
  onDeep: (id: string) => void
  /** Fired when the user clicks the per-row "Compare to previous" icon.
   *  The parent uses the full ``runs`` array (not just this page slice)
   *  to find the previous-of-same-suite candidate, so the lookup pool
   *  isn't broken by client-side pagination. */
  onCompareWithPrevious: (run: TestRun) => void
  isQaEngineer: boolean
  /** Pagination props — parent computes pages from full ``runs.length``. */
  page: number
  pages: number
  total: number
  onPageChange: (p: number) => void
  datetimeSortDir: 'asc' | 'desc'
  onToggleDatetimeSort: () => void
}) {
  const allSelected = runs.length > 0 && runs.every(r => selectedIds.has(r.id))
  const someSelected = runs.some(r => selectedIds.has(r.id))
  const selectedCount = selectedIds.size

  const toggleAll = () => {
    if (allSelected) setSelectedIds(new Set())
    else             setSelectedIds(new Set(runs.map(r => r.id)))
  }
  const toggleOne = (id: string) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else              next.add(id)
    setSelectedIds(next)
  }

  return (
    <CardShell
      title={<>Runs <span className="text-[11.5px] text-[var(--color-text-muted)] font-normal ml-2"><strong>{total}</strong> in window · sorted by Started {datetimeSortDir === 'desc' ? '↓' : '↑'} · showing {runs.length} on this page</span></>}
      rightSlot={
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11.5px]">{selectedCount} selected</span>
          <GhostBtn
            disabled={selectedCount === 0 || !isQaEngineer}
            onClick={() => onTrigger('__bulk__')}
            title={isQaEngineer ? 'Re-trigger pipelines for selected runs' : 'QA Engineer role required'}
          >
            Bulk trigger
          </GhostBtn>
          <GhostBtn
            disabled={selectedCount === 0 || !isQaEngineer}
            onClick={() => onDeep('__bulk__')}
            title={isQaEngineer ? 'Run deep investigation on selected runs' : 'QA Engineer role required'}
          >
            Bulk deep
          </GhostBtn>
        </div>
      }
    >
      {/* Wide table (10 cols) — force natural width with min-w so horizontal
          scroll kicks in cleanly on narrow viewports instead of columns
          getting squeezed and clipped to the right of the visible area. */}
      <div className="overflow-x-auto">
        <table className="text-[12.5px]" style={{ minWidth: 1180, width: '100%' }}>
          <thead>
            <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
              <th style={{ width: 32, padding: '8px 12px' }}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={el => { if (el) el.indeterminate = !allSelected && someSelected }}
                  onChange={toggleAll}
                  aria-label="Select all visible runs"
                />
              </th>
              <ThSort label="Build" />
              <Th label="Test Suite" />
              <Th label="Signature" />
              <Th label="Status" />
              <Th label="Tests" />
              <ThSort label="Pass rate" />
              <ThSort
                label="Started"
                sortDir={datetimeSortDir === 'desc' ? '↓' : '↑'}
                active
                onClick={onToggleDatetimeSort}
              />
              <Th label="End" />
              <Th label="Actions" align="right" />
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={10} className="text-center py-10 text-[var(--color-text-muted)]">
                  No runs in the window. Try a longer window or check your reporter.
                </td>
              </tr>
            )}
            {runs.map((r, i) => {
              const sig = clusterSignature(r)
              const isOutlier = primarySignature !== null && sig !== primarySignature && r.failed_tests > 0
              const sigLabel = primarySignature
                ? (sig === primarySignature ? shortSignatureLabel({ signature: primarySignature, passed: 0, failed: 0, total: 0, members: [] }) : 'outlier · old config')
                : '—'
              const failed = isFailed(r.status)
              return (
                <tr
                  id={`run-row-${r.id}`}
                  key={r.id}
                  style={{
                    background: isOutlier ? 'color-mix(in srgb, var(--status-broken) 3%, transparent)' : (i % 2 === 0 ? 'var(--color-bg-card)' : 'transparent'),
                    borderBottom: '1px solid var(--color-border)',
                  }}
                  className="transition-colors hover:bg-[var(--color-bg-hover)]"
                >
                  <td style={{ padding: '8px 12px' }}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(r.id)}
                      onChange={() => toggleOne(r.id)}
                      aria-label={`Select #${r.build_number}`}
                    />
                  </td>
                  {/* Build column links to the run detail page. Header
                      label is "Build" for historical continuity, but the
                      VALUE is the human-readable, per-(project, suite)
                      incremental "Run #N" — server-side ROW_NUMBER()
                      over the partition. Falls back to the raw SDK
                      build_number for legacy rows that pre-date the
                      run_seq field. */}
                  <td className="font-mono text-[12.5px] font-semibold" style={{ padding: '8px 12px' }}>
                    <Link to={`/runs/${r.id}`} className="text-[var(--color-text)] hover:text-[var(--color-accent)] hover:underline">
                      {r.run_seq != null ? `Run #${r.run_seq}` : `#${String(r.build_number)}`}
                    </Link>
                    {r.ingestion_source === 'upload' && (
                      <span
                        className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-[9.5px] font-medium align-middle"
                        style={{
                          background: 'rgba(99,102,241,0.12)',
                          border: '1px solid rgba(99,102,241,0.30)',
                          color: '#a5b4fc',
                        }}
                        title="Results were manually uploaded from a report file"
                      >
                        Uploaded
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <SuiteBadge
                      primary={r.primary_suite_name}
                      all={r.suite_names}
                      linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
                    />
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <span
                      className={clsx(
                        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10.5px]',
                        isOutlier && 'italic',
                      )}
                      style={{
                        background: isOutlier ? 'color-mix(in srgb, var(--status-broken) 10%, transparent)' : (failed ? 'color-mix(in srgb, var(--status-failed) 10%, transparent)' : 'color-mix(in srgb, var(--status-passed) 10%, transparent)'),
                        border: `1px solid ${isOutlier ? 'color-mix(in srgb, var(--status-broken) 25%, transparent)' : (failed ? 'color-mix(in srgb, var(--status-failed) 25%, transparent)' : 'color-mix(in srgb, var(--status-passed) 25%, transparent)')}`,
                        color: isOutlier ? 'var(--status-broken)' : (failed ? 'var(--status-failed)' : 'var(--status-passed)'),
                      }}
                    >
                      <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
                      {sigLabel}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-semibold"
                      style={{
                        background: failed ? 'color-mix(in srgb, var(--status-failed) 15%, transparent)' : isPassed(r.status) ? 'color-mix(in srgb, var(--status-passed) 15%, transparent)' : 'var(--color-bg-secondary)',
                        color:      failed ? 'var(--status-failed)' : isPassed(r.status) ? 'var(--status-passed)' : 'var(--color-text-muted)',
                      }}
                    >
                      {(r.status || '—').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <div className="flex items-center gap-2">
                      <div className="flex h-3 rounded-sm overflow-hidden flex-1 max-w-[80px]" style={{ background: 'var(--color-bg-secondary)' }}>
                        {r.passed_tests > 0 && <span style={{ flex: r.passed_tests, background: 'var(--status-passed)' }} />}
                        {r.failed_tests > 0 && <span style={{ flex: r.failed_tests, background: 'var(--status-failed)' }} />}
                      </div>
                      <span className="text-[10.5px] tabular-nums text-[var(--color-text-muted)] whitespace-nowrap">
                        <span style={{ color: 'var(--status-passed)' }}>{r.passed_tests}</span> · <span style={{ color: 'var(--status-failed)' }}>{r.failed_tests}</span> · <span>{r.total_tests} total</span>
                      </span>
                    </div>
                  </td>
                  <td style={{ padding: '8px 12px' }} className="font-semibold tabular-nums">
                    <span style={{
                      color: Number(r.pass_rate) >= 80 ? 'var(--status-passed)'
                        : Number(r.pass_rate) >= 50 ? 'var(--status-broken)'
                        : 'var(--status-failed)',
                    }}>
                      {Number(r.pass_rate ?? 0).toFixed(1)}%
                    </span>
                  </td>
                  <td
                    className="text-[var(--color-text-muted)] whitespace-nowrap tabular-nums"
                    style={{ padding: '8px 12px' }}
                    title={relativeTime(r.start_time ?? r.created_at)}
                  >
                    {new Date(r.start_time ?? r.created_at).toLocaleString()}
                  </td>
                  <td
                    className="text-[var(--color-text-muted)] whitespace-nowrap tabular-nums"
                    style={{ padding: '8px 12px' }}
                    title={r.end_time ? relativeTime(r.end_time) : 'Run has not finished yet'}
                  >
                    {r.end_time ? new Date(r.end_time).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                    <div className="inline-flex items-center gap-1.5">
                      <Link
                        to={`/runs/${r.id}/intelligence`}
                        className="inline-flex items-center gap-1 text-[11.5px] px-2 py-0.5 rounded-md border"
                        style={{
                          color: 'var(--color-accent)',
                          borderColor: 'color-mix(in srgb, var(--color-accent) 30%, transparent)',
                          background: 'var(--color-accent-bg-soft)',
                        }}
                      >
                        <Sparkles className="h-3 w-3" /> Intel
                      </Link>
                      <button
                        type="button"
                        onClick={() => onTrigger(r.id)}
                        disabled={!isQaEngineer}
                        title={isQaEngineer ? 'Re-trigger standard pipeline' : 'QA Engineer role required'}
                        className="inline-flex items-center justify-center h-6 w-6 rounded-md border text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                        style={{ borderColor: 'var(--color-border)' }}
                      >
                        <Zap className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onDeep(r.id)}
                        disabled={!isQaEngineer}
                        title={isQaEngineer ? 'Trigger deep investigation' : 'QA Engineer role required'}
                        className="inline-flex items-center justify-center h-6 w-6 rounded-md border text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                        style={{ borderColor: 'var(--color-border)' }}
                      >
                        <Stethoscope className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onCompareWithPrevious(r)}
                        disabled={!r.primary_suite_name}
                        title={
                          r.primary_suite_name
                            ? `Compare to the previous run of "${r.primary_suite_name}"`
                            : 'No suite attribution on this run — cannot pick a previous-of-same-suite'
                        }
                        className="inline-flex items-center justify-center h-6 w-6 rounded-md border text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
                        style={{ borderColor: 'var(--color-border)' }}
                      >
                        <GitCompare className="h-3 w-3" />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pages={pages} total={total} onChange={onPageChange} />
    </CardShell>
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
  label, sortDir, active, onClick,
}: { label: string; sortDir?: '↑' | '↓'; active?: boolean; onClick?: () => void }) {
  return (
    <th
      aria-sort={active ? (sortDir === '↑' ? 'ascending' : 'descending') : undefined}
      onClick={onClick}
      style={{
        padding: '8px 12px',
        textAlign: 'left',
        color: active ? 'var(--color-text)' : 'var(--color-text-muted)',
        fontWeight: 500,
        fontSize: 10.5,
        textTransform: 'uppercase',
        letterSpacing: 'var(--tracking-wider)',
        cursor: onClick ? 'pointer' : 'default',
        userSelect: 'none',
      }}
    >
      {label} <span aria-hidden className="ml-1">{sortDir ?? '↕'}</span>
    </th>
  )
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return '—'
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

// ── Last green callout ─────────────────────────────────────────────────────
function LastGreenCallout({ model, onBisect }: { model: PipelineModel; onBisect: () => void }) {
  if (!model.lastGreen || model.failedRuns === 0) {
    return null
  }
  const sha = model.lastGreen.id.slice(0, 7)
  const hours = model.hoursSinceLastGreen ?? 0
  return (
    <section
      aria-labelledby="lastgreen"
      className="rounded-xl"
      style={{
        padding: '14px 16px',
        background: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 6%, transparent), transparent 55%), var(--color-bg-card)',
        border: '1px solid color-mix(in srgb, var(--status-passed) 28%, transparent)',
        borderLeft: '3px solid var(--status-passed)',
      }}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <h3 id="lastgreen" className="text-[13px] font-semibold m-0 text-[var(--color-text)]">Last green build</h3>
        <span
          className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase"
          style={{ background: 'color-mix(in srgb, var(--status-passed) 15%, transparent)', color: 'var(--status-passed)', letterSpacing: 'var(--tracking-wide)' }}
        >
          Bisect target
        </span>
      </div>
      <p className="text-[12.5px] m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        The last green Jenkins run is <code className="font-mono text-[11.5px]">#{model.lastGreen.build_number}</code>{' '}
        at SHA <code className="font-mono text-[11.5px]">{sha}</code>, {hours} hour{hours === 1 ? '' : 's'} ago.
        {' '}{model.failedRuns} build{model.failedRuns === 1 ? '' : 's'} since{' '}
        {model.failedRuns === 1 ? 'has failed' : 'have all failed'}. Bisect or revert from that SHA.
      </p>
      <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <CalloutStat label="Last green" value={<span className="font-mono">{sha}</span>} tone="good" />
        <CalloutStat label="Hours ago" value={hours} tone="neutral" />
      </div>
      <div className="flex flex-wrap gap-2 mt-3">
        <PrimaryBtn onClick={onBisect}>
          <GitBranch className="h-3.5 w-3.5" /> Start bisect
        </PrimaryBtn>
        <GhostBtn onClick={() => toast('Diff against HEAD — coming in Phase 2', { icon: '⇆' })}>
          Diff against HEAD
        </GhostBtn>
      </div>
    </section>
  )
}

function CalloutStat({ label, value, tone }: { label: string; value: React.ReactNode; tone: 'good' | 'bad' | 'neutral' }) {
  const fg = tone === 'good' ? 'var(--status-passed)' : tone === 'bad' ? 'var(--status-failed)' : 'var(--color-text)'
  return (
    <div
      className="rounded-md border px-3 py-2.5"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="text-[16px] font-bold tabular-nums mt-0.5" style={{ color: fg }}>{value}</div>
    </div>
  )
}

// ── Build velocity card ──────────────────────────────────────────────────
function BuildVelocityCard({ cells, redStreak }: { cells: VelocityCell[]; redStreak: number }) {
  return (
    <CardShell title="Build velocity · 14d" rightSlot={<span>{redStreak} reds in a row</span>}>
      <div className="px-4 pt-1 pb-3.5">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-2.5" style={{ lineHeight: 1.5 }}>
          Each cell is one build, oldest left.
        </p>
        <div
          role="img"
          aria-label={`Build velocity over the last 14 builds: ${cells.filter(c => c.kind === 'pass').length} passed, ${cells.filter(c => c.kind === 'fail').length} failed, ${cells.filter(c => c.kind === 'empty').length} no-build cells.`}
          className="grid gap-1.5 mb-2.5"
          style={{ gridTemplateColumns: 'repeat(14, 1fr)' }}
        >
          {cells.map((c, i) => (
            <div
              key={i}
              title={c.buildLabel ? `${c.buildLabel} · ${c.kind}` : 'no build'}
              className="rounded-sm"
              style={{
                aspectRatio: '1',
                background: c.kind === 'pass' ? 'var(--status-passed)' : c.kind === 'fail' ? 'var(--gate-no-go)' : 'var(--color-bg-secondary)',
                border: c.kind === 'empty' ? '1px solid var(--color-border)' : '1px solid transparent',
                boxShadow: c.isLast && c.kind === 'fail' ? '0 0 0 1px color-mix(in srgb, var(--status-failed) 45%, transparent)' : 'none',
              }}
            />
          ))}
        </div>
        <div className="flex justify-between text-[10px] text-[var(--color-text-muted)]">
          <span>14 builds ago</span>
          <span className="inline-flex items-center gap-2">
            <span className="inline-flex items-center gap-1">
              <i aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--status-passed)' }} />Pass
            </span>
            <span className="inline-flex items-center gap-1">
              <i aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--gate-no-go)' }} />Fail
            </span>
            <span className="inline-flex items-center gap-1">
              <i aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }} />No build
            </span>
          </span>
          <span>Now</span>
        </div>
      </div>
    </CardShell>
  )
}

// ── Recommended actions ──────────────────────────────────────────────────
interface RecRow {
  role: 'dev' | 'qa' | 'rm'
  Icon: typeof CodeIcon
  label: string
  who?: string
  body: React.ReactNode
  cta: { label: string; onClick: () => void; dim?: boolean }
}

function buildRecActions(model: PipelineModel, onBisect: () => void): RecRow[] {
  const recs: RecRow[] = []
  const lastGreenSha = model.lastGreen ? model.lastGreen.id.slice(0, 7) : null
  if (model.primaryCluster && lastGreenSha) {
    recs.push({
      role: 'dev',
      Icon: CodeIcon,
      label: 'Developer',
      who: '@team-checkout',
      body: (
        <>
          Bisect from <code>{lastGreenSha}</code> → HEAD.
          {' '}The failing test signature is <code>{shortSignatureLabel(model.primaryCluster)}</code>.
        </>
      ),
      cta: { label: 'Start', onClick: onBisect },
    })
  }
  if (model.primaryCluster && model.primaryCluster.members.length >= 2) {
    recs.push({
      role: 'qa',
      Icon: ShieldCheck,
      label: 'QA',
      who: '@release-qa',
      body: <>Confirm the cluster is one root cause, not {model.primaryCluster.members.length} incidents. Don't open new bugs per build.</>,
      cta: { label: 'Triage cluster', onClick: () => toast('Cluster triage — coming in Phase 2', { icon: '🔍' }) },
    })
  }
  if (model.failedRuns > 0) {
    recs.push({
      role: 'rm',
      Icon: Wrench,
      label: 'Release manager',
      who: '@releng',
      body: <>Hold deploys touching the affected suite until a green build appears. Auto-deploy is currently armed.</>,
      cta: { label: 'Hold', onClick: () => toast('Hold-deploys editor — coming in Phase 2', { icon: '🚦' }) },
    })
  }
  return recs
}

function RecommendedActions({ recs }: { recs: RecRow[] }) {
  return (
    <CardShell title="Recommended actions" rightSlot={<span>routed by signature</span>}>
      <div className="px-4 py-3.5 flex flex-col gap-2">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-1">Generated from the failure signature.</p>
        {recs.length === 0 ? (
          <p className="text-[12.5px] text-[var(--color-text-muted)] py-2 text-center m-0">
            No recommendations — pipeline is healthy.
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
    dev: { bg: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', fg: 'var(--status-flaky)' },
    qa:  { bg: 'color-mix(in srgb, var(--color-accent) 16%, transparent)', fg: 'var(--color-accent)' },
    rm:  { bg: 'color-mix(in srgb, var(--status-passed) 16%, transparent)',  fg: 'var(--status-passed)' },
  }[rec.role]
  const Icon = rec.Icon
  return (
    <div
      className={clsx('grid items-center gap-2.5 rounded-md border', rec.cta.dim && 'opacity-60')}
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
function ProvenanceFooter(
  { totalEvidence, signature, refreshedAt }:
  { totalEvidence: number; signature: string | null; refreshedAt: string },
) {
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>Provenance</span>
        <span aria-hidden>·</span>
        <span>runs ingestion v1</span>
        <span aria-hidden>·</span>
        <span>{totalEvidence} evidence items · 3 tools</span>
        <span aria-hidden>·</span>
        <span>refreshed {refreshedAt}</span>
        {signature && (
          <>
            <span aria-hidden>·</span>
            <span>cluster <code className="font-mono text-[11.5px]">{signature}</code></span>
          </>
        )}
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
export default function RunsPage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { isQaEngineer } = usePermissions()
  // Rollout gate (MRU-17): the upload UI is hidden until an admin enables the
  // `manual_upload` feature flag.
  const uploadEnabled = useFeatureEnabled('manual_upload')

  // Manual report upload (PRD MRU-4). Opens from the header button or via the
  // sidebar deep-link ``/runs?upload=1``. Disabled in All-Projects mode — an
  // upload must target one concrete project.
  const [searchParams, setSearchParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(false)
  // Auto-open the upload drawer when the deep-link condition (``?upload=1`` on a
  // concrete project with the flag enabled) first becomes true. Tracked during
  // render via the previous-value pattern instead of a setState-in-effect so a
  // transition false→true opens once, and re-navigating after a close re-opens.
  const uploadRequested = searchParams.get('upload') === '1' && uploadEnabled
  const shouldAutoOpenUpload = uploadRequested && isQaEngineer
  // Why the deep link could not honour the request. The reason has to be
  // *rendered*: it previously lived only in the disabled button's `title`, so
  // arriving from the sidebar's "Upload Report" entry showed an unchanged runs
  // list and no explanation — the reported bug. `?upload=1` stays in the URL,
  // so resolving the reason opens the panel via the transition below.
  const uploadBlockedReason = resolveUploadBlockedReason({
    uploadRequested,
    isQaEngineer,
  })
  const [prevShouldAutoOpenUpload, setPrevShouldAutoOpenUpload] = useState(false)
  if (shouldAutoOpenUpload !== prevShouldAutoOpenUpload) {
    setPrevShouldAutoOpenUpload(shouldAutoOpenUpload)
    if (shouldAutoOpenUpload) setUploadOpen(true)
  }
  const closeUpload = () => {
    setUploadOpen(false)
    if (searchParams.has('upload')) {
      const next = new URLSearchParams(searchParams)
      next.delete('upload')
      setSearchParams(next, { replace: true })
    }
  }

  // Global shared time-window preference — selection here propagates
  // to every other window-filtered page (and vice versa). Snapped to
  // RunsPage's allowed set, which includes ``6`` (instead of 7) and
  // ``0`` (= all time) so the shared value may differ from what other
  // pages display.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, WINDOWS) as Window
  const setDays = setStoredDays as (w: Window) => void

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('')
  const [selectedSuite, setSelectedSuite] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // Client-side table pagination. Analytics widgets (signature clustering,
  // build velocity, KPIs) continue to consume the full fetched window so
  // their derived metrics stay accurate; only the table view is sliced.
  const TABLE_PAGE_SIZE = 25
  // Analytics fetch size. Was 50 (capped) which made the Pipeline-signal
  // denominator frozen at "of 50" no matter the selected window — KPIs
  // and verdict reflected only the latest page slice. 500 covers
  // realistic windows (1y of daily builds is ~365) so the verdict,
  // cluster card, KPI strip and velocity grid summarise the actual
  // window the user picked. ``runs.length`` may still be < server total
  // when a project ingests >500 runs in-window; the disclosure footer
  // surfaces that.
  const ANALYTICS_FETCH_SIZE = 500
  const [tablePage, setTablePage] = useState(1)
  // Reset page + selection whenever the filters change so users aren't stuck
  // on an empty page after narrowing the window. Done during render via
  // previous-value tracking (the React-recommended way to reset state on a
  // dependency change) rather than a cascading setState-in-effect.
  const runsFilterKey = `${days}|${statusFilter}|${selectedSuite}`
  const [prevRunsFilterKey, setPrevRunsFilterKey] = useState(runsFilterKey)
  if (prevRunsFilterKey !== runsFilterKey) {
    setPrevRunsFilterKey(runsFilterKey)
    setTablePage(1)
    setSelectedIds(new Set())
  }

  const { options: suiteOptions } = useSuiteOptions(days || 0)

  const { data, isLoading } = useRuns({
    page: 1,
    size: ANALYTICS_FETCH_SIZE,
    days: days || undefined,
    ...(statusFilter && { status: statusFilter }),
    ...(selectedSuite && { suite_name: selectedSuite }),
  })
  // Provenance rows must report when this client actually received the
  // payload. Both of this page's rows previously rendered a hardcoded
  // "refreshed just now", so a tab left open overnight still claimed the
  // numbers above it were seconds old -- the #893 defect, in the element whose
  // entire job is trustworthiness.
  const fetchedAt = useDataFreshness(data)
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  const runs = useMemo<TestRun[]>(() => (data?.items ?? []) as TestRun[], [data?.items])
  // Client-side sort by run datetime (created_at). The backend already returns
  // desc order, so 'desc' here is a no-op until the user clicks. Sorting is
  // applied to the full fetched page set *before* table-pagination, so
  // toggling the direction reorders every row currently in scope.
  const [datetimeSortDir, setDatetimeSortDir] = useState<'asc' | 'desc'>('desc')
  const sortedRuns = useMemo<TestRun[]>(() => {
    const mul = datetimeSortDir === 'desc' ? -1 : 1
    return [...runs].sort((a, b) =>
      mul * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    )
  }, [runs, datetimeSortDir])
  const tableTotalPages = Math.max(1, Math.ceil(sortedRuns.length / TABLE_PAGE_SIZE))
  const tableRuns = useMemo<TestRun[]>(() => {
    const start = (tablePage - 1) * TABLE_PAGE_SIZE
    return sortedRuns.slice(start, start + TABLE_PAGE_SIZE)
  }, [sortedRuns, tablePage])
  const model = useMemo(() => buildPipelineModel(runs), [runs])
  const verdict = pickVerdict(model)
  const ribbonStages = useMemo(() => buildRibbon(model), [model])
  const sigLabel = model.primaryCluster ? shortSignatureLabel(model.primaryCluster) : null

  // Bisect navigation — shared by every "Bisect" / "Start bisect" /
  // "Bisect from green" / "Bisect from last green" button on the page.
  // When either side is missing (no green run in window, or no failing
  // run to compare against), toast a clear reason instead of nav'ing
  // to a half-populated compare page.
  const handleBisect = (): void => {
    const href = buildBisectHref(model)
    if (!href) {
      if (!model.lastGreen) {
        toast('No green run found in this window — widen the window to enable bisect.', { icon: '⚠️' })
      } else {
        toast('No failing run to bisect — everything is green.', { icon: '✅' })
      }
      return
    }
    navigate(href)
  }

  // Per-row "Compare to previous run" handler — picks the chronologically
  // immediately preceding run with the same ``primary_suite_name`` from
  // the full fetched ``runs`` list (not the paginated ``tableRuns`` slice,
  // so the previous-of-same-suite can live on a different page).
  const handleCompareWithPrevious = (run: TestRun): void => {
    if (!run.primary_suite_name) {
      toast('This run has no suite attribution — cannot pick a previous-of-same-suite.', { icon: '⚠️' })
      return
    }
    const previous = findPreviousRunOfSuite(run, runs)
    const href = buildCompareWithPreviousHref(run, previous)
    if (!href) {
      toast(
        `No earlier run of "${run.primary_suite_name}" in this window — widen the window or pick a different run.`,
        { icon: '⚠️' },
      )
      return
    }
    navigate(href)
  }

  // Not memoised — buildRecActions captures the latest ``handleBisect``
  // closure (which depends on render-time state via ``model``). The
  // function returns a small fixed array per render; memoisation here
  // would require tracking handleBisect identity, which is more code
  // than the equality saves.
  const recs = buildRecActions(model, handleBisect)

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Search className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view test runs."
      />
    )
  }

  if (isLoading && runs.length === 0) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }

  const projectLabel = project?.name ?? 'All Projects'

  // Per-row + bulk action handlers ────────────────────────────────────────
  async function handleTrigger(id: string) {
    if (id === '__bulk__') {
      try {
        const ids = Array.from(selectedIds)
        const result = await agentService.bulkTriggerPipelines(ids)
        toast.success(`Bulk-triggered ${result?.queued ?? ids.length} pipelines`)
        setSelectedIds(new Set())
      } catch {
        toast.error('Bulk trigger failed')
      }
    } else {
      try {
        await agentService.triggerPipeline(id)
        toast.success('Pipeline queued')
      } catch {
        toast.error('Trigger failed')
      }
    }
  }

  async function handleDeep(id: string) {
    if (id === '__bulk__') {
      try {
        const ids = Array.from(selectedIds)
        await Promise.all(ids.map(rid => agentService.triggerDeepPipeline(rid)))
        toast.success(`Bulk-queued deep investigation for ${ids.length} run${ids.length === 1 ? '' : 's'}`)
        setSelectedIds(new Set())
      } catch {
        toast.error('Bulk deep failed')
      }
    } else {
      try {
        await agentService.triggerDeepPipeline(id)
        toast.success('Deep investigation queued')
      } catch {
        toast.error('Deep trigger failed')
      }
    }
  }

  async function handleTriggerAllFailed() {
    const failedIds = runs.filter(r => isFailed(r.status)).map(r => r.id)
    if (failedIds.length === 0) {
      toast('No failed runs in window', { icon: 'ℹ️' })
      return
    }
    try {
      const result = await agentService.bulkTriggerPipelines(failedIds)
      toast.success(`Triggered ${result?.queued ?? failedIds.length} failed runs`)
    } catch {
      toast.error('Trigger-all failed')
    }
  }

  async function handleDeepAllFailed() {
    const failedIds = runs.filter(r => isFailed(r.status)).map(r => r.id)
    if (failedIds.length === 0) {
      toast('No failed runs in window', { icon: 'ℹ️' })
      return
    }
    try {
      await Promise.all(failedIds.map(id => agentService.triggerDeepPipeline(id)))
      toast.success(`Deep investigation queued for ${failedIds.length} runs`)
    } catch {
      toast.error('Deep-all failed')
    }
  }

  // Verdict copy ──────────────────────────────────────────────────────────
  const summaryNode: React.ReactNode = (() => {
    if (verdict === 'PENDING') return <>no builds in window</>
    if (verdict === 'HEALTHY') return <>{model.totalRuns} builds passing</>
    if (verdict === 'BROKEN') {
      const sharesSig = model.primaryCluster && model.primaryCluster.members.length / model.failedRuns >= 0.8
      return sharesSig
        ? <>{model.failedRuns} of {model.totalRuns} builds failed with the same signature</>
        : <>{model.failedRuns} of {model.totalRuns} builds failed</>
    }
    return <>{model.failedRuns} of {model.totalRuns} builds failed</>
  })()

  const lede: React.ReactNode = (() => {
    if (verdict === 'PENDING')
      return <>No builds in this window. Try a longer window or check that the reporter is firing.</>
    if (verdict === 'HEALTHY')
      return <>Every build in the last {days || '∞'} days passed. No regressions, no clusters — pipeline is green.</>
    if (verdict === 'BROKEN' && model.primaryCluster) {
      const matchN = model.primaryCluster.members.length
      return (
        <>
          {model.failedRuns === model.totalRuns ? 'Every build in this window' : `${model.failedRuns} of ${model.totalRuns} builds`}
          {' '}hit FAILED status, and {matchN} of {model.failedRuns} share an identical{' '}
          <strong style={{ color: 'var(--color-text)' }}>{model.primaryCluster.passed}-pass / {model.primaryCluster.failed}-fail / {model.primaryCluster.total}-total</strong> pattern —
          {' '}the same regression, re-triggered {matchN - 1 === 1 ? 'once' : `${matchN - 1} times`}.
          {' '}Stop re-running. Bisect from the last green build.
        </>
      )
    }
    return <>Some builds failed in the window. Check the cluster card and the runs table for the breakdown.</>
  })()

  const issues: IssueRowSpec[] = []
  if (model.failedRuns > 0 && model.passedRuns === 0) {
    issues.push({
      tone: 'bad',
      Icon: AlertCircle,
      body: (
        <>
          <strong>0 shippable builds.</strong> The last green Jenkins run produced
          {' '}{model.lastGreen ? <code>#{model.lastGreen.build_number}</code> : 'a predecessor'}
          {' '}{model.hoursSinceLastGreen != null ? `${model.hoursSinceLastGreen}h ago` : 'before this window'} — every build since has failed.
        </>
      ),
      cta: { label: 'Bisect from green', onClick: handleBisect },
    })
  }
  if (model.primaryCluster && model.primaryCluster.members.length >= 2) {
    issues.push({
      tone: 'warn',
      Icon: TrendingUp,
      body: (
        <>
          <strong>{model.primaryCluster.members.length} builds share signature <code>{sigLabel}</code></strong> — same {model.primaryCluster.passed}-pass/{model.primaryCluster.failed}-fail pattern.
          {' '}<span className="text-[var(--color-text-muted)]">Run intelligence once, triage at the cluster level instead of {model.primaryCluster.members.length} times.</span>
        </>
      ),
      cta: { label: 'Open cluster', onClick: () => toast('Cluster detail — coming in Phase 2', { icon: '🧬' }) },
    })
  }
  if (model.hasMissingMetadata) {
    issues.push({
      tone: 'info',
      Icon: AlertTriangle,
      body: (
        <>
          Branch, release, and duration absent on {Math.round(100 - model.metadataCoveragePct)}% of rows — the Jenkins reporter isn't sending these fields.
          {' '}<span className="text-[var(--color-text-muted)]">Cannot tell which feature branch broke things.</span>
        </>
      ),
      cta: { label: 'Fix reporter', onClick: () => toast('Reporter docs — coming in Phase 2', { icon: '📖' }) },
    })
  }

  const verdictCtas = {
    primary: model.lastGreen
      ? { label: 'Bisect from last green', onClick: handleBisect } as IssueRowSpec['cta']
      : { label: 'Refresh', onClick: () => window.location.reload() } as IssueRowSpec['cta'],
    secondary: [
      runs[0]
        ? { label: `Open intelligence · #${runs[0].build_number}`, onClick: () => navigate(`/runs/${runs[0].id}/intelligence`) } as IssueRowSpec['cta']
        : null,
      { label: 'Hold deploys', onClick: () => toast('Hold-deploys editor — coming in Phase 2', { icon: '🚦' }) } as IssueRowSpec['cta'],
    ].filter((c): c is IssueRowSpec['cta'] => c !== null),
  }

  // KPI sparkline data ────────────────────────────────────────────────────
  const buildsFailedFraction = model.totalRuns > 0 ? (model.failedRuns / model.totalRuns) * 100 : 0
  const primaryShare = model.primaryCluster && model.failedRuns > 0
    ? (model.primaryCluster.members.length / model.failedRuns) * 100
    : 0
  const lastGreenLabel = model.hoursSinceLastGreen != null ? `${model.hoursSinceLastGreen}h` : '—'

  const onJumpToRow = (run: TestRun) => {
    const el = document.getElementById(`run-row-${run.id}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.style.background = 'color-mix(in srgb, var(--status-broken) 10%, transparent)'
      window.setTimeout(() => { el.style.background = '' }, 1200)
    }
  }

  return (
    <PageShell>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Test Runs
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Jenkins builds for</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            {selectedSuite && (
              <>
                <span aria-hidden>·</span>
                <span>Suite</span>
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{selectedSuite}</code>
              </>
            )}
            <span aria-hidden>·</span>
            <span>{model.totalRuns} build{model.totalRuns === 1 ? '' : 's'} in window</span>
            <span aria-hidden>·</span>
            <span>refreshed {refreshedAt}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {uploadEnabled && (
            <GhostBtn
              onClick={() => setUploadOpen(true)}
              disabled={!isQaEngineer}
              title={
                !isQaEngineer
                  ? 'QA Engineer role required'
                  : 'Upload a JUnit / TestNG / Allure / Playwright / Cypress report file'
              }
            >
              <Upload className="h-3.5 w-3.5" />
              Upload report
            </GhostBtn>
          )}
          <DangerBtn
            onClick={handleTriggerAllFailed}
            disabled={!isQaEngineer || model.failedRuns === 0}
            title={isQaEngineer ? 'Re-fire the standard pipeline on every failed run in this window' : 'QA Engineer role required'}
          >
            <Zap className="h-3.5 w-3.5" />
            Trigger all failed
            <span className="ml-1 px-1.5 py-px rounded font-mono text-[10.5px]" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
              {model.failedRuns}
            </span>
          </DangerBtn>
          <GhostBtn
            onClick={handleDeepAllFailed}
            disabled={!isQaEngineer || model.failedRuns === 0}
            title="Run deep investigation on all failed runs"
          >
            <Stethoscope className="h-3.5 w-3.5" />
            Deep all failed
            <span className="ml-1 px-1.5 py-px rounded font-mono text-[10.5px]" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
              {model.failedRuns}
            </span>
          </GhostBtn>
          <GhostSelect
            value={days}
            onChange={(v) => setDays(v)}
            options={WINDOWS.map(w => ({ value: w, label: WINDOW_LABELS[w] }))}
          />
          <GhostSelect
            value={statusFilter}
            onChange={(v) => setStatusFilter(v as StatusFilter)}
            options={STATUS_FILTERS.map(s => ({ value: s, label: STATUS_LABELS[s] }))}
          />
          <SuiteFilterSelect
            value={selectedSuite}
            onChange={setSelectedSuite}
            options={suiteOptions}
            allLabel="All suites"
          />
        </div>
      </header>

      {uploadBlockedReason === 'role' && (
        <div
          className="flex items-start gap-2 rounded px-3 py-2.5 text-sm"
          style={{
            background: 'color-mix(in srgb, var(--color-accent) 10%, transparent)',
            border: '1px solid color-mix(in srgb, var(--color-accent) 35%, transparent)',
          }}
        >
          <Upload className="h-4 w-4 mt-0.5 shrink-0" style={{ color: 'var(--color-accent)' }} />
          <span className="text-[var(--color-text)]">
            <span className="font-medium">QA Engineer role required to upload reports.</span>{' '}
            <span className="text-[var(--color-text-muted)]">
              Ask an admin to grant the role, or ingest results through the CLI or SDK with an
              API key that has it.
            </span>
          </span>
        </div>
      )}

      <VerdictCard
        model={model}
        verdict={verdict}
        summary={summaryNode}
        lede={lede}
        issues={issues}
        ctas={verdictCtas}
      />

      <WorkflowRibbon stages={ribbonStages} />

      {/* KPI strip */}
      <section aria-label="Run KPIs" className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 mb-3.5">
        <KpiCell
          Icon={XCircle}
          label="Builds failed"
          value={model.failedRuns}
          sub={`/ ${model.totalRuns}`}
          tone={model.failedRuns > 0 ? 'bad' : 'good'}
          meta={<>{Math.round(buildsFailedFraction)}% of window</>}
          spark={<SparklineRedBars count={Math.min(7, model.failedRuns)} />}
          isFirst
        />
        <KpiCell
          Icon={Layers}
          label="Unique failures"
          value={model.uniqueClustersCount}
          sub={model.uniqueClustersCount === 1 ? 'signature' : 'signatures'}
          tone={model.uniqueClustersCount === 1 && model.failedRuns >= 2 ? 'bad' : model.uniqueClustersCount > 0 ? 'warn' : 'good'}
          meta={
            model.primaryCluster
              ? <>{model.primaryCluster.members.length} of {model.failedRuns} match · low diversity</>
              : <>nothing to cluster</>
          }
          spark={<SparklineStackedShare primaryPct={primaryShare} />}
        />
        <KpiCell
          Icon={BarChart3}
          label="Avg pass rate"
          value={`${model.avgPassRate.toFixed(1)}`}
          sub="%"
          tone={model.avgPassRate >= 80 ? 'good' : model.avgPassRate >= 50 ? 'warn' : 'bad'}
          meta={
            model.failedRuns === model.totalRuns && model.totalRuns > 0
              ? <>misleading — every build still failed</>
              : <>{model.passedRuns} pass · {model.failedRuns} fail</>
          }
          spark={<SparklineTargetWithDot valuePct={model.avgPassRate} />}
        />
        <KpiCell
          Icon={Clock}
          label="Last green build"
          value={lastGreenLabel}
          sub="ago"
          tone={
            model.hoursSinceLastGreen == null ? 'neutral'
            : model.hoursSinceLastGreen <= 24 ? 'good'
            : model.hoursSinceLastGreen <= 72 ? 'warn'
            : 'bad'
          }
          meta={
            model.lastGreen
              ? <>predecessor of <code className="font-mono text-[10px]">{model.lastGreen.id.slice(0, 8)}</code></>
              : <>no green build in window</>
          }
          spark={<SparklineDottedPair leftHealthy rightFailing={model.failedRuns > 0} />}
        />
        <KpiCell
          Icon={Sparkles}
          label="Red streak"
          value={model.redStreak}
          sub={model.redStreak === 1 ? 'in a row' : 'in a row'}
          tone={model.redStreak >= 3 ? 'bad' : model.redStreak > 0 ? 'warn' : 'good'}
          meta={model.redStreak > 0 ? <>longest streak in window</> : <>no streak — last build passed</>}
          spark={<SparklineRedStreak count={model.redStreak} />}
          isLast
        />
      </section>

      {/* Runs table is now full-width — matches the VerdictCard /
          WorkflowRibbon / KPI strip widths above it. Layout updated
          2026-05-15: the previous 1.65fr / 1fr grid cramped the table
          into ~60% of the screen and stacked Last Green / Velocity /
          Recommended Actions vertically in the right rail. Users
          asked for the table to breathe and the three context cards
          to sit parallel to the Failure signature analysis instead. */}
      <div className="mb-3.5">
        <RunsTable
          runs={tableRuns}
          primarySignature={model.primaryCluster?.signature ?? null}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onTrigger={handleTrigger}
          onDeep={handleDeep}
          onCompareWithPrevious={handleCompareWithPrevious}
          isQaEngineer={isQaEngineer}
          page={tablePage}
          pages={tableTotalPages}
          total={runs.length}
          onPageChange={setTablePage}
          datetimeSortDir={datetimeSortDir}
          onToggleDatetimeSort={() => {
            setDatetimeSortDir(d => d === 'desc' ? 'asc' : 'desc')
            setTablePage(1)
          }}
        />
      </div>

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          {model.primaryCluster && (
            <SignatureClusterCard
              primaryCluster={model.primaryCluster}
              outlierClusters={model.outlierClusters}
              totalRuns={model.totalRuns}
              onJumpToRow={onJumpToRow}
            />
          )}
        </div>
        <div className="flex flex-col gap-3.5 min-w-0">
          <LastGreenCallout model={model} onBisect={handleBisect} />
          <BuildVelocityCard cells={model.velocityCells} redStreak={model.redStreak} />
          <RecommendedActions recs={recs} />
        </div>
      </div>

      <ProvenanceFooter totalEvidence={model.totalRuns + model.failedRuns + (model.primaryCluster ? 7 : 0)} signature={sigLabel} refreshedAt={refreshedAt} />

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>

      {uploadOpen && uploadEnabled && isQaEngineer && (
        <UploadReportModal
          projectId={isAllProjects ? null : activeProjectId}
          onClose={closeUpload}
          onSuccess={(runId) => {
            closeUpload()
            navigate(`/runs/${runId}`)
          }}
        />
      )}
    </PageShell>
  )
}
