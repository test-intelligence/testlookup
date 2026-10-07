/**
 * Project Summary Report — consolidated view of every test suite for the
 * active project. Two aggregation modes (window / latest-per-suite) plus
 * PDF / Excel / HTML exports.
 *
 * The page template (UX redesign P3, `02-design-spec.md` §2, §5 "Reports ›
 * Summary"), top to bottom:
 *   Header  → title, Views (the one secondary action), and ⋯ with every
 *             export: PDF, Excel, each "in background", the HTML analysis
 *             reports (1d, 7d).
 *   Toolbar → the global WindowPicker (24h / 7d / 30d / 90d), the
 *             Aggregation toggle, the release-scope badge.
 *   KPIs    → ONE row of five compact MetricCards (the old six tiles and the
 *             counts strip merged: every count and rate is still on it), and
 *             the window's run facts on one line under it.
 *   Primary → the catalogue's results-by-suite bars and the per-suite table
 *             under it (`data-primary`). No status donut: the KPI row
 *             carries its counts.
 *   Below   → the pass-rate trend in a collapsed "Trend" disclosure (asked
 *             for only once opened), the top failing tests table, then the
 *             background exports.
 *
 * Data: ``/api/v1/reports/summary`` via ``useSummaryReport``.
 * Export: ``/api/v1/reports/summary/pdf`` via ``summaryReportService.downloadPdf``.
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import { BarChart3, Download, FileText, Layers, TriangleAlert } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import WindowPicker from '@/components/ui/WindowPicker'
import Disclosure from '@/components/ui/Disclosure'
import { helpTopicParam } from '@/components/help/helpTopics'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { useSummaryReport, useSummaryReportScope } from '@/hooks/useSummaryReport'
import { summaryReportService } from '@/services/summaryReportService'
import { validateEnvelopeMeta, type EnvelopeMeta } from '@/lib/viz/contracts'
import type { SummaryReport, SummaryReportMode, SummarySuiteRow } from '@/types/summaryReport'
import { flakyCriteriaSentence, flakySubtitle } from './summaryFlakyCriteria'
import SummaryCatalogueShell from '@/components/reports/catalogue/SummaryCatalogueShell'
import ReportExportsPanel from '@/components/reports/ReportExportsPanel'
import SavedViewsMenu from '@/components/reports/SavedViewsMenu'
import { useReportViewsMenu } from '@/components/reports/useReportViewsMenu'
import type { SavedView } from '@/services/savedViewsService'

// Wave 2.6 (VIZ-408): the catalogue sections, in their own chunk. One loader
// for the lazy component and the preload below, so both wait on the same
// request.
const loadSummaryCatalogue = () => import('@/components/reports/catalogue/SummaryCatalogue')
const SummaryCatalogue = lazyWithRetry(loadSummaryCatalogue)

/** The page's help topic (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/reports/summary')

/** The windows the report answers (the toolbar's WindowPicker offers these). */
const DAYS_OPTIONS = [1, 7, 30, 90] as const
/** Aggregation mode is page-local — different from the global window
 *  preference. Persisted here so the user's mode choice survives
 *  navigation without leaking into pages that don't have a mode. */
const LS_MODE_KEY = 'summary-report.mode'

/** Default aggregation mode. ``latest`` shows one snapshot per suite
 *  (the most recent run in the window), which matches user intent on
 *  "show me where things stand right now" — a fresh user landing on
 *  /reports/summary expects current state, not volume-weighted history.
 *  The volume-weighted ``window`` view is still available via the
 *  toggle, but isn't the default because it produces totals scaled by
 *  run count (5 runs × 100 tests = 500 total) which reads as duplicate
 *  rows to anyone who hasn't read the docstring. */
const DEFAULT_MODE: SummaryReportMode = 'latest'

function loadStoredMode(): SummaryReportMode {
  try {
    const raw = localStorage.getItem(LS_MODE_KEY)
    return raw === 'latest' || raw === 'window' ? raw : DEFAULT_MODE
  } catch {
    return DEFAULT_MODE
  }
}

const MODE_LABELS: Record<SummaryReportMode, { label: string; hint: string }> = {
  window: {
    label: 'All runs in window',
    hint: 'Unique tests across every run in the window — the same count the Coverage page shows. Pass rate is volume-weighted across executions.',
  },
  latest: {
    label: 'Latest run per suite',
    hint: 'Only each suite’s most recent run counts — expect fewer tests than the Coverage page, which spans the whole window.',
  },
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(1)}%`
}

function fmtInt(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString()
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function SummaryReportPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Window is a global preference — picking 24h here propagates to /live,
  // /coverage, /trends, /runs, /failures, /overview, /my-failures. The
  // toolbar's WindowPicker writes it; a value picked elsewhere (e.g. 14d on
  // Live) is snapped to the nearest option here, as the picker snaps it.
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, DAYS_OPTIONS)

  // Aggregation mode is page-local (no other page has the concept).
  const [mode, setMode] = useState<SummaryReportMode>(() => loadStoredMode())
  const [downloading, setDownloading] = useState<'pdf' | 'xlsx' | null>(null)
  // Bumped when an export is queued, so the exports panel re-reads at once.
  const [exportsToken, setExportsToken] = useState(0)
  // US-7.5: on-demand download of the self-contained HTML analysis report
  // (the same document daily/weekly digest emails attach).
  const [downloadingReport, setDownloadingReport] = useState<'1d' | '7d' | null>(null)
  useEffect(() => {
    try { localStorage.setItem(LS_MODE_KEY, mode) } catch { /* ignore */ }
  }, [mode])

  // P1: this page's saved views (the top-bar release, the window, the mode).
  // Before any early return; the menu itself shows in the report's header only.
  const viewExtra = useMemo(() => ({ summary: { mode } }), [mode])
  const applyViewExtra = useCallback((view: SavedView) => {
    const saved = view.filters?.summary
    const savedMode = saved && typeof saved === 'object' ? (saved as { mode?: unknown }).mode : undefined
    if (savedMode === 'latest' || savedMode === 'window') setMode(savedMode)
  }, [])
  const viewsMenu = useReportViewsMenu({
    route: '/reports/summary',
    windowDays: days,
    windowOptions: DAYS_OPTIONS,
    release: true,
    extraFilters: viewExtra,
    applyExtra: applyViewExtra,
  })

  // Start the sections' chunk (the page's first chart code) on mount, while
  // the report is still loading, instead of after the report has rendered. A
  // failure here is not reported: the lazy component asks again, and its
  // error boundary says so.
  useEffect(() => {
    loadSummaryCatalogue().catch(() => undefined)
  }, [])

  // ONE scope for the screen and the PDF: both requests are built from it, so
  // the exported sign-off document cannot cover different releases from the
  // report on screen.
  const scope = useSummaryReportScope({ days, mode })
  const { data, isLoading, error: reportError, mutate: retryReport } = useSummaryReport({ days, mode })

  const windowLabel = days === 1 ? '24h' : `${days}d`

  // VIZ-607: PDF (charts drawn by the server) or Excel (a sheet per part, native charts).
  // `inBackground`: the "… in background" items, which send even a small report
  // to the worker (the old "In background" checkbox, one menu item per format).
  const handleDownload = async (format: 'pdf' | 'xlsx', inBackground = false) => {
    if (!project?.id) {
      toast.error('Select a single project before exporting.')
      return
    }
    const label = format === 'pdf' ? 'PDF' : 'Excel workbook'
    setDownloading(format)
    try {
      const params = { project_id: project.id, ...scope }
      // VIZ-607: a large report (or one sent to the background) is rendered by
      // a worker; the exports panel below says when it is ready.
      const decision = await summaryReportService.requestExport({ ...params, format, background: inBackground })
      if (decision.delivery === 'background') {
        setExportsToken(t => t + 1)
        if (decision.dispatched === false) {
          toast.error('The export is queued, but the worker could not be reached. Retry it from Background exports.')
        } else {
          toast.success(
            `The ${label} is being generated in the background. It will appear under Background exports when it is ready.`,
          )
        }
        return
      }
      const blob = format === 'pdf'
        ? await summaryReportService.downloadPdf(params)
        : await summaryReportService.downloadXlsx(params)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const slug = (project.name || 'project').toLowerCase().replace(/\s+/g, '_')
      a.download = `summary-${slug}-${windowLabel}-${mode}.${format}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success(`Summary ${label} downloaded`)
    } catch (err: unknown) {
      toast.error((err as Error).message || `Failed to download the ${label}`)
    } finally {
      setDownloading(null)
    }
  }

  const handleDownloadAnalysisReport = async (window: '1d' | '7d') => {
    if (!project?.id) {
      toast.error('Select a single project before exporting.')
      return
    }
    setDownloadingReport(window)
    try {
      const blob = await summaryReportService.downloadAnalysisReport({
        project_id: project.id,
        window,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const slug = (project.name || 'project').toLowerCase().replace(/\s+/g, '-')
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
      a.download = `testlookup-report-${slug}-${stamp}${window === '7d' ? '-weekly' : ''}.html`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success('Analysis report downloaded')
    } catch (err: unknown) {
      toast.error((err as Error).message || 'Failed to download analysis report')
    } finally {
      setDownloadingReport(null)
    }
  }

  // The summary is project-scoped; all-projects mode is meaningless here.
  if (isAllProjects || !project) {
    return (
      <>
        <PageHeader
          compact
          title="Summary Report"
          subtitle="Consolidated pass / fail / skip / flaky across every test suite for the active project."
          helpTopic={HELP_TOPIC}
        />
        <EmptyState
          title="Pick a single project"
          description="The Summary Report aggregates a single project's test suites. Choose a project from the top bar to generate one."
          icon={<FileText className="h-6 w-6" />}
        />
      </>
    )
  }

  // M21: an outage used to render "No executions in this window".
  if (reportError && !data) {
    return (
      <>
        <PageHeader compact title="Summary Report" subtitle={`Project: ${project.name}`} helpTopic={HELP_TOPIC} />
        <DataUnavailable error={reportError} onRetry={() => void retryReport()} testId="summary-data-unavailable" />
      </>
    )
  }

  if (isLoading && !data) {
    return (
      <>
        <PageHeader compact title="Summary Report" subtitle={`Project: ${project.name}`} helpTopic={HELP_TOPIC} />
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      </>
    )
  }

  const totals = data?.totals
  const suites = data?.suites ?? []
  const topFailing = data?.top_failing_tests ?? []
  const hasData = totals != null && totals.total_test_cases > 0

  // Header ⋯ (P3): every export. The page's one secondary action is Views.
  const exportBusy = downloading !== null || !hasData
  const exportItems: OverflowItem[] = [
    {
      label: downloading === 'pdf' ? 'Generating PDF…' : 'Export PDF',
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void handleDownload('pdf'),
      disabled: exportBusy,
    },
    {
      label: downloading === 'xlsx' ? 'Generating Excel…' : 'Export Excel',
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void handleDownload('xlsx'),
      disabled: exportBusy,
    },
    // VIZ-607: generate the file on the server and say when it is ready (large reports always do).
    {
      label: 'Export PDF in background',
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void handleDownload('pdf', true),
      disabled: exportBusy,
    },
    {
      label: 'Export Excel in background',
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void handleDownload('xlsx', true),
      disabled: exportBusy,
    },
    // US-7.5: the self-contained HTML analysis report digest emails attach.
    ...(['1d', '7d'] as const).map((w): OverflowItem => ({
      label: downloadingReport === w ? `Generating analysis report (${w})…` : `Analysis report (${w})`,
      icon: <Download className="h-3.5 w-3.5" />,
      onClick: () => void handleDownloadAnalysisReport(w),
      disabled: downloadingReport !== null,
    })),
  ]

  return (
    <div className="space-y-4">
      {/* The subtitle is the window's run facts (the project is the top bar's;
          what each aggregation counts is the toggle's tooltip, §2). */}
      <PageHeader
        compact
        title="Summary Report"
        subtitle={data ? runFactsLine(data) : `Project: ${project.name}`}
        helpTopic={HELP_TOPIC}
        actions={viewsMenu ? <SavedViewsMenu {...viewsMenu} variant="ghost" /> : undefined}
        overflow={exportItems}
      />

      {/* One toolbar row: the global window, the aggregation, and the release
          scope the report covers. */}
      <div className="flex items-center gap-3 flex-wrap" data-summary-toolbar="">
        <WindowPicker options={DAYS_OPTIONS} />
        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
        <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] tracking-wider">Aggregation</span>
        <div role="radiogroup" aria-label="Aggregation mode" className="flex items-center gap-1.5">
          {(Object.keys(MODE_LABELS) as SummaryReportMode[]).map(m => {
            const active = mode === m
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setMode(m)}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
                style={{
                  background: active ? 'color-mix(in srgb, var(--status-flaky) 14%, transparent)' : 'transparent',
                  borderColor: active ? 'color-mix(in srgb, var(--status-flaky) 30%, transparent)' : 'var(--color-border)',
                  color: active ? '#d8b4fe' : 'var(--color-text-muted)',
                }}
                title={MODE_LABELS[m].hint}
              >
                {MODE_LABELS[m].label}
              </button>
            )
          })}
        </div>
        {/* The report's output LEAVES the tool (a PDF attached to a go/no-go
            thread), so the badge states the release scope the SERVER applied —
            `meta.scope.releases` — rather than what the client asked for. */}
        <SummaryScopeBadge meta={data?.meta} requestedReleaseId={scope.release_id ?? null} />
        {data?.generated_at && (
          <span className="ml-auto text-[11px] text-[var(--color-text-faint)]">
            Generated {fmtDateTime(data.generated_at)}
          </span>
        )}
      </div>

      {!hasData || totals == null || data == null ? (
        <EmptyState
          title="No executions in this window"
          description={`No test runs were recorded for ${project.name} in the last ${windowLabel}. Ingest a run or widen the window to populate this report.`}
          icon={<BarChart3 className="h-6 w-6" />}
        />
      ) : (
        <>
          <SummaryKpis report={data} />

          {/* The page's primary content: the catalogue's results-by-suite
              bars with the per-suite table under it. Until the section chunk
              arrives the page draws the frame's box itself, from this report
              and with no chart code, so the table never moves (R1-2) and the
              page's largest paint is not held for the charts.
              No status donut (UX redesign P3, §5): its four counts — passed,
              failed, broken, skipped, per unique test — are the KPI row's
              tiles right above, from the same `totals`. */}
          <div data-primary="">
            <SectionErrorBoundary message="Failed to load charts">
              <Suspense fallback={<SummaryCatalogueShell part="suites" report={data} days={days} mode={mode} />}>
                <SummaryCatalogue part="suites" report={data} days={days} mode={mode} />
              </Suspense>
            </SectionErrorBoundary>

            <section>
              <h2 className="text-[13px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] mb-2 flex items-center gap-2">
                <Layers className="h-3.5 w-3.5" /> Per-suite breakdown
                <span className="ml-1 text-[10px] font-normal text-[var(--color-text-faint)] normal-case tracking-normal">{suites.length} suite{suites.length === 1 ? '' : 's'}</span>
              </h2>
              <SuiteTable rows={suites} />
            </section>
          </div>

          {/* The pass-rate trend, collapsed (§5): the one chart with a request
              of its own (`/metrics/trends`), which it makes only once opened
              and near the reader (its own lazy section). It counts
              executions, not unique tests, and its caption says so. */}
          <Disclosure title="Trend" summary={`pass rate per day · ${windowLabel}`}>
            <SectionErrorBoundary message="Failed to load the trend">
              <Suspense fallback={<SummaryCatalogueShell part="trend" report={data} days={days} mode={mode} />}>
                <SummaryCatalogue part="trend" report={data} days={days} mode={mode} />
              </Suspense>
            </SectionErrorBoundary>
          </Disclosure>

          {/* Top failing tests. P3 deleted the "Failures by test" bars above
              this table (§5 "duplicate failing-tests chart"): they drew the
              top ten of these same rows; the table lists every one, with its
              suite, class and count. */}
          <section>
            <h2 className="text-[13px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] mb-2 flex items-center gap-2">
              <TriangleAlert className="h-3.5 w-3.5" /> Top failing tests
              <span className="ml-1 text-[10px] font-normal text-[var(--color-text-faint)] normal-case tracking-normal">{topFailing.length} test{topFailing.length === 1 ? '' : 's'}</span>
            </h2>
            {topFailing.length === 0 ? (
              <p className="text-[12.5px] text-[var(--color-text-muted)]">No failures in this window.</p>
            ) : (
              // `overflow-x-auto`, not `overflow-hidden` (VIZ-106): a narrow
              // viewport scrolls the table inside its card instead of cutting
              // off the Failures column.
              <div
                role="region"
                aria-label="Top failing tests table"
                // A scroller a keyboard cannot focus cannot be scrolled without a mouse (R2-6).
                tabIndex={0}
                className={TABLE_SCROLLER}
              >
                <table className="w-full text-sm">
                  <thead className="bg-[var(--color-bg-secondary)]/80">
                    <tr>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Suite</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Class</th>
                      <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Failures</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-border)]/60">
                    {topFailing.map((t, idx) => (
                      <tr key={`${t.test_name}-${idx}`} className="hover:bg-[var(--color-bg-secondary)]/40">
                        <td className="px-4 py-2.5 text-[var(--color-text)] font-medium text-xs truncate max-w-[420px]" title={t.test_name}>{t.test_name}</td>
                        <td className="px-4 py-2.5 text-[var(--color-text-muted)] text-xs">{t.suite_name ?? '—'}</td>
                        <td className="px-4 py-2.5 text-[var(--color-text-muted)] text-xs">{t.class_name ?? '—'}</td>
                        <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)] font-medium">{fmtInt(t.failures)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* VIZ-607: the reader's background exports (hidden while there are
          none). Below the report: a finished export also says so in a toast
          with its Download button. */}
      <ReportExportsPanel projectId={project.id} refreshToken={exportsToken} />
    </div>
  )
}

// ── small components ───────────────────────────────────────────────────────

const ALL_RELEASES_REASON =
  'This report and its PDF export cover the selected time window across all releases.'

const BADGE_CLASS =
  'inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border border-[var(--color-border)] text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]'

/**
 * States which releases this report — and therefore its PDF export, which is
 * requested with the same scope — actually covers.
 *
 * The source of truth is the response's `meta.scope.releases` (contract C2):
 * what the SERVER applied, not what the client asked for. Three cases:
 *
 *   - meta names releases → say so, by name. Names are untrusted text and are
 *     only ever rendered as React text children / a `title` attribute.
 *   - meta names none → the report is all-releases. `AllReleasesBadge` keeps its
 *     own semantics: it is silent when no release is selected (nothing to
 *     explain) and says "All releases" when one is selected but was not applied.
 *   - meta absent or unreadable (older backend, cached payload) → fall back to
 *     client state and claim only that a release was REQUESTED; an older server
 *     applied the release to part of the report only.
 */
function SummaryScopeBadge({
  meta,
  requestedReleaseId,
}: {
  meta: EnvelopeMeta | undefined
  /** The release the report is scoped to, if any. */
  requestedReleaseId: string | readonly string[] | null
}) {
  const checked = meta === undefined ? null : validateEnvelopeMeta(meta)
  const envelope = checked?.ok ? checked.value : null

  if (envelope) {
    const applied = envelope.scope.releases
    if (applied.length === 0) {
      const ignored = envelope.ignored_filters.find(f => f.dimension === 'release')
      return (
        <AllReleasesBadge
          reason={ignored ? `${ALL_RELEASES_REASON} ${ignored.reason}` : ALL_RELEASES_REASON}
        />
      )
    }
    const names = applied.map(r => (r.name.trim() ? r.name : r.id)).join(', ')
    const one = applied.length === 1
    return (
      <span
        data-testid="summary-scope-badge"
        className={BADGE_CLASS}
        title={`Filtered to ${one ? 'release' : 'releases'} ${names}. This report and its PDF export both cover only ${one ? 'this release' : 'these releases'} within the selected time window.`}
      >
        <Layers className="h-3 w-3 flex-shrink-0" />
        <span className="truncate max-w-[220px]">{`${one ? 'Release' : 'Releases'}: ${names}`}</span>
      </span>
    )
  }

  if (!requestedReleaseId) return null
  return (
    <span
      data-testid="summary-scope-badge"
      className={BADGE_CLASS}
      title="A release filter was requested, but this response does not say which releases the server applied — an older server may have applied it to only part of the report. The PDF export requests the same release."
    >
      <Layers className="h-3 w-3 flex-shrink-0" />
      Release scope unconfirmed
    </span>
  )
}

/**
 * The report's headline numbers in ONE row (UX redesign P3, §5 "one merged KPI
 * row"): the six tiles and the counts strip under them became five compact
 * MetricCards, and nothing was dropped — every rate keeps its count beside it:
 *
 *   Total tests  the tests, evaluated, and skipped (count and rate)
 *   Pass %       passed and the population (F-067: "per unique test" here,
 *                while /overview counts executions); the weighted rate beside
 *                the value
 *   Fail %, Broken %   the rate and the count
 *   Flaky        the count, the rate (or why it is 0), and the rule it applied
 *                (BUG-007) on hover
 *
 * The counts strip's footer (the window's run facts) is the header's subtitle
 * (`runFactsLine`); when the report was generated sits at the toolbar's end.
 */
function SummaryKpis({ report }: { report: SummaryReport }) {
  const t = report.totals
  // F-067: name the population. No basis if the API omits it (a cached pre-#778 payload).
  const basis = t.pass_rate_basis_label?.trim()
  const line = (text: string) => ({ trend_direction: 'none' as const, trend_text: text })
  return (
    <section aria-label="Summary KPIs">
      <KpiStrip>
        <MetricCard
          compact
          title="Total tests"
          icon={null}
          metric={{
            value: fmtInt(t.total_test_cases),
            ...line(`${fmtInt(t.evaluated)} evaluated · ${fmtInt(t.skipped)} skipped (${fmtPct(t.skip_rate_pct)})`),
          }}
        />
        <MetricCard
          compact
          title="Pass %"
          icon={null}
          metric={{ value: fmtPct(t.pass_rate_pct), ...line(`${fmtInt(t.passed)} passed${basis ? ` · ${basis}` : ''}`) }}
          sparkline={
            <span className="whitespace-nowrap text-[11px] text-[var(--color-text-secondary)]">
              weighted {fmtPct(t.weighted_pass_rate_pct)}
            </span>
          }
        />
        <MetricCard
          compact
          title="Fail %"
          icon={null}
          metric={{ value: fmtPct(t.fail_rate_pct), ...line(`${fmtInt(t.failed)} failed`) }}
        />
        <MetricCard
          compact
          title="Broken %"
          icon={null}
          metric={{ value: fmtPct(t.broken_rate_pct), ...line(`${fmtInt(t.broken)} broken`) }}
        />
        {/* The flaky rule (BUG-007) on hover: MetricCard itself takes no title. */}
        <div className="min-w-0" title={flakyCriteriaSentence(report.flaky_criteria)} data-summary-flaky="">
          <MetricCard
            compact
            title="Flaky"
            icon={null}
            metric={{
              value: fmtInt(report.flaky_test_count),
              ...line(flakySubtitle(report.flaky_rate_pct, report.flaky_criteria, report.flaky_test_count)),
            }}
          />
        </div>
      </KpiStrip>
    </section>
  )
}

/**
 * The window's run facts — the old counts strip's footer: runs in the window,
 * runs per day (window mode only: the server sends none for "latest"), average
 * duration, the latest run. The page header's subtitle carries them (P3): a
 * line of their own under the KPI row put the primary content past the fold.
 */
function runFactsLine(report: SummaryReport): string {
  return [
    `Runs in window: ${fmtInt(report.run_count)}`,
    report.runs_per_day != null ? `Avg / day: ${report.runs_per_day.toFixed(2)}` : null,
    `Avg duration: ${fmtInt(report.avg_duration_ms)} ms`,
    report.latest_run_at ? `Latest run: ${fmtDateTime(report.latest_run_at)}` : null,
  ].filter((part): part is string => part !== null).join(' · ')
}

/**
 * The two tables' sideways scroller (VIZ-106). It is a Tab stop (axe
 * `scrollable-region-focusable`, R2-6): at 375 px both tables are wider than
 * their card, and a keyboard reader could otherwise not scroll to the right
 * columns. The ring shows on keyboard focus only, so a click leaves no ring.
 */
const TABLE_SCROLLER =
  'rounded-xl border border-[var(--color-border)] overflow-x-auto focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

type SuiteSortKey = 'suite' | 'last_run'
type SuiteSortDir = 'asc' | 'desc'

function SuiteTable({ rows }: { rows: SummarySuiteRow[] }) {
  const storedDays = useTimeWindowStore(s => s.days)
  // Default per request: most-recent activity at the top. The user
  // typically wants "what ran last" before "what's biggest", so the
  // initial order is ``last_run DESC``; clicking the Suite header
  // switches to alphabetical, with a toggle for asc/desc.
  const [sortKey, setSortKey] = useState<SuiteSortKey>('last_run')
  const [sortDir, setSortDir] = useState<SuiteSortDir>('desc')

  function handleSort(key: SuiteSortKey) {
    if (sortKey === key) {
      // Same column twice → flip direction.
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      // Suite alpha defaults to ASC (A→Z); date defaults to DESC.
      setSortDir(key === 'suite' ? 'asc' : 'desc')
    }
  }

  const sorted = useMemo(() => {
    const mult = sortDir === 'desc' ? -1 : 1
    const copy = [...rows]
    if (sortKey === 'suite') {
      copy.sort((a, b) => a.suite_name.localeCompare(b.suite_name) * mult)
    } else {
      // last_run sort. Treat missing timestamps as the epoch — they
      // sink to the bottom under DESC, top under ASC.
      copy.sort((a, b) => {
        const ta = a.last_run_at ? new Date(a.last_run_at).getTime() : 0
        const tb = b.last_run_at ? new Date(b.last_run_at).getTime() : 0
        return (ta - tb) * mult
      })
    }
    return copy
  }, [rows, sortKey, sortDir])

  // Step success-rate is a Phase 5 enrichment that may be absent (no
  // captured step data). Only surface the column when at least one suite
  // carries it, so legacy/no-step projects keep the original layout.
  const hasStepData = useMemo(
    () => rows.some(r => r.step_success_rate != null),
    [rows],
  )

  if (sorted.length === 0) {
    return <p className="text-[12.5px] text-[var(--color-text-muted)]">No suites with executions in this window.</p>
  }
  const arrow = (k: SuiteSortKey) => sortKey === k ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''
  return (
    // `overflow-x-auto`, not `overflow-hidden` (VIZ-106): on a narrow viewport
    // the columns scroll inside the card; they used to be cut off. Focusable
    // and named, so a keyboard can reach the scroll too (R2-6).
    <div role="region" aria-label="Per-suite breakdown table" tabIndex={0} className={TABLE_SCROLLER}>
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-secondary)]/80">
          <tr>
            <th
              className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text)]"
              onClick={() => handleSort('suite')}
              aria-sort={sortKey === 'suite' ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              title="Sort by suite name"
            >
              Suite{arrow('suite')}
            </th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Total</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Pass</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Fail</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Skip</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Broken</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Pass %</th>
            {hasStepData && (
              <th
                className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider"
                title="Share of captured test steps that passed (where step data exists)"
              >
                Step %
              </th>
            )}
            <th
              className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text)]"
              onClick={() => handleSort('last_run')}
              aria-sort={sortKey === 'last_run' ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              title="Sort by last run timestamp"
            >
              Last run{arrow('last_run')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]/60">
          {sorted.map(s => (
            <tr key={s.suite_name} className="hover:bg-[var(--color-bg-secondary)]/40">
              <td className="px-4 py-2.5 text-xs truncate max-w-[260px]" title={s.suite_name}>
                <Link
                  to={`/coverage/suite?name=${encodeURIComponent(s.suite_name)}&days=${storedDays}`}
                  className="font-medium text-[var(--color-accent)] hover:underline"
                >
                  {s.suite_name}
                </Link>
              </td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums">{fmtInt(s.total)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-passed)]">{fmtInt(s.passed)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)]">{fmtInt(s.failed)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-broken)]">{fmtInt(s.skipped)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)]">{fmtInt(s.broken)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums">
                <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium', passPctClass(s.pass_rate_pct))}>{fmtPct(s.pass_rate_pct)}</span>
              </td>
              {hasStepData && (
                <td
                  className="px-4 py-2.5 text-right text-[11px] text-[var(--color-text-muted)] tabular-nums"
                  title={
                    s.total_steps != null
                      ? `${fmtInt(s.passed_steps)} / ${fmtInt(s.total_steps)} steps passed`
                      : undefined
                  }
                >
                  {s.step_success_rate != null ? fmtPct(s.step_success_rate) : '—'}
                </td>
              )}
              <td className="px-4 py-2.5 text-right text-[11px] text-[var(--color-text-muted)]">{fmtDateTime(s.last_run_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function passPctClass(pct: number): string {
  if (pct >= 95) return 'bg-[var(--status-passed-bg)]/30 text-[var(--status-passed)]'
  if (pct >= 80) return 'bg-[var(--status-broken-bg)]/30 text-[var(--status-broken)]'
  return 'bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)]'
}
