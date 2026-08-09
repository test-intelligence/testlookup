import { useState } from 'react'
import {
  BarChart3, Clock, Download, GitMerge, HelpCircle, Save, Shield, ShieldAlert, SlidersHorizontal,
  Sparkles, Bug, AlertTriangle, Timer,
} from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { clsx } from 'clsx'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildValueMetricsWorkflow } from '@/components/workflow/workflowPresets'
import { valueMetricsService } from '@/services/valueMetricsService'
import { refreshValueMetrics, useValueMethodology, useValueMetrics } from '@/hooks/useValueMetrics'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import {
  ASSUMPTION_MAX,
  isValidAssumptionMinutes,
  type AssumptionsSource,
  type AssumptionsWrite,
  type ValueAssumptions,
  type ValueMetricsMonthly,
} from '@/types/valueMetrics'

function MetricCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: string | number; sub?: string; color: string
}) {
  // Render "—" instead of a stark "0" so an empty metric reads as
  // "no data yet" rather than "shipped exactly zero of these." Same
  // muted tone as the rest of the card so it doesn't draw the eye.
  const isZero = typeof value === 'number' ? value === 0 : value === '0'
  const display = isZero ? '—' : value
  const valueColor = isZero ? 'text-[var(--color-text-faint)]' : color
  return (
    <div className="card space-y-1">
      <div className="flex items-center gap-2">
        <Icon className={clsx('h-4 w-4', isZero ? 'text-[var(--color-text-faint)]' : color)} />
        <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">{label}</span>
      </div>
      <p className={clsx('text-2xl font-bold tabular-nums', valueColor)}>{display}</p>
      {sub && <p className="text-xs text-[var(--color-text-faint)]">{sub}</p>}
    </div>
  )
}

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

function AssumptionsEditor({ projectId, assumptions, source, onSaved }: {
  projectId: string
  assumptions: ValueAssumptions
  source: AssumptionsSource
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
    <section className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-[var(--color-text-muted)]" />
        <h3 className="text-sm font-semibold text-[var(--color-text)]">Model assumptions</h3>
        <span
          className={clsx(
            'ml-auto text-[10px] px-2 py-0.5 rounded border',
            source === 'custom'
              ? 'border-[var(--color-accent)] text-[var(--color-accent-ink)]'
              : 'border-[var(--color-border)] text-[var(--color-text-muted)]',
          )}
        >
          {source === 'custom' ? 'Customized' : 'Defaults'}
        </span>
      </div>
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
    </section>
  )
}

// ── Monthly hours chart ─────────────────────────────────────────────────────

// Series colors come from theme tokens (palette ratchet: no new hex).
const HOURS_SERIES = [
  { key: 'hours_triage', name: 'Triage', color: 'var(--color-accent)' },
  { key: 'hours_quarantine', name: 'Quarantine', color: 'var(--status-skipped)' },
  { key: 'hours_dedup', name: 'Duplicate absorption', color: 'var(--status-flaky)' },
] as const

function MonthlyHoursChart({ monthly }: { monthly: ValueMetricsMonthly[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={monthly} margin={{ top: 4, right: 4, left: -8, bottom: 0 }} barSize={28}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonth}
          axisLine={false}
          tickLine={false}
          tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
          dy={6}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
        />
        <Tooltip
          labelFormatter={(label) => fmtMonth(String(label))}
          formatter={(value) => `${value ?? 0} h`}
          contentStyle={{
            background: 'var(--color-bg-card)',
            border: '1px solid var(--color-border)',
            borderRadius: 8,
            color: 'var(--color-text)',
            fontSize: 12,
          }}
        />
        <Legend iconType="circle" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
        {HOURS_SERIES.map(({ key, name, color }, i) => (
          <Bar
            key={key}
            dataKey={key}
            stackId="hours"
            name={name}
            fill={color}
            radius={i === HOURS_SERIES.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function ValueMetricsPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const project = useProjectStore(s => s.activeProject)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined)
  const { canAccessManagement } = usePermissions()
  // Global shared time window — Value Metrics' options diverge from
  // most other pages (no 24h, includes 1y), so we snap the shared value
  // to the nearest supported option here. Picking a value here also
  // propagates to other pages that may snap to a different nearest.
  const VALUE_OPTIONS = [7, 30, 90, 365] as const
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, VALUE_OPTIONS)
  const setDays = setStoredDays

  const { metrics, isLoading, refresh } = useValueMetrics(projectId, days)
  const [showMethodology, setShowMethodology] = useState(false)

  if (isLoading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (!metrics) return null
  const workflow = buildValueMetricsWorkflow(metrics, days, project?.name ?? 'All Projects')

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
  // but every card is zero, making it look like a data bug. Banner below
  // explains what each pipeline needs to fire so the user can act.
  const nonIntelSignalsAllZero = (
    metrics.defects_auto_grouped === 0 &&
    metrics.duplicate_tickets_avoided === 0 &&
    metrics.defects_promoted === 0 &&
    metrics.flaky_tests_identified === 0 &&
    metrics.risky_releases_blocked === 0 &&
    metrics.releases_conditional === 0 &&
    metrics.release_overrides === 0
  )
  // If even the hero number is essentially "from N intel reports * 20min",
  // qualify it so users don't think it's an aggregate of everything.
  const heroIsFromIntelOnly = nonIntelSignalsAllZero && metrics.intelligence_reports_generated > 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Value Metrics"
        subtitle="Operational value delivered by AI-powered test intelligence"
        actions={
          <div className="flex items-center gap-3">
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value))}
              className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>Last year</option>
            </select>
            <a
              href={valueMetricsService.exportUrl(projectId, days)}
              className="btn-secondary text-sm flex items-center gap-2"
              download
            >
              <Download className="h-4 w-4" /> Export
            </a>
          </div>
        }
      />

      {/* ── Hours-saved headline (US-12.1) ───────────────────────────────── */}
      {hoursSavedAvailable ? (
        <div className="card border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/10 p-6">
          <div className="flex items-center gap-3 mb-2">
            <Timer className="h-6 w-6 text-[var(--color-text)]" />
            <p className="text-sm text-[var(--color-text-muted)] uppercase tracking-wider">
              Eng-Hours Saved · Last 30 Days
            </p>
          </div>
          {/* The headline number itself links to the math (AC: credibility
              requires showing the math). */}
          <button
            type="button"
            onClick={() => setShowMethodology(true)}
            title="How is this calculated?"
            className="group text-left"
          >
            <p className="text-4xl font-black text-[var(--color-text-secondary)] tabular-nums group-hover:underline decoration-dotted underline-offset-8">
              {fmtHours(metrics.headline.hours_saved_30d)}h
            </p>
          </button>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            ≈ {fmtFte(metrics.headline.fte_equivalent_30d)} FTE over the last 30 days
            {' · '}
            <button
              type="button"
              onClick={() => setShowMethodology(true)}
              className="text-[var(--color-accent)] hover:underline inline-flex items-center gap-1 align-baseline"
            >
              <HelpCircle className="h-3.5 w-3.5" /> How is this calculated?
            </button>
          </p>
        </div>
      ) : (
        // Honest empty state: no estimate rather than a misleading "0 hours".
        <div
          className="rounded-xl border p-5 text-sm"
          style={{
            background: 'var(--status-skipped-bg)',
            borderColor: 'var(--status-skipped-bd)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <p className="font-semibold text-[var(--color-text)] mb-1">
            Not enough data yet to estimate engineering hours saved.
          </p>
          {metrics.insufficient_data_reason && (
            <p className="text-[12.5px] leading-relaxed">{metrics.insufficient_data_reason}</p>
          )}
          <p className="text-[12.5px] leading-relaxed text-[var(--color-text-muted)] mt-1">
            The hours-saved model only reports once it has real triage, quarantine, and
            duplicate-absorption signal — an honest blank beats a made-up zero.
          </p>
        </div>
      )}

      {/* Monthly trend + model counts */}
      {hoursSavedAvailable && monthly.length > 0 && (
        <div className="card p-4 space-y-2">
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Hours saved per month</h3>
            <p className="text-xs text-[var(--color-text-muted)]">
              Estimated engineering hours returned each month, split by model leg
              (methodology v{metrics.methodology_version}).
            </p>
          </div>
          <MonthlyHoursChart monthly={monthly} />
        </div>
      )}

      {hoursSavedAvailable && monthly.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
          <MetricCard
            icon={Sparkles}
            label="Auto-triaged failures"
            value={monthlyTotals.auto_triaged}
            sub={`Last ${monthly.length} months`}
            color="text-[var(--color-text)]"
          />
          <MetricCard
            icon={GitMerge}
            label="Clustered failures"
            value={monthlyTotals.clustered_failures}
            sub={`Last ${monthly.length} months`}
            color="text-[var(--color-text)]"
          />
          <MetricCard
            icon={AlertTriangle}
            label="Duplicate failures absorbed"
            value={monthlyTotals.duplicates_absorbed}
            sub={`Last ${monthly.length} months`}
            color="text-[var(--color-text)]"
          />
          <MetricCard
            icon={Shield}
            label="Quarantine-suppressed failures"
            value={monthlyTotals.quarantine_suppressed_failures}
            sub={`Last ${monthly.length} months`}
            color="text-[var(--color-text)]"
          />
          <div title="Proxy estimate — derived from quarantine suppressions and the blocked-run wait assumption, not directly measured.">
            <MetricCard
              icon={Clock}
              label="Runs unblocked (estimate)"
              value={monthlyTotals.runs_unblocked_proxy}
              sub={`Proxy · last ${monthly.length} months`}
              color="text-[var(--color-text)]"
            />
          </div>
        </div>
      )}

      {/* Assumptions editor — QA_LEAD+ and a concrete project only. */}
      {canAccessManagement && projectId && metrics.assumptions && (
        <AssumptionsEditor
          projectId={projectId}
          assumptions={metrics.assumptions}
          source={metrics.assumptions_source}
          onSaved={async () => {
            // Bound mutate revalidates this page's key; the global filter
            // sweep also refreshes any other cached value-metrics keys
            // (e.g. the Overview KPI card's 30d/6mo key).
            await Promise.all([refresh(), refreshValueMetrics()])
          }}
        />
      )}

      <WorkflowTimeline
        title="Value Realization Workflow"
        subtitle="Track how RunScope AI converts triage, clustering, and release protection into measurable value."
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
      />

      {/* Hero: triage time saved (legacy simple counter model) */}
      <div className="card border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/10 p-6">
        <div className="flex items-center gap-3 mb-2">
          <Clock className="h-6 w-6 text-[var(--color-text)]" />
          <p className="text-sm text-[var(--color-text-muted)] uppercase tracking-wider">Triage Time Saved</p>
        </div>
        <p className="text-4xl font-black text-[var(--color-text-secondary)] tabular-nums">
          {metrics.triage_time_saved_hours}h
        </p>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          {metrics.triage_time_saved_minutes} minutes saved over the last {days} days
          {heroIsFromIntelOnly && (
            <>
              {' '}
              <span className="text-[var(--color-text-faint)]">
                — entirely from {metrics.intelligence_reports_generated} intelligence report
                {metrics.intelligence_reports_generated === 1 ? '' : 's'}; no clustering,
                defect, flaky, or release-gate signal yet.
              </span>
            </>
          )}
        </p>
      </div>

      {nonIntelSignalsAllZero && (
        // Empty-state banner: data IS accurate but the project hasn't
        // generated any of the signals these cards track. Surface this
        // explicitly so users don't read the zeros as a data bug.
        <div
          className="rounded-xl border p-4 text-sm"
          style={{
            background: 'color-mix(in srgb, var(--status-broken) 6%, transparent)',
            borderColor: 'color-mix(in srgb, var(--status-broken) 30%, transparent)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <p className="font-semibold text-[var(--color-text)] mb-1">No value-generating activity yet for this window.</p>
          <p className="text-[12.5px] leading-relaxed">
            Each card below tracks a specific pipeline. They'll populate as those pipelines fire on your runs:
          </p>
          <ul className="text-[12.5px] mt-1.5 space-y-0.5 list-disc list-inside text-[var(--color-text-muted)]">
            <li><strong className="text-[var(--color-text-secondary)]">Defects auto-grouped</strong> · run deep investigation on failing builds to cluster failures.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Duplicates avoided</strong> · file a defect from a cluster and the system detects duplicates of prior ones.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Flaky tests found</strong> · the Flaky Coach scans history; needs ≥ 5 runs per fingerprint to flag.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Risky releases blocked</strong> · publish a Release Gate Policy and run release-gate evaluation on a build.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Defects promoted</strong> · promote a cluster to a tracked defect from the Failure Analysis page.</li>
          </ul>
        </div>
      )}

      {/* Metric cards grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
        <MetricCard
          icon={GitMerge}
          label="Defects Auto-Grouped"
          value={metrics.defects_auto_grouped}
          sub={`${metrics.tests_grouped} tests grouped into clusters`}
          color="text-[var(--status-flaky)]"
        />
        <MetricCard
          icon={AlertTriangle}
          label="Duplicates Avoided"
          value={metrics.duplicate_tickets_avoided}
          sub="Duplicate tickets prevented"
          color="text-[var(--status-broken)]"
        />
        <MetricCard
          icon={Sparkles}
          label="Defects Promoted"
          value={metrics.defects_promoted}
          sub="Cluster → Jira defect"
          color="text-[var(--status-passed)]"
        />
        <MetricCard
          icon={Bug}
          label="Flaky Tests Found"
          value={metrics.flaky_tests_identified}
          sub={`${metrics.quarantine_recommended} recommended for quarantine`}
          color="text-[var(--status-flaky)]"
        />
        <MetricCard
          icon={ShieldAlert}
          label="Risky Releases Blocked"
          value={metrics.risky_releases_blocked}
          sub={`${metrics.releases_conditional} conditional, ${metrics.release_overrides} overridden`}
          color="text-[var(--status-failed)]"
        />
        <MetricCard
          icon={BarChart3}
          label="Intelligence Reports"
          value={metrics.intelligence_reports_generated}
          sub="AI analysis reports generated"
          color="text-[var(--color-text)]"
        />
        <MetricCard
          icon={Shield}
          label="Release Decisions"
          value={metrics.risky_releases_blocked + metrics.releases_conditional}
          sub="Automated go/no-go assessments"
          color="text-[var(--status-broken)]"
        />
      </div>

      <MethodologyModal open={showMethodology} onClose={() => setShowMethodology(false)} />
    </div>
  )
}
