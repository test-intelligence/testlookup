import type { ReactNode } from 'react'
import { Suspense, useCallback, useMemo, useState } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import { Clock, TrendingUp } from 'lucide-react'
import Sparkline from '@/components/charts/Sparkline'
import GaugeBar, { type GaugeTone } from '@/components/charts/GaugeBar'
import { dayWindow } from '@/components/charts/dayStrip.model'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import ScopedLink from '@/components/ui/ScopedLink'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import PageHeader from '@/components/ui/PageHeader'
import WindowPicker from '@/components/ui/WindowPicker'
import StatusBanner, { type BannerState } from '@/components/ui/StatusBanner'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Disclosure from '@/components/ui/Disclosure'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useDashboardSummary, useFailureCategories, useTrendData } from '@/hooks/useMetrics'
import { useRuns } from '@/hooks/useRuns'
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
import { dayTimeAgo } from '@/utils/formatters'
import { utcDayIso } from '@/utils/calendarDay'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { TrendPoint } from '@/types/metrics'
import type { DashboardMetricValue, DashboardSummary } from '@/types/analytics'
import type { TestRun } from '@/types/runs'

// Wave 2.6 (VIZ-408): the catalogue sections, in their own chunk, mounted on
// every render since the chart flags were retired (Phase D S1).
const OverviewCatalogue = lazyWithRetry(() => import('@/components/reports/catalogue/OverviewCatalogue'))

// `1` = last 24 hours. Label is rendered as "24h" (the only sub-day option);
// all other values render as `${d}d`. The shared `WindowPicker`'s default set.
const TIME_OPTIONS = [1, 7, 14, 30, 90] as const

/** The help drawer's topic for this page (the **?** beside the title). */
const HELP_TOPIC = helpTopicParam('/overview')

/**
 * The secondary sections under the charts, in `?tab=` (UX redesign P3): the
 * catalogue's breakdown row (top failing tests + failure categories) and the
 * recent-activity feed. Top failing is the default and the clean URL. A tab
 * that is not open is not rendered, so it asks for nothing.
 */
type HomeTab = 'top-failing' | 'activity'
const HOME_TABS: readonly TabItem<HomeTab>[] = [
  { id: 'top-failing', label: 'Top failing' },
  { id: 'activity', label: 'Activity' },
]
const HOME_TAB_IDS: readonly HomeTab[] = HOME_TABS.map((t) => t.id)
const HOME_TAB_LABEL: Record<HomeTab, string> = { 'top-failing': 'Top failing', activity: 'Activity' }

// 5-state verdict layered over the backend's 4-band pass-rate classification
// (red/orange/yellow/green) plus the legacy "no data → PENDING" sentinel.
// ``WATCH`` is the yellow band — pass-rate is healthy but inside the project's
// caution zone (e.g. 95-99%). Still GO, just flagged for review.
type Verdict = 'GO' | 'WATCH' | 'CONDITIONAL' | 'NO_GO' | 'PENDING'

/**
 * The verdict as the page template's one-line banner (UX redesign P3): its
 * pill, and the words after it. WATCH is a GO (the pill says so) whose words
 * flag the caution zone.
 */
const VERDICT_BANNER: Record<Verdict, { state: BannerState; headline: string; meterTone: GaugeTone }> = {
  GO: { state: 'go', headline: 'ship cleared', meterTone: 'good' },
  WATCH: { state: 'go', headline: 'go with watch', meterTone: 'watch' },
  CONDITIONAL: { state: 'conditional', headline: 'review before shipping', meterTone: 'warn' },
  NO_GO: { state: 'no_go', headline: 'ship blocked by unresolved failures', meterTone: 'bad' },
  PENDING: { state: 'pending', headline: 'awaiting evidence', meterTone: 'neutral' },
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

const windowWords = (days: number) => (days === 1 ? 'the last 24 hours' : `the last ${days} days`)
const windowShort = (days: number) => (days === 1 ? '24h' : `${days}d`)

// ── KPI tiles ────────────────────────────────────────────────────────────
type SparkTone = 'good' | 'warn' | 'bad' | 'neutral'

/** A pass rate's line runs on the whole percentage scale. */
const PASS_RATE_DOMAIN = [0, 100] as const
const formatSparkPercent = (value: number) => `${value.toFixed(1)}%`

function metricNumber(m: DashboardMetricValue | undefined): number {
  if (!m) return 0
  if (typeof m.value === 'number') return m.value
  const parsed = Number(m.value)
  return Number.isFinite(parsed) ? parsed : 0
}

/**
 * A summary metric as a compact `MetricCard`'s value and change line.
 *
 * `trend` is a RELATIVE PERCENTAGE change vs the previous period —
 * ((cur - prev) / prev) * 100 in metrics_service — for every metric that flows
 * through here. Rendered bare it put "+400" next to a value of "150", which
 * reads as four hundred more executions when it means the count quadrupled:
 * the card's change line carries the unit and the baseline ("Up 400% vs prev
 * period"), the direction as a word. An unchanged metric reads "No change vs
 * prev period", never a bare "0" beside a count. A metric the API sent no
 * direction for has no change line at all — never a "no change" nobody
 * measured.
 */
function kpiMetric(display: string, m: DashboardMetricValue | undefined) {
  const dir = m?.trend_direction
  if (!m || dir == null) return { value: display }
  if (dir === 'flat' || m.trend == null) return { value: display, trend_direction: dir, trend_text: 'vs prev period' }
  return { value: display, trend: m.trend, trend_direction: dir }
}

/**
 * A KPI tile that opens the page behind its number. `ScopedLink`, not a bare
 * Link: the tile can be a CROSS-project aggregate while the destination shows
 * one project at a time. The tile still links (the destination offers a
 * picker), and says so under the tile before the click.
 */
function KpiLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <ScopedLink
      to={to}
      containerClassName="min-w-0"
      className="block h-full w-full min-w-0 rounded-xl transition-shadow hover:ring-1 hover:ring-[var(--color-border-light)]"
    >
      {children}
    </ScopedLink>
  )
}

// ── Trend window (the catalogue's day series and the KPI sparklines) ────
/**
 * The catalogue's headline chart height, mirrored here so its loading row
 * holds the same box without importing the lazy chunk
 * (`OVERVIEW_HEADLINE_HEIGHT` in `OverviewCatalogue.tsx`).
 */
const TREND_HEIGHT = 260
/** The breakdown row's plot height, mirrored the same way (`OVERVIEW_BREAKDOWN_HEIGHT`). */
const BREAKDOWN_HEIGHT = 280

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

/**
 * A catalogue row while its chunk loads: the row's two cards, holding their
 * height (the headline row's 1.6 : 1 columns, or the breakdown row's halves).
 */
function CataloguePending({ row }: { row: 'headline' | 'breakdown' }) {
  const columns = row === 'headline' ? 'xl:[grid-template-columns:minmax(0,1.6fr)_minmax(0,1fr)]' : 'xl:grid-cols-2'
  const height = (row === 'headline' ? TREND_HEIGHT : BREAKDOWN_HEIGHT) + 110
  return (
    <div className={`grid grid-cols-1 ${columns} gap-4`} aria-busy="true">
      {[0, 1].map((slot) => (
        <div key={slot} className="card flex items-center justify-center" style={{ minHeight: height }}>
          <LoadingSpinner />
        </div>
      ))}
    </div>
  )
}

// ── How the verdict is decided (below the primary content) ─────────────
/**
 * What the old verdict card said around its headline: the reasoning sentence,
 * the pass-rate meter with the population it is computed over (F-067), and
 * where the verdict comes from. The banner above carries the verdict itself;
 * this is the detail behind it, collapsed below the charts (UX redesign P3).
 *
 * ``newFailures24h`` is ``new_failures_24h`` — a FIXED 24-hour count computed
 * in ``metrics_service`` as ``status == FAILED AND created_at >= now - 24h``.
 * It deliberately ignores the page's window selector, so every word about it
 * says 24 h and nothing else (never "in the window", never "since the last
 * green run": no such baseline is part of the computation).
 */
function VerdictDetails({
  verdict, newFailures24h, totalExecutions, windowDays, passRatePct, passRateBasis,
}: {
  verdict: Verdict
  newFailures24h: number
  totalExecutions: number
  windowDays: number
  passRatePct: number
  passRateBasis: string
}) {
  const lede = verdict === 'PENDING' ? (
    <>No test executions in {windowWords(windowDays)} — readiness will assess once data lands in <code className="font-mono text-[12px]">release</code>.</>
  ) : verdict === 'NO_GO' ? (
    newFailures24h > 0 ? (
      <>{newFailures24h} new failure{newFailures24h === 1 ? '' : 's'} in the last 24 h on a run that completed with failures still open. Resolve the failures or override before merging to <code className="font-mono text-[12px]">release</code>.</>
    ) : (
      <>No new failures in the last 24 h; existing failures still require resolution or an approved override before merging to <code className="font-mono text-[12px]">release</code>.</>
    )
  ) : verdict === 'CONDITIONAL' ? (
    <>Some quality criteria need attention. Verify the warning evidence before merging to <code className="font-mono text-[12px]">release</code>.</>
  ) : (
    <>All quality gates passed across {totalExecutions} test execution{totalExecutions === 1 ? '' : 's'} in {windowWords(windowDays)}. Safe to merge to <code className="font-mono text-[12px]">release</code>.</>
  )
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between" data-testid="verdict-details">
      <div className="min-w-0 space-y-2">
        <p className="m-0 max-w-[72ch] text-[13px] text-[var(--color-text-secondary)]">{lede}</p>
        <p className="m-0 flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
          <Clock className="h-3.5 w-3.5" aria-hidden />
          <span>
            Verdict {totalExecutions > 0 ? `for ${windowWords(windowDays)}` : `awaiting data · ${windowWords(windowDays)}`}, from the{' '}
            <span className="font-medium text-[var(--color-text-secondary)]">release-readiness band</span>
          </span>
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-start gap-1 md:items-end">
        <div className="text-[11px] font-medium uppercase text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
          Pass rate
        </div>
        {/* PENDING has no pass rate to draw: an empty track, never a bar at 0. */}
        <GaugeBar
          value={verdict === 'PENDING' ? null : passRatePct}
          label="Pass rate"
          size="sm"
          width={140}
          track="tint"
          tone={VERDICT_BANNER[verdict].meterTone}
          format={(v) => `${v}%`}
        />
        {verdict !== 'PENDING' && (
          <div className="text-[11px] tabular-nums text-[var(--color-text-muted)]" data-testid="verdict-pass-rate-basis">
            {passRatePct}% · {passRateBasis}
          </div>
        )}
      </div>
    </div>
  )
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

/** The newest run's suite(s), as words: "Checkout", or "Checkout +2". */
function suiteWords(run: TestRun | undefined): string | null {
  if (!run) return null
  const all = (run.suite_names ?? []).filter(Boolean)
  const label = run.primary_suite_name ?? all[0] ?? null
  if (!label) return null
  const extra = all.filter((s) => s !== label).length
  return extra > 0 ? `${label} +${extra}` : label
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function OverviewPage() {
  // The window is a global user-level preference (shared with Runs / Trends
  // / Coverage / Failures / Live / Summary / My Failures), set through the
  // shared `WindowPicker` in the header and snapped to this page's options.
  const storedDays = useTimeWindowStore(s => s.days)
  const setDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, TIME_OPTIONS)
  // The open secondary tab (`?tab=`; Top failing is the clean URL).
  const [tab, setTab] = useTabParam(HOME_TAB_IDS, 'top-failing')
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
  // The catalogue's Failure categories chart draws the page's own
  // failure-categories read (one request, one SWR entry); its
  // `error`/`isValidating` are passed to it below.
  const categoriesRead = useFailureCategories(days, suiteFilter)
  const failureCategories = categoriesRead.data
  const mutateCategories = categoriesRead.mutate
  const retryCategories = useCallback(() => {
    void mutateCategories()
  }, [mutateCategories])
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

  const verdict = mapReadinessToVerdict(summary?.release_readiness_band, summary?.release_readiness, totalExecutions)
  // Real, weighted pass rate for the window — clamped for the meter width.
  const passRatePct = Math.min(100, Math.max(0, Math.round(passRate)))
  // F-067: name the POPULATION, not just "weighted". /overview counts every
  // execution and the Summary Report counts each distinct test once — 81.0%
  // vs 83.3% on the same window. Both are right; showing which is which is
  // what stops them reading as a contradiction. Falls back to the old copy
  // when the API omits it (a cached pre-#588 payload).
  const passRateBasis = `${summary?.avg_pass_rate_7d?.basis_label || 'weighted'} · ${windowShort(days)}`
  const lastRunLabel = trendData.length > 0
    ? dayTimeAgo(trendData[trendData.length - 1].date)
    : '—'
  const latestSuite = suiteWords(latestRun)
  const subtitle = [scopeLabel, `Last run ${lastRunLabel}`, latestSuite && `latest suite ${latestSuite}`]
    .filter(Boolean)
    .join(' · ')

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
  // The guide REPLACES the page (below), so it also needs a summary with
  // nothing in it: executions the summary did count are never hidden behind
  // a welcome, whatever the run list says.
  const showFirstRunGuide = isFreshInstall && !guideDismissed && totalExecutions <= 0

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

  // The slot beside a tile's value: its trend line, or — with fewer than two
  // measured days — why there is none. The words explain the missing LINE,
  // never deny the number beside them: they used to read "no failures
  // recorded" beside a 23, "awaiting runs" beside 60 executions, and "need >=
  // 2 runs" for a project with six (the shortfall is days of history, not
  // runs). A count line needs two days that HAD runs: the zeros filled in for
  // quiet days are real, but a line of them around one day of data is not a
  // trend.
  const spark = (
    label: string,
    series: readonly (number | null)[],
    measuredDays: number,
    tone: SparkTone,
    opts: { domain?: readonly [number, number]; format?: (v: number) => string } = {},
  ): ReactNode => {
    if (measuredDays >= 2) {
      const measured = series.filter((v): v is number => v !== null && Number.isFinite(v))
      return (
        <Sparkline
          series={series}
          label={`${label} per day, last ${days} days`}
          tone={tone}
          // A count line runs from 0 to the series' top; a zoomed [min, max]
          // would turn a 94-97% week into a cliff.
          domain={opts.domain ?? [0, Math.max(1, ...measured)]}
          format={opts.format}
          area
          className="w-24"
        />
      )
    }
    const hint = measuredDays === 0 ? `${windowShort(days)} · no executions recorded` : `${measuredDays} of ${days} days has data · no trend line`
    return (
      <span data-spark-hint="" title={hint} className="block max-w-[9.5rem] text-right text-[11px] leading-tight text-[var(--color-text-faint)]">
        {hint}
      </span>
    )
  }

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

  // An empty project shows only the getting-started guide under the title:
  // a zeroed verdict, five zero tiles and four empty charts say nothing the
  // guide does not. Dismissed, the page below explains the empty window.
  if (showFirstRunGuide) {
    return (
      <div className="space-y-4">
        <PageHeader compact title="Dashboard" subtitle={scopeLabel} helpTopic={HELP_TOPIC} />
        <FirstRunGuide
          projectName={project?.name}
          projectId={project?.id}
          onDismiss={() => {
            dismissFirstRunGuide(activeProjectId)
            bumpGuideDismissed((t) => t + 1)
          }}
        />
      </div>
    )
  }

  const kpiLoading = summaryLoading && !summary
  const banner = VERDICT_BANNER[verdict]
  // What both catalogue mounts (the headline row above, the breakdown row in
  // its tab) are given: the page's window, day series, failure-categories
  // read, scope and existence answer.
  const catalogueProps = {
    days,
    window: trendDays,
    trendsLoading,
    categories: {
      data: failureCategories,
      error: categoriesRead.error,
      isValidating: categoriesRead.isValidating,
      retry: retryCategories,
    },
    suiteFilter,
    filtersApplied: trendFiltered,
    everHadRun: newestRunLoaded ? everHadRun : null,
  }

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Dashboard"
        subtitle={subtitle}
        helpTopic={HELP_TOPIC}
        actions={
          <>
            <label className="inline-flex items-center gap-2 text-[13px] text-[var(--color-text-muted)]">
              <span>Suite</span>
              <select
                value={selectedSuite}
                onChange={(event) => setSelectedSuite(event.target.value)}
                className="h-8 min-w-[180px] rounded-md border bg-[var(--color-bg-secondary)] px-2 text-[13px] text-[var(--color-text)]"
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
            <WindowPicker options={TIME_OPTIONS} />
            {viewsMenu && <SavedViewsMenu {...viewsMenu} variant="ghost" />}
          </>
        }
      />

      {/* Why the dashboard is empty. Rendered ONLY when the window really is
          empty: a zeroed KPI row with no explanation is indistinguishable
          from a broken page, which is exactly how a 7-day window over
          16-day-old data was read as an outage. */}
      {!summaryLoading && emptyWindow.kind !== 'has-data' && (
        <div
          className="card flex flex-wrap items-center gap-x-3 gap-y-1.5 !py-2.5 px-4"
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

      {/* The release-readiness verdict, one line (UX redesign P3): the pill,
          the pass rate and the population it is over, the fixed 24-hour new
          failures, the sample, and the way to the failures. It absorbed the
          old "What's blocking release" panel: that panel's facts were the
          24-hour count, the verdict's words, and this link. The reasoning and
          the meter sit in "How this verdict is decided" below the charts. */}
      {kpiLoading ? (
        <div className="card flex min-h-11 items-center justify-center !py-2">
          <LoadingSpinner size="sm" />
        </div>
      ) : (
        <SectionErrorBoundary message="Failed to load release readiness">
          <section aria-label="Release readiness">
            <StatusBanner
              state={banner.state}
              title={banner.headline}
              facts={[
                {
                  label: 'Pass rate',
                  value: verdict === 'PENDING' ? '—' : (
                    <>
                      {passRatePct}%{' '}
                      <span className="font-normal text-[var(--color-text-muted)]">{passRateBasis}</span>
                    </>
                  ),
                },
                { label: 'New failures · 24h', value: newFailures },
                {
                  label: 'Sample',
                  value: `${totalExecutions} execution${totalExecutions === 1 ? '' : 's'} · ${windowShort(days)}`,
                },
              ]}
              action={{ label: 'Open failures', href: '/failures' }}
            />
          </section>
        </SectionErrorBoundary>
      )}

      {/* The five dashboard KPIs (UX redesign P3: 7 → 5). Infra-caused
          failures and Avg run duration left the strip: the AI kind triad is
          on Failures (its kind chips), and run durations are on Runs and
          Reports › Summary. Eng-hours saved lives on Reports › Value. */}
      <KpiStrip>
        <KpiLink to="/runs">
          <MetricCard
            compact
            icon={null}
            title="Total executions"
            loading={kpiLoading}
            metric={kpiMetric(`${totalExecutions}`, summary?.total_executions_7d)}
            sparkline={spark('Total executions', totalSeries, daysWithData, 'neutral')}
          />
        </KpiLink>
        <MetricCard
          compact
          icon={null}
          title="Avg pass rate"
          loading={kpiLoading}
          metric={kpiMetric(`${Math.round(passRate)}%`, summary?.avg_pass_rate_7d)}
          sparkline={spark('Avg pass rate', passRateSeries, measuredPassDays, passRate >= 90 ? 'good' : passRate >= 70 ? 'warn' : 'bad', {
            domain: PASS_RATE_DOMAIN,
            format: formatSparkPercent,
          })}
        />
        <KpiLink to="/failures">
          <MetricCard
            compact
            icon={null}
            title="New failures · 24h"
            positiveDirection="down"
            loading={kpiLoading}
            metric={kpiMetric(`${newFailures}`, summary?.new_failures_24h)}
            sparkline={spark('New failures · 24h', failedSeries, daysWithData, newFailures === 0 ? 'good' : 'bad')}
          />
        </KpiLink>
        <KpiLink to="/flaky">
          <MetricCard
            compact
            icon={null}
            title="Flaky tests"
            positiveDirection="down"
            loading={kpiLoading}
            metric={kpiMetric(`${flaky}`, summary?.flaky_test_count)}
          />
        </KpiLink>
        <KpiLink to="/defects">
          <MetricCard
            compact
            icon={null}
            title="Active defects"
            positiveDirection="down"
            loading={kpiLoading}
            metric={kpiMetric(`${activeDefects}`, summary?.active_defects)}
          />
        </KpiLink>
      </KpiStrip>

      {/* PRIMARY CONTENT (VIZ-408): the catalogue's headline row — the
          pass-rate trend (which replaced the Execution trend card, OD-4)
          beside the status donut. Mounted unconditionally since the chart
          flags were retired. */}
      <div data-primary="">
        {/* The outline's level 2 for the charts' level-3 titles (axe
            heading-order): the verdict card's h2 used to be it. */}
        <h2 className="sr-only">Pass rate and status</h2>
        <SectionErrorBoundary message="Failed to load charts">
          <Suspense fallback={<CataloguePending row="headline" />}>
            <OverviewCatalogue rows="headline" {...catalogueProps} />
          </Suspense>
        </SectionErrorBoundary>
      </div>

      {/* The secondary sections, one tab open at a time (`?tab=`). A closed
          tab is not rendered: the catalogue's lazy row (and its top-failing
          request) exists only in Top failing, and the activity feed is asked
          for only once Activity is opened. */}
      <div data-home-sections="">
        <Tabs items={HOME_TABS} value={tab} onChange={setTab} ariaLabel="Dashboard sections" />
        <div role="tabpanel" aria-label={HOME_TAB_LABEL[tab]} data-tab-panel={tab} className="pt-3 min-w-0">
          {tab === 'top-failing' ? (
            // The catalogue's lazy row: Top failing tests beside Failure
            // categories (the page's own failure-categories read).
            <SectionErrorBoundary message="Failed to load charts">
              <Suspense fallback={<CataloguePending row="breakdown" />}>
                <OverviewCatalogue rows="breakdown" {...catalogueProps} />
              </Suspense>
            </SectionErrorBoundary>
          ) : (
            // Recent activity (epic ACT). Inside its own error boundary and on
            // its own SWR key: the panel must never be able to take /overview
            // down or hold up its first paint.
            <SectionErrorBoundary message="Failed to load recent activity">
              <RecentActivityPanel days={days} />
            </SectionErrorBoundary>
          )}
        </div>
      </div>

      {!kpiLoading && (
        <Disclosure
          title="How this verdict is decided"
          summary={verdict === 'PENDING' ? 'awaiting data' : `pass rate ${passRatePct}% · ${passRateBasis}`}
          persistKey="overview.verdict-details"
        >
          <VerdictDetails
            verdict={verdict}
            newFailures24h={newFailures}
            totalExecutions={totalExecutions}
            windowDays={days}
            passRatePct={passRatePct}
            passRateBasis={passRateBasis}
          />
        </Disclosure>
      )}
    </div>
  )
}
