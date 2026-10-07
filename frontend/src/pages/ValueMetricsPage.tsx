import { useMemo, useState, type ReactNode } from 'react'
import { Download, HelpCircle, Save, Timer } from 'lucide-react'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import WindowPicker from '@/components/ui/WindowPicker'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Disclosure from '@/components/ui/Disclosure'
import { helpTopicParam } from '@/components/help/helpTopics'
import StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import { readyState } from '@/components/charts/chartState'
import { buildStackedColumnModel, type StackedColumnSeriesInput } from '@/components/charts/stackedColumnModel'
import { formatNumber } from '@/utils/formatters'
import { valueMetricsService } from '@/services/valueMetricsService'
import { refreshValueMetrics, useValueMethodology, useValueMetrics } from '@/hooks/useValueMetrics'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import {
  ASSUMPTION_MAX,
  isValidAssumptionMinutes,
  type AssumptionsWrite,
  type ValueAssumptions,
  type ValueMetricsMonthly,
} from '@/types/valueMetrics'

// ── Hours-saved model (US-12.1) ─────────────────────────────────────────────

/** Compact hours formatting: 1 decimal under 100h, whole hours above. */
function fmtHours(h: number): string {
  return String(h >= 100 ? Math.round(h) : Math.round(h * 10) / 10)
}

function fmtFte(f: number): string {
  return f > 0 && f < 0.1 ? f.toFixed(2) : f.toFixed(1)
}

/** "2026-07-01" → "2026-07". */
function fmtMonth(month: string): string {
  return month.slice(0, 7)
}

/** Defensive list coercion — backend is built in parallel (pinned contract). */
function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
}

/**
 * Methodology panel — "credibility requires showing the math" (US-12.1 AC).
 * Fetched lazily from GET /api/v1/value-metrics/methodology on first open.
 */
function MethodologyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { methodology, isLoading, isError } = useValueMethodology(open)
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="How hours saved is calculated"
    >
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden />
      <div className="relative card w-full max-w-2xl max-h-[80vh] overflow-y-auto p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[var(--color-text)]">How is this calculated?</h2>
            {methodology && (
              <p className="text-xs text-[var(--color-text-muted)]">Methodology version {methodology.version}</p>
            )}
          </div>
          <button type="button" onClick={onClose} className="btn-secondary text-xs">Close</button>
        </div>

        {isLoading && <div className="flex justify-center py-6"><LoadingSpinner /></div>}
        {isError && (
          <p className="text-sm text-[var(--status-failed)]">Failed to load the methodology.</p>
        )}

        {methodology && (
          <>
            {methodology.legs.map((leg) => {
              const inputs = asStringList(leg.inputs)
              const caveats = asStringList(leg.caveats)
              return (
                <section key={leg.key} className="rounded-md border border-[var(--color-border)] p-3 space-y-1.5">
                  <h3 className="text-sm font-semibold text-[var(--color-text)]">{leg.title}</h3>
                  <code className="block text-xs bg-[var(--color-bg-secondary)] rounded p-2 whitespace-pre-wrap text-[var(--color-text-secondary)]">
                    {leg.formula}
                  </code>
                  {inputs.length > 0 && (
                    <p className="text-xs text-[var(--color-text-muted)]">
                      <strong className="text-[var(--color-text-secondary)]">Inputs:</strong> {inputs.join(' · ')}
                    </p>
                  )}
                  {caveats.length > 0 && (
                    <ul className="text-xs text-[var(--color-text-muted)] list-disc list-inside space-y-0.5">
                      {caveats.map((c) => <li key={c}>{c}</li>)}
                    </ul>
                  )}
                </section>
              )
            })}

            <section className="space-y-1">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">Default assumptions</h3>
              <p className="text-xs text-[var(--color-text-muted)]">
                Triage {methodology.defaults.triage_minutes_per_failure} min/failure
                {' · '}blocked-run wait {methodology.defaults.blocked_run_wait_minutes} min
                {' · '}defect filing {methodology.defaults.defect_filing_minutes} min
              </p>
            </section>

            {asStringList(methodology.research_notes).length > 0 && (
              <section className="space-y-1">
                <h3 className="text-sm font-semibold text-[var(--color-text)]">Research notes</h3>
                <ul className="text-xs text-[var(--color-text-muted)] list-disc list-inside space-y-0.5">
                  {asStringList(methodology.research_notes).map((n) => <li key={n}>{n}</li>)}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Assumptions editor (QA_LEAD+) ───────────────────────────────────────────

type AssumptionField = keyof ValueAssumptions

const ASSUMPTION_FIELDS: { key: AssumptionField; label: string; help: string }[] = [
  {
    key: 'triage_minutes_per_failure',
    label: 'Triage minutes per failure',
    help: 'Average engineer minutes to triage one net-new failure without auto-triage.',
  },
  {
    key: 'blocked_run_wait_minutes',
    label: 'Blocked-run wait minutes',
    help: 'Average minutes a pipeline stays blocked on a known-flaky failure before quarantine absorbs it.',
  },
  {
    key: 'defect_filing_minutes',
    label: 'Defect filing minutes',
    help: 'Average minutes to write up and file one duplicate defect by hand.',
  },
]

/**
 * The per-project minutes behind the hours-saved math, inside the page's
 * "Model assumptions" disclosure (UX redesign P3): the disclosure carries the
 * heading and the Customized / Defaults badge.
 */
function AssumptionsEditor({ projectId, assumptions, onSaved }: {
  projectId: string
  assumptions: ValueAssumptions
  /** Revalidates the value-metrics data (headline, monthly, source badge). */
  onSaved: () => Promise<unknown>
}) {
  const toDrafts = (a: ValueAssumptions): Record<AssumptionField, string> => ({
    triage_minutes_per_failure: String(a.triage_minutes_per_failure),
    blocked_run_wait_minutes: String(a.blocked_run_wait_minutes),
    defect_filing_minutes: String(a.defect_filing_minutes),
  })

  const [drafts, setDrafts] = useState<Record<AssumptionField, string>>(() => toDrafts(assumptions))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<AssumptionField, string>>>({})

  // Seed-during-render reset (same pattern as GitLabIntegrationPage): when a
  // refetch hands us a new assumptions reference and there are no unsaved
  // edits, re-seed the drafts from it.
  const [seeded, setSeeded] = useState<ValueAssumptions>(assumptions)
  if (assumptions !== seeded && !dirty) {
    setSeeded(assumptions)
    setDrafts(toDrafts(assumptions))
    setFieldErrors({})
  }

  function edit(key: AssumptionField, value: string) {
    setDrafts((d) => ({ ...d, [key]: value }))
    setDirty(true)
  }

  async function save() {
    // Client-side mirror of the backend bounds (0 < x <= 480); PUT sends a
    // partial payload containing only the changed fields.
    const errors: Partial<Record<AssumptionField, string>> = {}
    const patch: AssumptionsWrite = {}
    for (const { key } of ASSUMPTION_FIELDS) {
      const raw = drafts[key].trim()
      const parsed = Number(raw)
      if (raw === '' || !Number.isFinite(parsed)) {
        errors[key] = 'Enter a number of minutes'
        continue
      }
      if (parsed === seeded[key]) continue
      if (!isValidAssumptionMinutes(parsed)) {
        errors[key] = `Must be greater than 0 and at most ${ASSUMPTION_MAX} minutes`
        continue
      }
      patch[key] = parsed
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      // Local (non-axios) toast — axios errors are toasted by the shared
      // interceptor, but this never reaches the network.
      toast.error('Fix the highlighted assumption values')
      return
    }
    setFieldErrors({})
    if (Object.keys(patch).length === 0) {
      setDirty(false)
      return
    }
    setSaving(true)
    try {
      await valueMetricsService.putAssumptions(projectId, patch, assumptions)
      toast.success('Assumptions saved')
      setDirty(false)
      // Revalidate the value-metrics data: the headline/monthly hours and
      // the source badge all recompute server-side from the new assumptions.
      await onSaved()
    } catch (err) {
      // Axios failures already toast via the shared interceptor (#433 lesson).
      if (!isAxiosError(err)) toast.error(`Failed to save assumptions: ${(err as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3" data-testid="assumptions-editor">
      <p className="text-xs text-[var(--color-text-muted)]">
        Per-project minutes behind the hours-saved math. Bounds: greater than 0, at most {ASSUMPTION_MAX} minutes.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {ASSUMPTION_FIELDS.map(({ key, label, help }) => (
          <label key={key} className="text-xs block">
            <span className="text-[var(--color-text-muted)]">{label}</span>
            <input
              type="number"
              inputMode="decimal"
              min={1}
              max={ASSUMPTION_MAX}
              value={drafts[key]}
              onChange={(e) => edit(key, e.target.value)}
              aria-label={label}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
            />
            {fieldErrors[key] ? (
              <span className="mt-1 block text-[var(--status-failed)]">{fieldErrors[key]}</span>
            ) : (
              <span className="mt-1 block text-[var(--color-text-faint)]">{help}</span>
            )}
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn-primary text-xs flex items-center gap-1.5"
        >
          <Save className="h-3 w-3" /> {saving ? 'Saving…' : 'Save assumptions'}
        </button>
        {dirty && <span className="text-[11px] text-[var(--color-text-faint)]">Unsaved changes</span>}
      </div>
    </div>
  )
}

// ── Monthly hours chart ─────────────────────────────────────────────────────

/**
 * The three model legs, bottom of the stack first. They are NOT statuses, so
 * the kit gives them series colours 1-3 and a decal each (`--chart-series-*`);
 * the accent / skipped / flaky status colours they used to borrow said
 * "status" about something that is not one.
 */
const HOURS_SERIES: StackedColumnSeriesInput[] = [
  { key: 'hours_triage', label: 'Triage' },
  { key: 'hours_quarantine', label: 'Quarantine' },
  { key: 'hours_dedup', label: 'Duplicate absorption' },
]

const formatHours = (value: number) => `${formatNumber(value, { maximumFractionDigits: 1 })} h`

/** A leg the payload does not carry is not measured: a gap, never 0 hours. */
const hoursOf = (value: unknown) => (typeof value === 'number' ? value : null)

/**
 * Estimated hours saved per month by model leg, on the chart kit's stacked
 * columns (VIZ-104). The heading and the methodology line are the frame's
 * title and takeaway; `monthly-chart` is the frame's test id.
 */
function MonthlyHoursChart({ monthly, methodologyVersion }: { monthly: ValueMetricsMonthly[]; methodologyVersion: number }) {
  const model = useMemo(
    () =>
      buildStackedColumnModel({
        buckets: monthly.map((m) => ({
          key: m.month,
          label: fmtMonth(m.month),
          values: {
            hours_triage: hoursOf(m.hours_triage),
            hours_quarantine: hoursOf(m.hours_quarantine),
            hours_dedup: hoursOf(m.hours_dedup),
          },
        })),
        series: HOURS_SERIES,
        valueTitle: 'Hours saved',
        bucketTitle: 'Month',
        format: formatHours,
        xType: 'time',
      }),
    [monthly],
  )
  return (
    <StackedColumnChartFrame
      data-testid="monthly-chart"
      title="Hours saved per month"
      takeaway={`Estimated engineering hours returned each month, split by model leg (methodology v${methodologyVersion}).`}
      headingLevel={3}
      state={readyState(monthly)}
      model={model}
      height={260}
      bucketNoun="month"
    />
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

/**
 * Value Metrics' windows: no 24h, and a year (the shared `WindowPicker`'s
 * `options`). The global window is snapped to the nearest of these, and
 * picking one here propagates to pages that may snap to a different nearest.
 */
const VALUE_OPTIONS = [7, 30, 90, 365] as const

/** The help drawer's topic for this page (the **?** beside the title). */
const HELP_TOPIC = helpTopicParam('/value-metrics')

/**
 * A counter as a compact card's value: "—" instead of a stark "0", so an empty
 * counter reads as "no data yet" rather than "shipped exactly zero of these".
 */
const counter = (value: number) => (value === 0 ? '—' : formatNumber(value))

/**
 * The words beside a counter's value (the compact card's inline slot): the
 * facts that qualify it, e.g. "64 tests grouped · 4 promoted".
 */
function CounterNote({ children }: { children: ReactNode }) {
  return (
    <span data-counter-note="" className="block max-w-[11rem] text-right text-[11px] leading-tight text-[var(--color-text-muted)]">
      {children}
    </span>
  )
}

export default function ValueMetricsPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined)
  const { canAccessManagement } = usePermissions()
  // The global shared window, snapped to this page's options (the header's
  // `WindowPicker` writes the snapped value back).
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, VALUE_OPTIONS)

  const { metrics, error, isLoading, refresh } = useValueMetrics(projectId, days)
  const [showMethodology, setShowMethodology] = useState(false)

  const header = (
    <PageHeader
      compact
      title="Value Metrics"
      helpTopic={HELP_TOPIC}
      actions={
        <>
          <WindowPicker options={VALUE_OPTIONS} />
          <a
            href={valueMetricsService.exportUrl(projectId, days)}
            className="btn-secondary !py-1 text-sm flex items-center gap-2"
            download
          >
            <Download className="h-4 w-4" /> Export
          </a>
        </>
      }
    />
  )

  if (isLoading) {
    return (
      <div className="space-y-4">
        {header}
        <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
      </div>
    )
  }
  if (error) {
    return <DataUnavailable error={error} onRetry={() => void refresh()} testId="value-metrics-data-unavailable" />
  }
  if (!metrics) return null

  // Hours-saved model (US-12.1). Defensive against the parallel-built
  // backend: only treat the model as available when the headline actually
  // arrived alongside available=true.
  const hoursSavedAvailable = metrics.available === true && metrics.headline != null
  const monthly = hoursSavedAvailable && Array.isArray(metrics.monthly) ? metrics.monthly : []
  const monthlyTotals = monthly.reduce(
    (acc, m) => ({
      auto_triaged: acc.auto_triaged + m.auto_triaged,
      clustered_failures: acc.clustered_failures + m.clustered_failures,
      duplicates_absorbed: acc.duplicates_absorbed + m.duplicates_absorbed,
      quarantine_suppressed_failures: acc.quarantine_suppressed_failures + m.quarantine_suppressed_failures,
      runs_unblocked_proxy: acc.runs_unblocked_proxy + m.runs_unblocked_proxy,
    }),
    {
      auto_triaged: 0,
      clustered_failures: 0,
      duplicates_absorbed: 0,
      quarantine_suppressed_failures: 0,
      runs_unblocked_proxy: 0,
    },
  )

  // Detect the "barely any signal" state: nothing has driven a measurable
  // outcome other than (maybe) AI intelligence reports. This is the most
  // common confusing state on a fresh project — the hero shows time saved
  // but every counter is empty, making it look like a data bug. The note
  // below says what each pipeline needs to fire so the user can act.
  const nonIntelSignalsAllZero = (
    metrics.defects_auto_grouped === 0 &&
    metrics.duplicate_tickets_avoided === 0 &&
    metrics.defects_promoted === 0 &&
    metrics.flaky_tests_identified === 0 &&
    metrics.risky_releases_blocked === 0 &&
    metrics.releases_conditional === 0 &&
    metrics.release_overrides === 0
  )
  const showAssumptions = canAccessManagement && Boolean(projectId) && Boolean(metrics.assumptions)

  return (
    <div className="space-y-4">
      {header}

      {/* ── Hero: hours saved (US-12.1), one line ─────────────────────────── */}
      {hoursSavedAvailable ? (
        <section
          aria-label="Engineering hours saved"
          data-value-hero=""
          className="card flex flex-wrap items-center gap-x-4 gap-y-1 !py-2.5"
          style={{ borderColor: 'color-mix(in srgb, var(--color-accent) 35%, var(--color-border))' }}
        >
          <span className="inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            <Timer className="h-4 w-4" aria-hidden /> Eng-hours saved · last 30 days
          </span>
          {/* The headline number itself links to the math (AC: credibility
              requires showing the math). */}
          <button
            type="button"
            onClick={() => setShowMethodology(true)}
            title="How is this calculated?"
            className="group text-left"
          >
            <span className="text-2xl font-bold tabular-nums text-[var(--color-text)] group-hover:underline decoration-dotted underline-offset-4">
              {fmtHours(metrics.headline.hours_saved_30d)}h
            </span>
          </button>
          <span className="text-sm text-[var(--color-text-secondary)]">
            ≈ {fmtFte(metrics.headline.fte_equivalent_30d)} FTE
          </span>
          <button
            type="button"
            onClick={() => setShowMethodology(true)}
            className="text-sm text-[var(--color-accent)] hover:underline inline-flex items-center gap-1"
          >
            <HelpCircle className="h-3.5 w-3.5" aria-hidden /> How is this calculated?
          </button>
          {/* The API reports hours saved for a fixed trailing 30 days only
              (`hours_saved_30d`); the window picker above does not reach it. */}
          <span className="ml-auto text-xs text-[var(--color-text-faint)]" data-testid="hours-saved-window-note">
            Fixed 30-day figure: the window picker does not change it.
          </span>
        </section>
      ) : (
        // Honest empty state: no estimate rather than a misleading "0 hours".
        <section
          aria-label="Engineering hours saved"
          data-value-hero=""
          className="rounded-xl border px-4 py-3 text-sm"
          style={{
            background: 'var(--status-skipped-bg)',
            borderColor: 'var(--status-skipped-bd)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <p className="font-semibold text-[var(--color-text)]">
            Not enough data yet to estimate engineering hours saved.
          </p>
          {metrics.insufficient_data_reason && (
            <p className="text-[12.5px] leading-relaxed">{metrics.insufficient_data_reason}</p>
          )}
          <p className="text-[12.5px] leading-relaxed text-[var(--color-text-muted)]">
            The model only reports once it has real triage, quarantine, and duplicate-absorption signal — an
            honest blank beats a made-up zero.
          </p>
        </section>
      )}

      {/* ── The value counters, one strip (UX redesign P3: seven cards → five).
          "Defects promoted" is the second fact of "Defects grouped", and the
          derived "Release decisions" (blocked + conditional, both shown on
          "Releases blocked") is gone. The labels are short enough to stay
          whole at 1280 px (`kpi-labels.spec.ts`); the full names are hints. */}
      <KpiStrip>
        <MetricCard
          compact
          icon={null}
          title="Defects grouped"
          hint="Defects auto-grouped"
          metric={{ value: counter(metrics.defects_auto_grouped) }}
          sparkline={<CounterNote>{metrics.tests_grouped} tests · {metrics.defects_promoted} promoted to Jira</CounterNote>}
        />
        <MetricCard
          compact
          icon={null}
          title="Duplicates avoided"
          metric={{ value: counter(metrics.duplicate_tickets_avoided) }}
          sparkline={<CounterNote>tickets prevented</CounterNote>}
        />
        <MetricCard
          compact
          icon={null}
          title="Flaky tests found"
          metric={{ value: counter(metrics.flaky_tests_identified) }}
          sparkline={<CounterNote>{metrics.quarantine_recommended} recommended for quarantine</CounterNote>}
        />
        <MetricCard
          compact
          icon={null}
          title="Releases blocked"
          hint="Risky releases blocked"
          metric={{ value: counter(metrics.risky_releases_blocked) }}
          sparkline={<CounterNote>{metrics.releases_conditional} conditional · {metrics.release_overrides} overridden</CounterNote>}
        />
        <MetricCard
          compact
          icon={null}
          title="AI reports"
          hint="Intelligence reports"
          metric={{ value: counter(metrics.intelligence_reports_generated) }}
          sparkline={<CounterNote>AI analyses generated</CounterNote>}
        />
      </KpiStrip>

      {/* ── PRIMARY CONTENT: estimated hours saved per month ──────────────── */}
      {hoursSavedAvailable && monthly.length > 0 && (
        <div data-primary="">
          {/* The outline's level 2 for the chart's level-3 title (axe heading-order). */}
          <h2 className="sr-only">Hours saved by month</h2>
          <MonthlyHoursChart monthly={monthly} methodologyVersion={metrics.methodology_version} />
        </div>
      )}

      {/* The model's own counts behind the chart, summed over its months. */}
      {hoursSavedAvailable && monthly.length > 0 && (
        <Disclosure
          title="Model breakdown"
          summary={`last ${monthly.length} month${monthly.length === 1 ? '' : 's'}`}
          persistKey="value-metrics.model-breakdown"
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" data-testid="model-breakdown">
            <MetricCard compact icon={null} title="Auto-triaged failures" metric={{ value: counter(monthlyTotals.auto_triaged) }} />
            <MetricCard compact icon={null} title="Clustered failures" metric={{ value: counter(monthlyTotals.clustered_failures) }} />
            <MetricCard compact icon={null} title="Duplicate failures absorbed" metric={{ value: counter(monthlyTotals.duplicates_absorbed) }} />
            <MetricCard
              compact
              icon={null}
              title="Quarantine-suppressed failures"
              metric={{ value: counter(monthlyTotals.quarantine_suppressed_failures) }}
            />
            <div title="Proxy estimate — derived from quarantine suppressions and the blocked-run wait assumption, not directly measured.">
              <MetricCard
                compact
                icon={null}
                title="Runs unblocked (estimate)"
                metric={{ value: counter(monthlyTotals.runs_unblocked_proxy), trend_direction: 'none', trend_text: 'Proxy estimate' }}
              />
            </div>
          </div>
        </Disclosure>
      )}

      {/* Assumptions editor — QA_LEAD+ and a concrete project only. */}
      {showAssumptions && projectId && metrics.assumptions && (
        <Disclosure
          title="Model assumptions"
          summary={metrics.assumptions_source === 'custom' ? 'Customized' : 'Defaults'}
          persistKey="value-metrics.assumptions"
        >
          <AssumptionsEditor
            projectId={projectId}
            assumptions={metrics.assumptions}
            onSaved={async () => {
              // Bound mutate revalidates this page's key; the global filter
              // sweep also refreshes any other cached value-metrics keys.
              await Promise.all([refresh(), refreshValueMetrics()])
            }}
          />
        </Disclosure>
      )}

      {nonIntelSignalsAllZero && (
        // The counters ARE accurate, but the project hasn't generated any of
        // the signals they track: said once, and what fills each one is a
        // click away, so nobody reads the dashes as a data bug.
        <Disclosure title="No value-generating activity yet for this window" summary="what fills these counters">
          <ul className="text-[12.5px] space-y-0.5 list-disc list-inside text-[var(--color-text-muted)]">
            <li><strong className="text-[var(--color-text-secondary)]">Defects grouped</strong> · run deep investigation on failing builds to cluster failures.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Duplicates avoided</strong> · file a defect from a cluster and the system detects duplicates of prior ones.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Flaky tests found</strong> · the Flaky Coach scans history; needs ≥ 5 runs per fingerprint to flag.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Releases blocked</strong> · publish a Release Gate Policy and run release-gate evaluation on a build.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Defects promoted</strong> · promote a cluster to a tracked defect from the Failure Analysis page.</li>
          </ul>
        </Disclosure>
      )}

      <MethodologyModal open={showMethodology} onClose={() => setShowMethodology(false)} />
    </div>
  )
}
