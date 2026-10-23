/**
 * Test Runs — the page template (UX redesign P3, `02-design-spec.md` §2, §5).
 *
 * Top to bottom at 1440 x 900:
 *   Header  → title, ONE primary action (Upload report, behind the
 *             `manual_upload` flag; `/runs?upload=1` still opens it: the
 *             upload form opens IN THE PAGE right under the header, W27a
 *             PAN-03), ONE secondary (Failure signatures → the section above
 *             the table), and ⋯ (Trigger all failed, Deep all failed).
 *   Toolbar → the global WindowPicker, the status filter, the suite filter,
 *             and (FAIL-05) a search box (build, branch, CI job; a commit
 *             prefix), a branch select (one project only) and an AI-verdict
 *             select. `?q=&branch=&verdict=` carry them, so a reload or a
 *             shared link restores the view (and `/intelligence?branch=main`
 *             lands filtered: the redirect forwards its query string).
 *   Banner  → the pipeline verdict in one line (`StatusBanner`): failing
 *             builds, last green, red streak, average pass rate, and Bisect
 *             from last green.
 *   Table   → the runs table, the page's primary content (`data-primary`):
 *             selectable, each run's AI verdict (P4, D2: the retired
 *             `/intelligence` list's job; the verdict opens the run's
 *             Analysis tab), per-row Trigger / Deep / Compare, bulk actions;
 *             a failed row's signature opens the signatures section.
 *   Below   → two collapsed Disclosures: "How this verdict is computed" (the
 *             composite pipeline-health gauge and its four weighted dimensions:
 *             Build success 40 % / Signature diversity 15 % / Fix velocity
 *             25 % / Metadata coverage 20 %; a dimension that is not measured
 *             reads "—" and the composite re-weights the others) and "Build
 *             history" (the 14-build velocity strip, the per-build pass rate,
 *             the last green build).
 *   Signatures → the failure signatures (FAIL-03), a read-only section in
 *             the page between the banner and the table (W27a PAN-03: it was
 *             an edge side panel; the table keeps its full card width): the
 *             window's failed builds grouped by the error most of their
 *             failing tests share (`runsList/runSignatures.ts`), each group
 *             titled by that error's first line, paginated.
 *
 * Data: every section derives from one useRuns(...) window, plus two batch
 * reads (`hooks/useRunListInsights`): ONE `GET /runs/ai-verdicts` for the
 * runs on screen (the stored verdicts, never computed by the list;
 * `runsList/aiVerdict.ts`), and ONE `GET /runs/failure-signatures` for up to
 * 50 failed builds (the visible ones first, then the newest). Bisect
 * navigates to /runs/compare; per-row Trigger / Deep + bulk-trigger use the
 * existing agentService calls. UX redesign P2: a control renders only when it
 * does something real.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  GitBranch, GitCompare, Layers, Search, Stethoscope, Upload, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import PageShell from '@/components/layout/PageShell'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import PageHeader from '@/components/ui/PageHeader'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import StatusBanner, { type BannerFact, type BannerState } from '@/components/ui/StatusBanner'
import { DECISION_LABEL } from '@/components/ui/statusVocabulary'
import Disclosure from '@/components/ui/Disclosure'
import MetricCard from '@/components/ui/MetricCard'
import { Panel } from '@/components/ui/PanelHeader'
import { Table, Td, Th, ThSort, Tr } from '@/components/ui/Table'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import WindowPicker, { DEFAULT_WINDOW_OPTIONS } from '@/components/ui/WindowPicker'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { TimingCell } from '@/components/ui/TimingCell'
import { helpTopicParam } from '@/components/help/helpTopics'
import UploadReportModal from '@/components/runs/UploadReportModal'
import AiVerdictCell from './runsList/AiVerdictCell'
import type { AiVerdict } from './runsList/aiVerdict'
import {
  buildSignatureModel, isFailedRun, signatureCellOf, signatureRunIds,
  type SignatureGroup, type SignatureModel, type SignatureRequestState,
} from './runsList/runSignatures'
import DayStrip from '@/components/charts/DayStrip'
import GaugeBar from '@/components/charts/GaugeBar'
import Sparkline from '@/components/charts/Sparkline'
import { buildSparklineModel } from '@/components/charts/Sparkline.model'
import { countTones, type DayStripCell } from '@/components/charts/dayStrip.model'
import type { GaugeTick, GaugeTone } from '@/components/charts/gaugeBar.model'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { useRuns } from '@/hooks/useRuns'
import { useRunAiVerdicts, useRunBranches, useRunSignatures } from '@/hooks/useRunListInsights'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePageSuiteFilter } from '@/hooks/usePageSuiteFilter'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import agentService from '@/services/agentService'
import type { RunListFilters, TestRun, TestRunListResponse } from '@/types/runs'
import { shortAgo } from '@/utils/formatters'
import { buildCompareWithPreviousHref } from '@/utils/runComparisons'
import { aiHistoryService } from '@/services/aiHistoryService'
import { isRunInProgress, measuredRunPassRate } from '@/utils/runPassRate'

/** The page's help topic (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/runs')

// ── Filters ────────────────────────────────────────────────────────────────
// The window is the toolbar's WindowPicker, bound to the global store
// (24h / 7d / 14d / 30d / 90d). "All time" (0) was a Runs-only option of the
// page's own select; the shared picker has no such window, and a stored 0
// snaps to 24h like on every other page.
const STATUS_FILTERS = ['', 'FAILED', 'PASSED', 'IN_PROGRESS'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]
const STATUS_LABELS: Record<StatusFilter, string> = {
  '':            'All statuses',
  FAILED:        'Failed',
  PASSED:        'Passed',
  IN_PROGRESS:   'In progress',
}

// FAIL-05: the AI-verdict filter (`GET /runs?recommendation=`). `none` = the
// runs whose row reads "—" (no current snapshot, or no recommendation).
const VERDICT_FILTERS = ['', 'GO', 'CONDITIONAL_GO', 'NO_GO', 'none'] as const
type VerdictFilter = (typeof VERDICT_FILTERS)[number]
const VERDICT_FILTER_LABELS: Record<VerdictFilter, string> = {
  '':             'Any verdict',
  GO:             DECISION_LABEL.go,
  CONDITIONAL_GO: DECISION_LABEL.conditional,
  NO_GO:          DECISION_LABEL.no_go,
  none:           'No verdict',
}
const isVerdictFilter = (value: string): value is VerdictFilter =>
  (VERDICT_FILTERS as readonly string[]).includes(value)

/** The search box waits this long after the last key before it asks. */
export const SEARCH_DEBOUNCE_MS = 300

/**
 * The toolbar's URL-synced filters (`?q=&branch=&verdict=`). An unknown
 * `verdict` reads as no filter (and is dropped from the URL by the page).
 */
function readRunListFilters(params: URLSearchParams): Required<RunListFilters> {
  const verdict = params.get('verdict') ?? ''
  return {
    q: (params.get('q') ?? '').trim(),
    branch: (params.get('branch') ?? '').trim(),
    verdict: isVerdictFilter(verdict) ? verdict : '',
  }
}

// ── Verdict ────────────────────────────────────────────────────────────────
type Verdict = 'BROKEN' | 'MIXED' | 'HEALTHY' | 'PENDING'

interface VerdictTheme {
  /** The StatusBanner state that carries this verdict above the table. */
  banner: BannerState
  pillBg: string
  pillBd: string
  pillFg: string
  meter: string
  label: string
}

const VERDICT_THEME: Record<Verdict, VerdictTheme> = {
  BROKEN: {
    banner: 'fail',
    pillBg: 'color-mix(in srgb, var(--status-failed) 16%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)',
    pillFg: 'var(--status-failed)',
    meter:  'var(--status-failed)',
    label:  'Pipeline broken',
  },
  MIXED: {
    banner: 'warn',
    pillBg: 'color-mix(in srgb, var(--status-broken) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
    pillFg: 'var(--status-broken)',
    meter:  'var(--status-broken)',
    label:  'Mixed',
  },
  HEALTHY: {
    banner: 'ok',
    pillBg: 'color-mix(in srgb, var(--status-passed) 12%, transparent)',
    pillBd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',
    pillFg: 'var(--status-passed)',
    meter:  'var(--status-passed)',
    label:  'Pipeline healthy',
  },
  PENDING: {
    banner: 'pending',
    pillBg: 'var(--color-bg-secondary)',
    pillBd: 'var(--color-border)',
    pillFg: 'var(--color-text-muted)',
    meter:  'var(--color-text-muted)',
    label:  'Pending',
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
  /** `null` = not measured: the tile reads "—" and the composite leaves it out. */
  score: number | null
  measured: boolean
  weight: number
  tone: 'good' | 'warn' | 'bad'
}

interface PipelineModel {
  composite: number
  dimensions: DimensionScore[]
  totalRuns: number
  failedRuns: number
  passedRuns: number
  inProgressRuns: number
  /** Mean pass rate over MEASURED builds; `null` when none has a result ("—", never 0 %). */
  avgPassRate: number | null
  /** Builds with a pass rate (the sparkline's points). */
  measuredRuns: number
  redStreak: number                  // consecutive failures from newest backwards
  lastGreen: TestRun | null
  /** Most recent failed run. Paired with ``lastGreen`` to populate the
   *  bisect modal (``/runs/compare?left=<green>&right=<failed>``). */
  latestFailedRun: TestRun | null
  hoursSinceLastGreen: number | null
  /** The failed builds grouped by their dominant error (FAIL-03). */
  signatures: SignatureModel
  hasMissingMetadata: boolean
  metadataCoveragePct: number
  velocityCells: DayStripCell[]      // 14 cells for the velocity strip, oldest first
  /** Per-build pass rate, OLDEST first; `null` for a build with no result yet. */
  passRateSeries: (number | null)[]
}

const isFailed = isFailedRun
function isPassed(s: string): boolean {
  return /^pass(ed)?$/i.test(s) || /success/i.test(s)
}
// One rule for "in flight" and "has a pass rate yet", shared with the
// Intelligence Hub (OD-18, `utils/runPassRate`).
const isInProgress = isRunInProgress

function buildVelocityCells(runs: TestRun[]): DayStripCell[] {
  // Take the most-recent 14 runs, oldest first. The newest build carries the
  // strip's "latest" marker: the cell a reader looks for first. With fewer
  // than 14 runs, the "no build" cells pad the OLDEST end, before the first
  // build: padded at the end they sat under "Now", newer than the newest
  // build, and End read "no build" (R2 F2).
  const recent = [...runs]
    .sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    .slice(0, 14)
    .reverse()
  const cells: DayStripCell[] = recent.map((r, i) => {
    const tone = isFailed(r.status) ? 'fail' : isPassed(r.status) ? 'pass' : 'none'
    return {
      key: r.id,
      label: `#${r.build_number} · ${tone === 'none' ? 'no result' : tone}`,
      tone,
      marker: i === recent.length - 1 ? 'today' : undefined,
    }
  })
  const padding: DayStripCell[] = Array.from({ length: 14 - cells.length }, (_, i) => ({
    key: `empty ${i}`,
    label: 'no build',
    tone: 'none',
  }))
  return [...padding, ...cells]
}

/**
 * The "Avg pass rate" sparkline's series: one point per build, OLDEST first
 * (the API lists runs newest first, and a sparkline reads left to right). A
 * build still running, or one that ran no tests, has no pass rate yet: it is
 * `null`, a gap in the line — never a 0 that would draw a crash nobody had.
 */
function buildPassRateSeries(runs: readonly TestRun[]): (number | null)[] {
  return [...runs]
    .sort((a, b) => +new Date(a.created_at) - +new Date(b.created_at))
    .map(measuredRunPassRate)
}

function computeRedStreak(runs: TestRun[]): number {
  // Walk newest → oldest, count consecutive failures. A build still in
  // progress has no result yet: it neither extends nor breaks the streak (it
  // broke it, and a broken pipeline read "Red streak 0 in a row" while its
  // next build ran — the UX redesign's browser E2E pass).
  const sorted = [...runs].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
  let streak = 0
  for (const r of sorted) {
    if (isRunInProgress(r.status)) continue
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

/**
 * The window's pipeline model. `signatures` is the failure-signature grouping
 * (`buildSignatureModel` over the batch read); without it — not loaded yet —
 * Signature diversity is not measured.
 */
export function buildPipelineModel(
  runs: TestRun[],
  signatures: SignatureModel = buildSignatureModel(runs, undefined),
): PipelineModel {
  const totalRuns      = runs.length
  const failedRuns     = runs.filter(r => isFailed(r.status)).length
  const passedRuns     = runs.filter(r => isPassed(r.status)).length
  const inProgressRuns = runs.filter(r => isInProgress(r.status)).length
  // The average is over MEASURED builds only — the same builds the sparkline
  // draws. A running build or one that ran no tests has no pass rate; reading
  // it as 0 % dragged the number down while the line beside it left a gap.
  // With nothing measured the average is `null` ("—"), never 0 %.
  const passRateSeries = buildPassRateSeries(runs)
  const measuredRates  = passRateSeries.filter((v): v is number => v !== null)
  const measuredRuns   = measuredRates.length
  const avgPassRate    = measuredRuns > 0
    ? measuredRates.reduce((s, v) => s + v, 0) / measuredRuns
    : null
  const redStreak = computeRedStreak(runs)
  const lastGreen = findLastGreen(runs)
  const latestFailedRun = findLatestFailed(runs)

  // Metadata coverage — % of runs that have branch + release_name + duration.
  const withMeta = runs.filter(r => !!r.branch && !!r.release_name && (r.duration_ms ?? 0) > 0).length
  const metadataCoveragePct = totalRuns > 0 ? (withMeta / totalRuns) * 100 : 0
  const hasMissingMetadata = totalRuns > 0 && metadataCoveragePct < 80

  // Build success — straight % passed.
  const buildSuccessScore = totalRuns > 0 ? (passedRuns / totalRuns) * 100 : 0
  // Signature diversity — when failures group heavily into one error, the
  // score is LOW (one root cause masquerading as many incidents):
  // groups / grouped failed builds (clipped to 100); 0 failures = 100. Not
  // measured (`null`) while the signatures load or when none could be read.
  const signatureDiversityScore = signatures.diversityScore
  // Fix velocity — proxy: hours since last green, mapped 0h→100, 72h→0.
  const hoursSinceLastGreen = lastGreen ? hoursSince(lastGreen.created_at) : null
  const fixVelocityScore = hoursSinceLastGreen == null
    ? 0
    : Math.max(0, 100 - (hoursSinceLastGreen / 72) * 100)
  const metadataCoverageScore = metadataCoveragePct

  const dimension = (id: DimensionId, label: string, score: number | null): DimensionScore => ({
    id, label, score, measured: score !== null, weight: WEIGHTS[id], tone: toneFor(score ?? 0),
  })
  const dimensions: DimensionScore[] = [
    dimension('build_success',       'Build success',       buildSuccessScore),
    dimension('signature_diversity', 'Signature diversity', signatureDiversityScore),
    dimension('fix_velocity',        'Fix velocity',        fixVelocityScore),
    dimension('metadata_coverage',   'Metadata coverage',   metadataCoverageScore),
  ]
  // Σ score·w / Σ w over the MEASURED dimensions: an unmeasured one is left
  // out and the others re-weighted, never scored as 0.
  // With every dimension measured the weights sum to 1 and this is exactly
  // the weighted sum it always was.
  const measured = dimensions.filter((d): d is DimensionScore & { score: number } => d.score !== null)
  const weighted = measured.reduce((sum, d) => sum + d.score * d.weight, 0)
  const weightSum = measured.reduce((sum, d) => sum + d.weight, 0)
  const composite = measured.length === dimensions.length
    ? Math.round(weighted)
    : weightSum > 0 ? Math.round(weighted / weightSum) : 0

  return {
    composite, dimensions,
    totalRuns, failedRuns, passedRuns, inProgressRuns,
    avgPassRate,
    redStreak, lastGreen, latestFailedRun,
    hoursSinceLastGreen,
    signatures,
    hasMissingMetadata, metadataCoveragePct,
    velocityCells: buildVelocityCells(runs),
    passRateSeries,
    measuredRuns,
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
  const cls = 'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50'
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
      className="btn-primary inline-flex items-center gap-1.5"
    >
      {children}
    </button>
  )
}

function GhostSelect<T extends string | number>({
  value, onChange, options, ariaLabel, className, title,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  ariaLabel?: string
  className?: string
  title?: string
}) {
  return (
    <select
      aria-label={ariaLabel}
      title={title}
      value={String(value)}
      onChange={(e) => {
        const next = options.find(o => String(o.value) === e.target.value)?.value
        if (next != null) onChange(next as T)
      }}
      className={clsx('text-sm px-2.5 py-1.5 rounded-md cursor-pointer', className)}
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

// ── Verdict banner ────────────────────────────────────────────────────────
/** The verdict in words: the banner's title after the state pill. */
function verdictSummary(model: PipelineModel, verdict: Verdict): string {
  if (verdict === 'PENDING') return 'no builds in window'
  if (verdict === 'HEALTHY') return `${model.totalRuns} builds passing`
  const failed = `${model.failedRuns} of ${model.totalRuns} builds failed`
  // "The same error" only when the data shows it: the primary group holds at
  // least 80 % of the failed builds the signatures grouped (two or more).
  const { groups, groupedRuns } = model.signatures
  const sharesError = verdict === 'BROKEN'
    && groups.length > 0
    && groupedRuns >= 2
    && groups[0].members.length / groupedRuns >= 0.8
  return sharesError ? `${failed} with the same error` : failed
}

/**
 * The StatusBanner's facts for the window (UX redesign P3: the banner replaces
 * the verdict card and the KPI strip that repeated it). The last green
 * build, the red streak, and the average pass rate over MEASURED builds —
 * "—", never 0 %, when no build has a result yet. No "Failing builds" fact:
 * the title already says "N of M builds failed" (or "N builds passing"), and
 * repeating it wrapped the banner onto a second line at 1280 px.
 */
function runsBannerFacts(model: PipelineModel): BannerFact[] {
  // The age in the table's own words (`shortAgo`: "5h ago", "7d ago"), never
  // a raw hour count ("172h ago").
  const lastGreen = model.lastGreen
    ? `#${model.lastGreen.build_number}${model.hoursSinceLastGreen != null ? ` · ${shortAgo(model.lastGreen.created_at)}` : ''}`
    : 'none in window'
  return [
    { label: 'Last green', value: lastGreen },
    { label: 'Red streak', value: `${model.redStreak} in a row` },
    { label: 'Avg pass rate', value: model.avgPassRate === null ? '—' : `${model.avgPassRate.toFixed(1)}%` },
  ]
}

/**
 * "How this verdict is computed" (a Disclosure below the table): the composite
 * pipeline-health gauge, its four weighted dimensions, and — when the reporter
 * sends no branch / release / duration — the gap the Metadata coverage
 * dimension scores.
 */
export function VerdictDetails({ model, verdict }: { model: PipelineModel; verdict: Verdict }) {
  return (
    <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.4fr)' }}>
      <HealthMeter model={model} verdict={verdict} />
      <div className="flex min-w-0 flex-col gap-2.5">
        <DimensionGrid dimensions={model.dimensions} />
        {model.hasMissingMetadata && (
          <p className="m-0 text-sm leading-[1.45] text-[var(--color-text-secondary)]">
            Branch, release, and duration absent on {Math.round(100 - model.metadataCoveragePct)}% of rows — the Jenkins reporter isn't sending these fields.
            {' '}<span className="text-[var(--color-text-muted)]">Cannot tell which feature branch broke things.</span>
          </p>
        )}
      </div>
    </div>
  )
}

/** The health meter's scale: the band edges the pill uses, in words and values. */
const HEALTH_TICKS: readonly GaugeTick[] = [
  { value: 0, label: 'Blocked' },
  { value: 33, label: 'At risk' },
  { value: 66, label: 'Stable' },
  { value: 100 },
]

const VERDICT_GAUGE_TONE: Record<Verdict, GaugeTone> = {
  BROKEN: 'bad',
  MIXED: 'warn',
  HEALTHY: 'good',
  PENDING: 'neutral',
}

export function HealthMeter({ model, verdict }: { model: PipelineModel; verdict: Verdict }) {
  const t = VERDICT_THEME[verdict]
  const score = model.composite
  const pillLabel = score >= 66 ? 'Stable' : score >= 33 ? 'At risk' : 'Blocked'
  return (
    <div>
      <div className="eyebrow uppercase text-[var(--color-text-muted)] mb-1.5">
        Pipeline health
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
      {/* PENDING has no score: the header says "—", so the meter is an empty
          track ("not measured"), never a marker parked at 0. */}
      <div className="mt-3">
        <GaugeBar
          variant="marker"
          gradient="health"
          tone={VERDICT_GAUGE_TONE[verdict]}
          value={verdict === 'PENDING' ? null : score}
          label="Pipeline health"
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

/**
 * One weighted dimension: the KPI tile's in-panel size (W29b UIX-09: MetricCard sm, the sunken well; it replaced
 * a hand-made tile on an invisible white sheen). Not measured: "—" and an empty bar, never a 0 (the composite
 * leaves it out); the label's hover says so.
 */
function DimensionTile({ dim }: { dim: DimensionScore }) {
  const measured = dim.score !== null
  return (
    <MetricCard
      size="sm"
      title={dim.label}
      hint={measured ? undefined : `${dim.label}: not measured, left out of the score`}
      note={`${Math.round(dim.weight * 100)}%`}
      metric={{ value: dim.score === null ? '—' : Math.round(dim.score) }}
      tone={measured ? dim.tone : 'neutral'}
      bar={dim.score}
      data-dimension={dim.id}
      data-dimension-measured={dim.measured ? 'true' : 'false'}
      valueData={{ 'data-dimension-value': '' }}
    />
  )
}

// ── Build history: the per-build pass rate ────────────────────────────────
/** A pass rate's fixed scale: the sparkline shows 80 → 95 as the small rise it is. */
const PASS_RATE_DOMAIN = [0, 100] as const

type Tone = 'good' | 'warn' | 'bad' | 'neutral'

/**
 * The window's average pass rate and the line it is the average of. Owner
 * decision OD-1 (Wave 2.5): a glyph draws a REAL series or nothing — every
 * build's pass rate, oldest first, a gap (never a 0) for a build with no
 * result; fewer than two measured builds draw no line.
 *
 * UX redesign P3: this was the KPI strip's "Avg pass rate" cell. The strip
 * repeated the banner, so it went; the banner states the average, and this
 * cell — in "Build history" below the table — draws where it came from.
 */
export function BuildPassRate({ model }: { model: PipelineModel }) {
  const avg = model.avgPassRate
  const tone: Tone = avg === null ? 'neutral' : avg >= 80 ? 'good' : avg >= 50 ? 'warn' : 'bad'
  const hasTrend = buildSparklineModel(model.passRateSeries) !== null
  const meta =
    avg === null ? 'not measured — no build has a result yet'
    : model.failedRuns === model.totalRuns ? 'misleading — every build still failed'
    : model.measuredRuns < model.totalRuns ? `${model.measuredRuns} of ${model.totalRuns} builds measured`
    : `${model.passedRuns} pass · ${model.failedRuns} fail`
  // The KPI tile (W29b UIX-09: MetricCard md): the value, the per-build line as its aside, the meta line as its
  // caption. The section keeps the cell's name ("Average pass rate") for the tests and the region readers.
  return (
    <section aria-label="Average pass rate" className="min-w-0">
      <MetricCard
        title="Avg pass rate"
        metric={{ value: avg === null ? '—' : `${avg.toFixed(1)}%` }}
        tone={tone}
        caption={meta}
        sparkline={hasTrend ? (
          <Sparkline
            series={model.passRateSeries}
            label="Pass rate per build, oldest first"
            domain={PASS_RATE_DOMAIN}
            format={(v) => `${v.toFixed(1)}%`}
            tone={tone}
            height={24}
            className="w-24"
          />
        ) : undefined}
      />
    </section>
  )
}

// ── Failure signatures (the side panel) ───────────────────────────────────
// Rows shown per page in the failure-signature list: a group is EXPECTED to
// be large (its premise is "many builds share one error"), so it pages.
export const SIGNATURE_ROWS_PER_PAGE = 8

/**
 * The failure signatures, in the side panel (UX redesign P3: a drill-down
 * opens beside the page instead of a card under the table; FAIL-03: the
 * failed builds grouped by the error most of their failing tests share). Each
 * group is titled by that error's first line and lists its builds; "the same
 * test fails in all of them" is said only when every member names the test.
 * Every row, paginated; a row jumps to its run in the table. Error lines and
 * test names come from CI files: they render as text, never markup.
 */
export function SignatureClusters({
  model, failed = false, onJumpToRow,
}: {
  model: SignatureModel
  /** The signatures request failed. */
  failed?: boolean
  onJumpToRow: (run: TestRun) => void
}) {
  const [sigPage, setSigPage] = useState(1)

  // Group membership changes when the project or time window changes; without
  // a reset a viewer parked on page 3 would land on an empty list.
  //
  // Adjusted during render rather than in an effect: an effect would paint the
  // stale page first and then re-render, and this repo forbids synchronous
  // setState inside useEffect (react-hooks set-state-in-effect, an error here)
  // precisely because of those cascading renders.
  const clusterKey = model.groups.map((g) => `${g.key}:${g.members.length}`).join('|')
  const [prevClusterKey, setPrevClusterKey] = useState(clusterKey)
  if (prevClusterKey !== clusterKey) {
    setPrevClusterKey(clusterKey)
    setSigPage(1)
  }

  if (model.failedRuns === 0) {
    return (
      <p className="m-0 py-6 text-center text-sm text-[var(--color-text-secondary)]">
        No failures in this window — nothing to group.
      </p>
    )
  }
  if (failed) {
    return (
      <p className="m-0 py-6 text-center text-sm text-[var(--color-text-secondary)]" data-signatures-unavailable="">
        Signatures unavailable: the request failed.
      </p>
    )
  }
  const primary = model.groups[0]
  if (!primary) {
    return (
      <p className="m-0 py-6 text-center text-sm text-[var(--color-text-muted)]" aria-busy="true">
        Grouping the failed builds by their error…
      </p>
    )
  }
  const { groups, groupedRuns, failedRuns } = model
  const primaryCount = primary.members.length
  const otherCount = groupedRuns - primaryCount
  const allRows = groups.flatMap((group, gi) => group.members.map((run) => ({ group, run, isPrimary: gi === 0 })))
  const sigPages = Math.max(1, Math.ceil(allRows.length / SIGNATURE_ROWS_PER_PAGE))
  // Clamp rather than trust state: a shrinking group can leave sigPage past
  // the end for the render that happens before the reset above.
  const safePage = Math.min(sigPage, sigPages)
  const pageRows = allRows.slice(
    (safePage - 1) * SIGNATURE_ROWS_PER_PAGE,
    safePage * SIGNATURE_ROWS_PER_PAGE,
  )
  const s = (n: number) => (n === 1 ? '' : 's')

  return (
    <div className="flex flex-col gap-3" data-signature-clusters="">
      <p className="m-0 text-sm text-[var(--color-text-secondary)]" data-signature-lead="">
        {groups.length} signature{s(groups.length)} across {groupedRuns} failed build{s(groupedRuns)}
        {groupedRuns < failedRuns && ` · grouped over the newest ${groupedRuns} of ${failedRuns} failed builds`}
      </p>
      <GaugeBar
        value={groupedRuns}
        domain={[0, groupedRuns]}
        size="sm"
        label="Failed builds by signature"
        segments={[
          { value: primaryCount, label: 'Primary signature', tone: 'bad' },
          { value: otherCount, label: 'Other signatures', tone: 'neutral' },
        ]}
      />
      <div className="flex flex-col gap-1.5">
        {pageRows.map(({ group, run, isPrimary }, i) => {
          const startsGroup = i === 0 || pageRows[i - 1].group.key !== group.key
          const item = model.itemOf.get(run.id)
          return (
            <Fragment key={run.id}>
              {startsGroup && <SignatureGroupHeader group={group} isPrimary={isPrimary} />}
              <ClusterRow run={run} isPrimary={isPrimary} onClick={() => onJumpToRow(run)} />
              {item?.capped && (
                <p className="m-0 -mt-1 pl-2.5 text-2xs text-[var(--color-text-muted)]" data-signature-capped="">
                  (grouped over {item.read} of {item.failing_tests} failing tests)
                </p>
              )}
            </Fragment>
          )
        })}
      </div>
      {groups.length > 1 && (
        <p className="text-xs m-0 italic" style={{ color: 'var(--color-text-muted)', lineHeight: 1.5 }}>
          {otherCount} failed build{s(otherCount)} carr{otherCount === 1 ? 'ies' : 'y'} a different error. Open {otherCount === 1 ? 'it' : 'them'} before counting {otherCount === 1 ? 'it' : 'them'} as a separate incident.
        </p>
      )}
      {/* Renders nothing at one page, so small groups look exactly as before. */}
      <Pagination
        page={safePage}
        pages={sigPages}
        total={allRows.length}
        onChange={setSigPage}
      />
    </div>
  )
}

/** A group's header in the panel: its error's first line, its size, and what its builds share. */
function SignatureGroupHeader({ group, isPrimary }: { group: SignatureGroup; isPrimary: boolean }) {
  const n = group.members.length
  const shared = group.sharedTests[0]
  return (
    <div className="mt-1.5 first:mt-0" data-signature-group={isPrimary ? 'primary' : 'other'}>
      <div className="flex items-baseline justify-between gap-2">
        <code
          className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]"
          title={group.key}
          style={{ color: isPrimary ? 'var(--status-failed)' : 'var(--status-broken)' }}
        >
          {group.title}
        </code>
        <span className="shrink-0 text-xs tabular-nums text-[var(--color-text-muted)]">
          {n} build{n === 1 ? '' : 's'}
        </span>
      </div>
      {shared ? (
        <p className="m-0 mt-0.5 text-xs text-[var(--color-text-secondary)]" data-signature-shared="">
          {shared} fails with this error in all {n} builds.
        </p>
      ) : n >= 2 ? (
        <p className="m-0 mt-0.5 text-xs text-[var(--color-text-secondary)]">
          {n} builds fail with this error.
        </p>
      ) : null}
    </div>
  )
}

const passRateColor = (pct: number) =>
  pct >= 80 ? 'var(--status-passed)' : pct >= 50 ? 'var(--status-broken)' : 'var(--status-failed)'

/**
 * A run's pass rate in the table, or "—" when it has none yet (OD-18,
 * `measuredRunPassRate`): the API sends `null` (or 0) while a run is in
 * progress, and `?? 0` drew the newest build as a red 0.0 % — the worst in
 * the window — before it had a result (the UX redesign's browser E2E pass).
 */
function RunPassRateValue({ run }: { run: TestRun }) {
  const pct = measuredRunPassRate(run)
  if (pct === null) {
    const why = isRunInProgress(run.status) ? 'the run is still in progress' : 'the run reported no tests'
    return <span className="text-[var(--color-text-muted)]" title={`No pass rate: ${why}`}>—</span>
  }
  return <span style={{ color: passRateColor(pct) }}>{pct.toFixed(1)}%</span>
}

function ClusterRow({
  run, isPrimary, onClick,
}: {
  run: TestRun
  isPrimary: boolean
  onClick: () => void
}) {
  const pct = measuredRunPassRate(run)
  const passFlex = run.passed_tests
  const failFlex = run.failed_tests
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
        background: isPrimary ? 'transparent' : 'color-mix(in srgb, var(--status-broken) 5%, transparent)',
      }}
    >
      <span className="text-xs tabular-nums truncate text-[var(--color-text-secondary)]">
        <strong className="text-[var(--color-text)]">#{String(run.build_number)}</strong>
        <span className="ml-1.5 font-mono text-2xs">{run.id.slice(0, 8)}</span>
      </span>
      <div className="flex h-3.5 rounded-sm overflow-hidden" style={{ background: 'var(--color-bg-secondary)' }}>
        {passFlex > 0 && <span style={{ flex: passFlex, background: 'var(--status-passed)' }} />}
        {failFlex > 0 && <span style={{ flex: failFlex, background: 'var(--status-failed)' }} />}
      </div>
      <span
        className="text-sm font-semibold tabular-nums text-right"
        style={{ color: pct === null ? 'var(--color-text-muted)' : isPrimary ? passRateColor(pct) : 'var(--status-broken)' }}
      >
        {pct === null ? '—' : `${pct.toFixed(1)}%`}
      </span>
      <span
        className="eyebrow uppercase text-center px-1.5 py-0.5 rounded-full justify-self-end"
        style={{
          background: isPrimary ? 'color-mix(in srgb, var(--status-failed) 15%, transparent)' : 'color-mix(in srgb, var(--status-broken) 16%, transparent)',
          color:      isPrimary ? 'var(--status-failed-ink)' : 'var(--status-broken-ink)',
        }}
      >
        {isPrimary ? 'primary' : 'other'}
      </span>
    </button>
  )
}

// ── Runs table ────────────────────────────────────────────────────────────
/**
 * A failed, grouped row's signature (FAIL-03): a button, the way into the
 * failure-signature side panel (P3: the cluster card became a drill-down),
 * titled by its group's error. The label keeps the old chip's width
 * (`chipLabelOf`: at most 11 characters), which the table's 1280 px fit
 * depends on.
 */
function SignatureChip({
  label, title, isOutlier, onOpen,
}: { label: string; title: string; isOutlier: boolean; onOpen: () => void }) {
  const hue = isOutlier ? 'var(--status-broken)' : 'var(--status-failed)'
  // The words on the chip's own tint read the hue's -ink (>= 4.5:1 in every theme, W29a UIX-03).
  const ink = isOutlier ? 'var(--status-broken-ink)' : 'var(--status-failed-ink)'
  return (
    <button
      type="button"
      onClick={onOpen}
      title={title}
      aria-label={`Signature ${label}: show the failure signatures`}
      data-signature-chip=""
      className={clsx(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-2xs whitespace-nowrap hover:underline',
        isOutlier && 'italic',
      )}
      style={{
        background: `color-mix(in srgb, ${hue} 10%, transparent)`,
        border: `1px solid color-mix(in srgb, ${hue} 25%, transparent)`,
        color: ink,
      }}
    >
      <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
      {label}
    </button>
  )
}

/**
 * The runs table is the ONE table (W29b UIX-06) at `compact` density: 8 px a
 * side, 6 px top and bottom. P4 had cut its cells to 10 px a side to keep the
 * Tests bar drawn (its 24 px floor) while the table fits its card at a 1280 px
 * window; compact gives back 4 px more per column, which the 13 px body and
 * the 11 px verdict chip of the type scale take (`fold-runs.spec.ts` holds the
 * table inside its card at 1280 and 1440).
 */
function RunsTable({
  runs, signatures, signatureRequest, verdictOf, verdictsLoading,
  selectedIds, setSelectedIds, onTrigger, onDeep,
  onCompareWithPrevious, onOpenSignatures,
  isQaEngineer,
  page, pages, total, onPageChange,
  datetimeSortDir, onToggleDatetimeSort,
  filtered, loading, onClearFilters,
}: {
  runs: TestRun[]
  /** The failed builds grouped by error (FAIL-03). */
  signatures: SignatureModel
  signatureRequest: SignatureRequestState
  /** Each run's AI verdict from the one batch read (FAIL-04). */
  verdictOf: (run: TestRun) => AiVerdict | undefined
  verdictsLoading: boolean
  /** A search / branch / verdict filter is set (FAIL-05). */
  filtered: boolean
  /** The rows are being fetched for a changed filter. */
  loading: boolean
  onClearFilters: () => void
  selectedIds: Set<string>
  setSelectedIds: (s: Set<string>) => void
  onTrigger: (id: string) => void
  onDeep: (id: string) => void
  /** Fired when the user clicks the per-row "Compare to previous" icon.
   *  The parent uses the full ``runs`` array (not just this page slice)
   *  to find the previous-of-same-suite candidate, so the lookup pool
   *  isn't broken by client-side pagination. */
  onCompareWithPrevious: (run: TestRun) => void
  /** A row's signature chip opens the failure-signature side panel. */
  onOpenSignatures: () => void
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
    <Panel
      title={<>Runs <span className="text-xs text-[var(--color-text-muted)] font-normal ml-2"><strong>{total}</strong> in window · sorted by Started {datetimeSortDir === 'desc' ? '↓' : '↑'} · showing {runs.length} on this page</span></>}
      actions={
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs">{selectedCount} selected</span>
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
      {/* Wide table (9 cols) — a min width so horizontal scroll kicks in
          cleanly on narrow viewports instead of columns getting squeezed and
          clipped. P4 (the AI verdict came in, the "Intel" link went): the
          columns' min-content width is under the card's 974 px at a 1280 px
          window (Build and the signature chip do not wrap; compact density);
          `fold-runs.spec.ts` holds the table inside its card at 1280 and
          1440. It was 980: 6 px of sideways scroll at 1280. The header row's
          sunken surface is the `.th` cells' (W29b UIX-06). */}
      <div className="overflow-x-auto" data-runs-table-scroller="">
        <Table density="compact" style={{ minWidth: 960 }}>
          <thead className="border-b border-[var(--color-border)]">
            <tr>
              <Th className="w-8">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={el => { if (el) el.indeterminate = !allSelected && someSelected }}
                  onChange={toggleAll}
                  aria-label="Select all visible runs"
                />
              </Th>
              {/* The suite is under the run number ("Run #60" counts per
                  suite): a column of its own put the table 66 px past its
                  card at 1280 px on CI's font, the row actions off-screen.
                  Build and Pass rate do not sort (no handler): plain headers,
                  no inert arrow. */}
              <Th>Build</Th>
              <Th>Signature</Th>
              <Th>Status</Th>
              <Th>AI verdict</Th>
              <Th numeric>Tests</Th>
              <Th numeric>Pass rate</Th>
              <ThSort
                label="Timing"
                sortKey="timing"
                currentKey="timing"
                dir={datetimeSortDir}
                onSort={onToggleDatetimeSort}
                numeric
              />
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={9} className="text-center py-10 text-[var(--color-text-muted)]" data-runs-empty="">
                  {loading ? (
                    'Loading runs…'
                  ) : filtered ? (
                    <>
                      No runs match these filters.{' '}
                      <button
                        type="button"
                        onClick={onClearFilters}
                        className="ml-1 text-[var(--color-accent)] hover:underline"
                      >
                        Clear filters
                      </button>
                    </>
                  ) : (
                    'No runs in the window. Try a longer window or check your reporter.'
                  )}
                </td>
              </tr>
            )}
            {runs.map((r) => {
              const sigCell = signatureCellOf(r, signatures, signatureRequest)
              const isOutlier = sigCell.kind === 'chip' && sigCell.isOutlier
              const failed = isFailed(r.status)
              return (
                // The outlier tint is the row's DATA (its build carries a different error) and stays; the zebra's
                // even rows were the card's own colour (no pixel), and as an inline style they blocked the hover.
                <Tr
                  id={`run-row-${r.id}`}
                  key={r.id}
                  interactive={false}
                  style={isOutlier ? { background: 'color-mix(in srgb, var(--status-broken) 3%, transparent)' } : undefined}
                >
                  <Td>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(r.id)}
                      onChange={() => toggleOne(r.id)}
                      aria-label={`Select #${r.build_number}`}
                    />
                  </Td>
                  {/* Build column links to the run detail page. Header
                      label is "Build" for historical continuity, but the
                      VALUE is the human-readable, per-(project, suite)
                      incremental "Run #N" — server-side ROW_NUMBER()
                      over the partition. Falls back to the raw SDK
                      build_number for legacy rows that pre-date the
                      run_seq field. */}
                  <Td className="font-mono text-sm font-semibold whitespace-nowrap">
                    {/* The job and branch the retired `/intelligence` rows
                        printed under the build (P4): on hover here, where
                        the table has no width left for a second line. */}
                    <Link
                      to={`/runs/${r.id}`}
                      title={[r.jenkins_job, r.branch ? `branch ${r.branch}` : null].filter(Boolean).join(' · ') || undefined}
                      className="text-[var(--color-text)] hover:text-[var(--color-accent)] hover:underline"
                    >
                      {r.run_seq != null ? `Run #${r.run_seq}` : `#${String(r.build_number)}`}
                    </Link>
                    {r.ingestion_source === 'upload' && (
                      <span
                        className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-medium align-middle"
                        style={{
                          background: 'var(--ui-info-bg)',
                          border: '1px solid var(--ui-info-bd)',
                          color: 'var(--ui-info-ink)',
                        }}
                        title="Results were manually uploaded from a report file"
                      >
                        Uploaded
                      </span>
                    )}
                    <div className="mt-1 font-sans font-normal" data-run-suite="">
                      <SuiteBadge
                        primary={r.primary_suite_name}
                        all={r.suite_names}
                        linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
                      />
                    </div>
                  </Td>
                  <Td data-run-signature="">
                    {sigCell.kind === 'chip' ? (
                      <SignatureChip
                        label={sigCell.group.chipLabel}
                        title={sigCell.group.title}
                        isOutlier={sigCell.isOutlier}
                        onOpen={onOpenSignatures}
                      />
                    ) : (
                      <span className="text-[var(--color-text-muted)]" title={sigCell.title} data-signature-none="">—</span>
                    )}
                  </Td>
                  <Td>
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-2xs font-semibold"
                      style={{
                        background: failed ? 'color-mix(in srgb, var(--status-failed) 15%, transparent)' : isPassed(r.status) ? 'color-mix(in srgb, var(--status-passed) 15%, transparent)' : 'var(--color-bg-secondary)',
                        color:      failed ? 'var(--status-failed-ink)' : isPassed(r.status) ? 'var(--status-passed-ink)' : 'var(--color-text-muted)',
                      }}
                    >
                      {(r.status || '—').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())}
                    </span>
                  </Td>
                  <Td data-ai-verdict-cell="">
                    <AiVerdictCell run={r} verdict={verdictOf(r)} isLoading={verdictsLoading && !isRunInProgress(r.status)} />
                  </Td>
                  <Td numeric>
                    <div className="flex items-center justify-end gap-2">
                      <div className="flex h-3 rounded-sm overflow-hidden flex-1 min-w-[24px] max-w-[80px]" style={{ background: 'var(--color-bg-secondary)' }}>
                        {r.passed_tests > 0 && <span style={{ flex: r.passed_tests, background: 'var(--status-passed)' }} />}
                        {r.failed_tests > 0 && <span style={{ flex: r.failed_tests, background: 'var(--status-failed)' }} />}
                      </div>
                      {/* Two groups that wrap between them when the column is
                          tight (passed · failed over the total), never inside one. */}
                      <span className="text-2xs leading-tight tabular-nums text-[var(--color-text-muted)]" data-run-tests-counts="">
                        <span className="whitespace-nowrap">
                          <span style={{ color: 'var(--status-passed)' }}>{r.passed_tests}</span> · <span style={{ color: 'var(--status-failed)' }}>{r.failed_tests}</span> ·
                        </span>{' '}
                        <span className="whitespace-nowrap">{r.total_tests} total</span>
                      </span>
                    </div>
                  </Td>
                  <Td numeric className="font-semibold" data-run-pass-rate="">
                    <RunPassRateValue run={r} />
                  </Td>
                  {/* EXEC-04b: the wall clock when the run reported one, else the
                      summed test time, each labelled; both in the tooltip. The
                      cell's own padding is the table's compact one. */}
                  <TimingCell
                    started={r.start_time ?? r.created_at}
                    end={r.end_time}
                    durationMs={r.duration_ms}
                    wallClockMs={r.wall_clock_ms ?? null}
                    className="!px-2 !py-1.5"
                  />
                  <Td className="text-right">
                    {/* P4: no separate "Intel" link — the AI verdict cell opens
                        the run's Analysis tab (`/runs/:id?tab=analysis`). */}
                    <div className="inline-flex items-center gap-1.5">
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
                  </Td>
                </Tr>
              )
            })}
          </tbody>
        </Table>
      </div>
      <Pagination page={page} pages={pages} total={total} onChange={onPageChange} />
    </Panel>
  )
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
        <h3 id="lastgreen" className="text-sm font-semibold m-0 text-[var(--color-text)]">Last green build</h3>
        <span
          className="eyebrow inline-flex items-center px-1.5 py-0.5 rounded-full uppercase"
          style={{ background: 'color-mix(in srgb, var(--status-passed) 15%, transparent)', color: 'var(--status-passed-ink)' }}
        >
          Bisect target
        </span>
      </div>
      <p className="text-sm m-0 mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        The last green Jenkins run is <code className="font-mono text-xs">#{model.lastGreen.build_number}</code>{' '}
        at SHA <code className="font-mono text-xs">{sha}</code>, {hours} hour{hours === 1 ? '' : 's'} ago.
        {' '}{model.failedRuns} build{model.failedRuns === 1 ? '' : 's'} since{' '}
        {model.failedRuns === 1 ? 'has failed' : 'have all failed'}. Bisect or revert from that SHA.
      </p>
      {/* The two callout stats are the KPI tile's in-panel size (W29b UIX-09). */}
      <div className="grid gap-2 mt-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <MetricCard size="sm" title="Last green" metric={{ value: sha }} tone="good" />
        <MetricCard size="sm" title="Hours ago" metric={{ value: hours }} />
      </div>
      <div className="flex flex-wrap gap-2 mt-3">
        <PrimaryBtn onClick={onBisect}>
          <GitBranch className="h-3.5 w-3.5" /> Start bisect
        </PrimaryBtn>      </div>
    </section>
  )
}

// ── Build velocity card ──────────────────────────────────────────────────
export function BuildVelocityCard({ cells, redStreak }: { cells: DayStripCell[]; redStreak: number }) {
  const n = countTones(cells)
  return (
    <Panel title="Build velocity · 14d" actions={<span>{redStreak} reds in a row</span>}>
      <div className="px-4 pt-1 pb-3.5">
        <p className="text-xs text-[var(--color-text-muted)] m-0 mb-2.5" style={{ lineHeight: 1.5 }}>
          Each cell is one build, oldest left.
        </p>
        <DayStrip
          mode="status"
          cells={cells}
          unit="build"
          gap={6}
          title="Build velocity"
          label={`Build velocity over the last 14 builds: ${n.pass} passed, ${n.fail} failed, ${n.none} no-build cells.`}
          endLabel="Now"
          text={{ none: 'No build', today: 'Latest build' }}
        />
      </div>
    </Panel>
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

  // Manual report upload. Opens from the header's primary button
  // or via the deep-link ``/runs?upload=1`` (bookmarks, docs), as a form in the
  // page right under the header. The form asks for the project itself, so All
  // Projects does not block it.
  const [searchParams, setSearchParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(false)
  // Auto-open the upload form when the deep-link condition (``?upload=1`` with
  // the flag enabled) first becomes true. Tracked during render via the
  // previous-value pattern instead of a setState-in-effect so a transition
  // false→true opens once, and re-navigating after a close re-opens.
  const uploadRequested = searchParams.get('upload') === '1' && uploadEnabled
  const shouldAutoOpenUpload = uploadRequested && isQaEngineer
  // Why the deep link could not honour the request. The reason has to be
  // *rendered*: it previously lived only in the disabled button's `title`, so
  // arriving from the "Upload Report" link showed an unchanged runs list and no
  // explanation — the reported bug.
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

  // Global shared time-window preference — the toolbar's WindowPicker writes
  // it, and a window picked on any other page arrives here (snapped to the
  // picker's 24h-90d set, as the picker itself snaps it).
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, DEFAULT_WINDOW_OPTIONS)

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('')
  // The page-local suite filter (usePageSuiteFilter).
  const { selectedSuite, setSelectedSuite, suiteFilter: pageSuiteFilter, suiteLabel } = usePageSuiteFilter()

  // FAIL-05: search, branch and AI verdict live in the URL (`?q=&branch=&verdict=`),
  // so a reload or a shared link restores the view. The branch select lists
  // one project's branches: in All Projects it is hidden and a `branch` in
  // the URL is not applied (a filter nobody can see or change).
  const urlFilters = readRunListFilters(searchParams)
  const urlQ = urlFilters.q
  const branchFilter = isAllProjects ? '' : urlFilters.branch
  const verdictFilter = urlFilters.verdict as VerdictFilter
  const filtersActive = Boolean(urlQ || branchFilter || verdictFilter)
  // react-router hands a functional setSearchParams the params of the last RENDER, and its navigations are
  // transitions: a Branch and an AI verdict picked before the first one rendered asked with the verdict alone
  // (runs-list-filters.spec, under load). Each filter write starts from the newest query this page wrote, until
  // a render shows that query (or a URL from elsewhere: back, a link).
  const latestQuery = useRef(searchParams)
  const pendingQueries = useRef<string[]>([])
  useEffect(() => {
    const at = pendingQueries.current.indexOf(searchParams.toString())
    pendingQueries.current = at === -1 ? [] : pendingQueries.current.slice(at + 1)
    if (pendingQueries.current.length === 0) latestQuery.current = searchParams
  }, [searchParams])
  const writeFilters = useCallback((values: Partial<Record<'q' | 'branch' | 'verdict', string>>) => {
    const next = new URLSearchParams(latestQuery.current)
    for (const [name, value] of Object.entries(values)) {
      if (value) next.set(name, value)
      else next.delete(name)
    }
    latestQuery.current = next
    pendingQueries.current.push(next.toString())
    setSearchParams(next, { replace: true })
  }, [setSearchParams])
  // An unknown `verdict` is ignored, and dropped from the URL.
  const rawVerdict = searchParams.get('verdict') ?? ''
  const verdictIsUnknown = rawVerdict !== '' && !isVerdictFilter(rawVerdict)
  useEffect(() => {
    if (!verdictIsUnknown) return
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('verdict')
      return next
    }, { replace: true })
  }, [verdictIsUnknown, setSearchParams])

  // The search box: what is typed shows at once; the URL (and so the runs
  // request) follows SEARCH_DEBOUNCE_MS after the last key — one request per
  // pause, not one per key. A URL changed from elsewhere (Clear filters, back)
  // replaces the text.
  const [searchText, setSearchText] = useState(urlQ)
  const [pendingQ, setPendingQ] = useState<string | null>(null)
  const [syncedQ, setSyncedQ] = useState(urlQ)
  if (syncedQ !== urlQ) {
    setSyncedQ(urlQ)
    if (searchText.trim() !== urlQ) setSearchText(urlQ)
  }
  useEffect(() => {
    if (pendingQ === null) return
    const timer = window.setTimeout(() => {
      setPendingQ(null)
      writeFilters({ q: pendingQ })
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [pendingQ, writeFilters])
  const clearSearch = () => {
    setSearchText('')
    setPendingQ(null)
    writeFilters({ q: '' })
  }
  const clearFilters = () => {
    setSearchText('')
    setPendingQ(null)
    writeFilters({ q: '', branch: '', verdict: '' })
  }
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // The failure-signature section (P3: the cluster card became a drill-down;
  // W27a: in the page above the table, no longer an edge panel).
  const [signaturesOpen, setSignaturesOpen] = useState(false)
  // A header button or a row chip while it is already open brings it back into view.
  const openSignatures = () => {
    if (signaturesOpen) {
      const section = document.querySelector<HTMLElement>('[data-signatures-section]')
      if (typeof section?.scrollIntoView === 'function') section.scrollIntoView({ block: 'nearest' })
    }
    setSignaturesOpen(true)
  }
  // Client-side table pagination. The banner, the signatures and the build
  // history consume the full fetched window so their derived metrics stay
  // accurate; only the table view is sliced.
  const TABLE_PAGE_SIZE = 25
  // Analytics fetch size. Was 50 (capped) which made the verdict's
  // denominator frozen at "of 50" no matter the selected window. 500 covers
  // realistic windows (1y of daily builds is ~365) so the verdict, the
  // signatures and the velocity strip summarise the actual window the user
  // picked.
  const ANALYTICS_FETCH_SIZE = 500
  const [tablePage, setTablePage] = useState(1)
  // Reset page + selection whenever the filters change so users aren't stuck
  // on an empty page after narrowing the window. Done during render via
  // previous-value tracking (the React-recommended way to reset state on a
  // dependency change) rather than a cascading setState-in-effect.
  const runsFilterKey = `${days}|${statusFilter}|${suiteLabel}|${urlQ}|${branchFilter}|${verdictFilter}`
  const [prevRunsFilterKey, setPrevRunsFilterKey] = useState(runsFilterKey)
  if (prevRunsFilterKey !== runsFilterKey) {
    setPrevRunsFilterKey(runsFilterKey)
    setTablePage(1)
    setSelectedIds(new Set())
  }

  const { options: suiteOptions } = useSuiteOptions(days)

  // Absent filters are not sent: an unfiltered page asks exactly what it did.
  const { data, isLoading, error: runsError, mutate: retryRuns } = useRuns({
    page: 1,
    size: ANALYTICS_FETCH_SIZE,
    days,
    ...(statusFilter && { status: statusFilter }),
    ...(pageSuiteFilter && { suite_name: pageSuiteFilter }),
    ...(urlQ && { q: urlQ }),
    ...(branchFilter && { branch: branchFilter }),
    ...(verdictFilter && { recommendation: verdictFilter }),
  })
  // While a search / branch / verdict change loads, the page keeps the rows it
  // had (same project, window, status and suite) instead of swapping itself
  // for the full-page spinner: the spinner unmounted the search box under the
  // reader's cursor. Held by a cheap content key, never by identity alone.
  const heldBase = `${activeProjectId}|${days}|${statusFilter}|${suiteLabel}`
  const liveKey = data ? `${heldBase}|${data.total}|${data.items?.length ?? 0}|${data.items?.[0]?.id ?? ''}` : null
  const [held, setHeld] = useState<{ key: string; base: string; data: TestRunListResponse } | null>(null)
  if (data && liveKey !== null && held?.key !== liveKey) setHeld({ key: liveKey, base: heldBase, data })
  const shownData = data ?? (held && held.base === heldBase ? held.data : undefined)
  // The branch select's options, once there is a list to filter (the
  // toolbar is not drawn while the list loads or after it failed).
  const { data: branchList } = useRunBranches(days, Boolean(shownData))
  // The header's "refreshed" note must report when this client actually
  // received the payload. It previously rendered a hardcoded "refreshed just
  // now", so a tab left open overnight still claimed the numbers above it were
  // seconds old -- the #893 defect.
  const fetchedAt = useDataFreshness(data)
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  const runs = useMemo<TestRun[]>(() => (shownData?.items ?? []) as TestRun[], [shownData?.items])
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
  // FAIL-03: the failure signatures of up to 50 failed builds (the visible
  // ones first, then the newest), ONE request; re-asked only when that id set
  // changes. FAIL-04: the AI verdicts of the runs on screen, ONE request.
  const signatureIds = useMemo(() => signatureRunIds(tableRuns, runs), [tableRuns, runs])
  const signatureBatch = useRunSignatures(signatureIds)
  const signatureModel = useMemo(() => buildSignatureModel(runs, signatureBatch.data), [runs, signatureBatch.data])
  const signatureRequest = useMemo<SignatureRequestState>(() => ({
    requested: new Set(signatureIds),
    loading: signatureBatch.isLoading,
    failed: Boolean(signatureBatch.error) && !signatureBatch.data,
  }), [signatureIds, signatureBatch.isLoading, signatureBatch.error, signatureBatch.data])
  const verdicts = useRunAiVerdicts(tableRuns)
  const model = useMemo(() => buildPipelineModel(runs, signatureModel), [runs, signatureModel])
  const verdict = pickVerdict(model)

  // The branch select's options: the project's branches in the window, plus
  // the URL's branch when the window does not hold it (it stays selectable).
  const branchOptions = useMemo(() => {
    const names = (branchList?.items ?? []).map((b) => b.branch)
    if (branchFilter && !names.includes(branchFilter)) names.unshift(branchFilter)
    return [{ value: '', label: 'All branches' }, ...names.map((name) => ({ value: name, label: name }))]
  }, [branchList?.items, branchFilter])

  // Bisect navigation — the banner's "Bisect from last green" and Build
  // history's "Start bisect". When either side is missing (no green run in
  // window, or no failing run to compare against), toast a clear reason
  // instead of nav'ing to a half-populated compare page.
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

  // Per-row "Compare to previous run" handler — asks the SERVER for the
  // previous run (W03, D42: same suite AND branch, the newest that finished
  // before this one), the same pair the run page's ⋯ menu and its Analysis
  // tab open. An event handler's one-off read, not a page read (no SWR).
  const handleCompareWithPrevious = async (run: TestRun): Promise<void> => {
    if (!run.primary_suite_name) {
      toast('This run has no suite attribution — cannot pick a previous-of-same-suite.', { icon: '⚠️' })
      return
    }
    let answer
    try {
      answer = await aiHistoryService.getPreviousRun(run.id)
    } catch {
      toast.error('Could not look up the previous run — try again in a moment.')
      return
    }
    const baseline = answer?.baseline
    const href = baseline
      ? buildCompareWithPreviousHref(run, { id: baseline.run_id, primary_suite_name: baseline.primary_suite_name })
      : null
    if (!href) {
      toast(
        answer?.message || `No earlier run of "${run.primary_suite_name}" on this branch was found.`,
        { icon: '⚠️' },
      )
      return
    }
    navigate(href)
  }

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Search className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view test runs."
      />
    )
  }

  // M21: a failed fetch is not an empty window. Without this the page fell
  // through to "No runs in the window. Try a longer window or check your
  // reporter." during an outage.
  if (runsError && !data) {
    return <DataUnavailable error={runsError} onRetry={() => void retryRuns()} testId="runs-data-unavailable" />
  }

  if (isLoading && !shownData) {
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

  const onJumpToRow = (run: TestRun) => {
    const el = document.getElementById(`run-row-${run.id}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.style.background = 'color-mix(in srgb, var(--status-broken) 10%, transparent)'
      window.setTimeout(() => { el.style.background = '' }, 1200)
    }
  }

  const theme = VERDICT_THEME[verdict]
  const canBisect = buildBisectHref(model) !== null
  const signatureCount = model.signatures.groups.length
  const subtitle = [
    `Jenkins builds for ${projectLabel}`,
    suiteLabel ? `Suite ${suiteLabel}` : null,
    `${model.totalRuns} build${model.totalRuns === 1 ? '' : 's'} in window`,
    filtersActive ? 'filtered' : null,
    `refreshed ${refreshedAt}`,
  ].filter(Boolean).join(' · ')
  // Header ⋯ (P3): the window-wide actions beyond the one primary + one secondary.
  const overflow: OverflowItem[] = [
    {
      label: `Trigger all failed (${model.failedRuns})`,
      icon: <Zap className="h-3.5 w-3.5" />,
      onClick: () => void handleTriggerAllFailed(),
      danger: true,
      disabled: !isQaEngineer || model.failedRuns === 0,
    },
    {
      label: `Deep all failed (${model.failedRuns})`,
      icon: <Stethoscope className="h-3.5 w-3.5" />,
      onClick: () => void handleDeepAllFailed(),
      disabled: !isQaEngineer || model.failedRuns === 0,
    },
  ]

  return (
    <PageShell className="space-y-4">
      <PageHeader
        compact
        title="Test Runs"
        subtitle={subtitle}
        helpTopic={HELP_TOPIC}
        actions={
          <>
            {/* The page's primary action (UX redesign P1): Upload left the
                sidebar, so this is where it is found. */}
            {uploadEnabled && (
              <PrimaryBtn
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
              </PrimaryBtn>
            )}
            {signatureCount > 0 && (
              <GhostBtn
                onClick={openSignatures}
                title="Failed builds grouped by the error most of their failing tests share"
              >
                <Layers className="h-3.5 w-3.5" />
                Failure signatures
                <span className="ml-1 px-1.5 py-px rounded font-mono text-2xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
                  {signatureCount}
                </span>
              </GhostBtn>
            )}
          </>
        }
        overflow={overflow}
      />

      {/* The upload form, in the page where Upload report lives (W27a PAN-03). */}
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

      {/* One toolbar row: the global window, the status, the suite, then
          (FAIL-05) the search, the branch and the AI verdict. ~1,050 px of
          controls against the ~1,168 px column at 1440 on CI's wider fonts;
          below that the row wraps (`flex-wrap`) rather than overflowing. */}
      <div className="flex flex-wrap items-center gap-3" data-runs-toolbar="">
        <WindowPicker />
        <GhostSelect
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as StatusFilter)}
          options={STATUS_FILTERS.map(s => ({ value: s, label: STATUS_LABELS[s] }))}
          ariaLabel="Run status"
        />
        <SuiteFilterSelect
          value={selectedSuite}
          onChange={setSelectedSuite}
          options={suiteOptions}
          allLabel="All suites"
        />
        <input
          type="search"
          aria-label="Search runs"
          placeholder="Build, branch, job or commit…"
          value={searchText}
          maxLength={100}
          onChange={(e) => {
            setSearchText(e.target.value)
            setPendingQ(e.target.value.trim())
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              clearSearch()
            }
          }}
          className="w-[180px] text-sm px-2.5 py-1.5 rounded-md"
          style={{
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text)',
          }}
        />
        {!isAllProjects && (
          <GhostSelect
            value={branchFilter}
            onChange={(v) => writeFilters({ branch: v })}
            options={branchOptions}
            ariaLabel="Branch"
            title={branchFilter || 'All branches'}
            className="max-w-[150px] truncate"
          />
        )}
        <GhostSelect
          value={verdictFilter}
          onChange={(v) => writeFilters({ verdict: v })}
          options={VERDICT_FILTERS.map(v => ({ value: v, label: VERDICT_FILTER_LABELS[v] }))}
          ariaLabel="AI verdict"
        />
      </div>

      <StatusBanner
        state={theme.banner}
        title={`${theme.label} · ${verdictSummary(model, verdict)}`}
        facts={runsBannerFacts(model)}
        action={canBisect ? { label: 'Bisect from last green', onClick: handleBisect } : undefined}
      />

      {/* The failure signatures: read-only, in the page above the table, which
          keeps its full card width (W27a PAN-03; the edge panel narrowed it). */}
      {signaturesOpen && (
        <InlineFormSection
          as="aside"
          title="Failure signatures"
          closeLabel="Close panel"
          onClose={() => setSignaturesOpen(false)}
          dataAttributes={{ 'data-signatures-section': '' }}
        >
          <SignatureClusters
            model={model.signatures}
            failed={signatureRequest.failed}
            onJumpToRow={onJumpToRow}
          />
        </InlineFormSection>
      )}

      {/* The page's primary content: the runs table. */}
      <div data-primary="">
        <RunsTable
          runs={tableRuns}
          signatures={model.signatures}
          signatureRequest={signatureRequest}
          verdictOf={verdicts.verdictOf}
          verdictsLoading={verdicts.isLoading}
          filtered={filtersActive}
          loading={isLoading && !data}
          onClearFilters={clearFilters}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onTrigger={handleTrigger}
          onDeep={handleDeep}
          onCompareWithPrevious={(run) => void handleCompareWithPrevious(run)}
          onOpenSignatures={openSignatures}
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

      <Disclosure
        title="How this verdict is computed"
        summary={verdict === 'PENDING' ? 'not measured' : `pipeline health ${model.composite} / 100`}
      >
        <VerdictDetails model={model} verdict={verdict} />
      </Disclosure>

      <Disclosure title="Build history" summary="the last 14 builds">
        <div className="grid items-start gap-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
          <BuildVelocityCard cells={model.velocityCells} redStreak={model.redStreak} />
          <div className="flex min-w-0 flex-col gap-3.5">
            <BuildPassRate model={model} />
            <LastGreenCallout model={model} onBisect={handleBisect} />
          </div>
        </div>
      </Disclosure>

      <div className="fixed bottom-4 left-4 right-4 lg:hidden text-center text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen needed for the full layout. Some sections may overflow on narrow viewports.
      </div>
    </PageShell>
  )
}
