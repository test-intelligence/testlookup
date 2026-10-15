import { useState } from 'react'
import { DollarSign, Save, ShieldAlert } from 'lucide-react'
import toast from 'react-hot-toast'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { useBillingOverview, useProjectQuota } from '@/hooks/useLlmBudget'
import {
  llmBudgetService,
  type BillingOverviewProject,
  type LlmQuotaWrite,
  type QuotaCapAction,
  type UsageStatus,
} from '@/services/llmBudgetService'
import { usePermissions } from '@/hooks/usePermissions'
import { formatDayIso, shiftDayIso } from '@/utils/calendarDay'
import { helpTopicParam } from '@/components/help/helpTopics'

const HELP_TOPIC = helpTopicParam('/settings/billing')

/**
 * Workspace billing overview — Tier 1 item 2.
 *
 * Enterprise QA leads use this page to see the LLM spend of every project
 * they have access to, identify projects approaching their caps, and (for
 * ADMIN) configure per-project quota and at-cap behaviour. Non-admin users
 * see the overview table but the edit form is read-only.
 *
 * The page is gated by the ``llm_cost_budget`` feature flag server-side —
 * endpoints still respond but usage/quota values are zero/null until the
 * flag is enabled. Because the flag only changes metering behaviour the
 * UI itself can render unconditionally.
 *
 * W04 AIC-06: "Included per month" and "Overage rate" now compute something.
 * The server returns Covered = min(spend, included), Overage = spend above
 * it, Charge = overage × rate and a linear month-end projection; the table
 * shows them (`—` where the server computed nothing: no enabled quota, or no
 * included allowance). Display only — the hard cap is what limits spend.
 */
export default function BillingPage() {
  const { isAdmin } = usePermissions()
  const { overview, error, isLoading, isError, refresh } = useBillingOverview()
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)

  if (isLoading) return <LoadingSpinner size="lg" />
  if (isError || !overview) {
    return <DataUnavailable error={error} onRetry={() => void refresh()} testId="billing-data-unavailable" />
  }

  const projections = overview.projects
    .map((p) => p.projected_overage_charge_usd)
    .filter((v): v is number => v != null)
  const projectedCharge = projections.length > 0 ? projections.reduce((sum, v) => sum + v, 0) : null
  const elapsed = elapsedOfPeriod(overview.period_start, overview.period_end)

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="LLM Cost Budget"
        helpTopic={HELP_TOPIC}
        actions={<ExperimentalBadge />}
        subtitle={`Current period: ${formatDate(overview.period_start)} — ${formatPeriodEnd(overview.period_end)}`}
      />

      {/* Workspace tiles */}
      <div className="grid grid-cols-4 gap-3">
        <SummaryTile
          icon={<DollarSign className="h-4 w-4" />}
          label="Total spend (period)"
          value={formatSpend(overview.total_cost_usd)}
        />
        <SummaryTile
          label="LLM calls"
          value={overview.total_llm_calls.toLocaleString()}
        />
        <SummaryTile
          icon={<ShieldAlert className="h-4 w-4 text-[var(--status-broken)]" />}
          label="Projects over soft-warn"
          value={overview.projects
            .filter((p) => p.status === 'SOFT_WARN' || p.status === 'CAPPED')
            .length.toString()}
          tone={
            overview.projects.some((p) => p.status === 'CAPPED')
              ? 'error'
              : overview.projects.some((p) => p.status === 'SOFT_WARN')
              ? 'warn'
              : 'neutral'
          }
        />
        <SummaryTile
          label="Projected overage charge"
          value={formatUsd(projectedCharge)}
          testId="billing-projected-charge"
        />
      </div>

      {/* Project table */}
      {/* Scrolls rather than clips: with the overage columns a wide row (a projection line,
          a long project name) pushed Status, Cap hits and Edit out of an overflow-hidden box. */}
      <div data-billing-table="" className="overflow-x-auto rounded-md border border-[var(--color-border)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
            <tr>
              <th className="text-left px-3 py-2">Project</th>
              <th className="text-right px-3 py-2">Spend</th>
              <th className="text-right px-3 py-2" title="Spend up to the included amount">Covered</th>
              <th className="text-right px-3 py-2" title="Spend above the included amount">Overage</th>
              <th className="text-right px-3 py-2" title="Overage × overage rate">Charge</th>
              <th className="text-right px-3 py-2">Cap</th>
              <th className="text-right px-3 py-2">Utilization</th>
              <th className="text-center px-3 py-2">Status</th>
              <th className="text-right px-3 py-2">Cap hits</th>
              <th className="text-right px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {overview.projects.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-8 text-center text-[var(--color-text-muted)]">
                  No accessible projects in this period.
                </td>
              </tr>
            )}
            {overview.projects.map((row) => (
              <ProjectRow
                key={row.project_id}
                row={row}
                elapsed={elapsed}
                onEdit={() => setSelectedProjectId(row.project_id)}
                canEdit={isAdmin}
              />
            ))}
          </tbody>
        </table>
      </div>

      {selectedProjectId && (
        <QuotaEditor
          projectId={selectedProjectId}
          onClose={() => setSelectedProjectId(null)}
          onSaved={async () => {
            await refresh()
            setSelectedProjectId(null)
          }}
          canEdit={isAdmin}
        />
      )}
    </div>
  )
}

function SummaryTile({
  icon,
  label,
  value,
  tone = 'neutral',
  testId,
}: {
  icon?: React.ReactNode
  label: string
  value: string
  tone?: 'neutral' | 'warn' | 'error'
  testId?: string
}) {
  const borderClass =
    tone === 'error'
      ? 'border-[var(--status-failed-bd)]/40'
      : tone === 'warn'
      ? 'border-[var(--status-broken-bd)]/40'
      : 'border-[var(--color-border)]'
  return (
    <div data-testid={testId} className={`rounded-md border ${borderClass} bg-[var(--color-bg-card)] p-3`}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
        {icon}
        {label}
      </div>
      <div className="text-lg font-semibold text-[var(--color-text)] mt-1">{value}</div>
    </div>
  )
}

/**
 * Spend at a precision that does not hide it: 2 decimals from $1 up, 4 below
 * (LLM spend is often a fraction of a cent: $0.0012 must not read "$0.00").
 */
function formatSpend(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value === 0) return '$0.00'
  return `$${value.toFixed(Math.abs(value) >= 1 ? 2 : 4)}`
}

/** Covered / Overage / Charge / projections: dollars and cents; `—` when the server computed nothing. */
function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `$${value.toFixed(2)}`
}

/** "17.5 of 30 days": how much of the billing period has passed (the projection's basis). */
function elapsedOfPeriod(startIso: string, endIso: string, now = Date.now()): string | null {
  const start = Date.parse(startIso)
  const end = Date.parse(endIso)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
  const day = 86_400_000
  const total = Math.round((end - start) / day)
  const elapsed = Math.min(Math.max(now - start, 0), end - start) / day
  return `${Math.round(elapsed * 10) / 10} of ${total} days`
}

/** The projection's tooltip, from the server's basis. */
function projectionTitle(basis: string | null | undefined, elapsed: string | null): string {
  if (basis === 'linear_elapsed_days') {
    return elapsed ? `linear projection from ${elapsed} of the period` : 'linear projection from the elapsed part of the period'
  }
  return basis ? `projection basis: ${basis}` : 'projection'
}

function ProjectRow({
  row,
  elapsed,
  onEdit,
  canEdit,
}: {
  row: BillingOverviewProject
  elapsed: string | null
  onEdit: () => void
  canEdit: boolean
}) {
  return (
    <tr data-billing-project={row.project_id} className="border-t border-[var(--color-border)]">
      <td className="px-3 py-2 text-[var(--color-text)]">{row.project_name}</td>
      <td data-billing-spend="" className="px-3 py-2 text-right font-mono text-[var(--color-text)]">
        {formatSpend(row.current_cost_usd)}
      </td>
      <td data-billing-covered="" className="px-3 py-2 text-right font-mono text-[var(--color-text-secondary)]">
        {formatUsd(row.covered_usd)}
      </td>
      <td data-billing-overage="" className="px-3 py-2 text-right font-mono text-[var(--color-text-secondary)]">
        {formatUsd(row.overage_usd)}
      </td>
      <td data-billing-charge="" className="px-3 py-2 text-right font-mono text-[var(--color-text)]">
        <div>{formatUsd(row.overage_charge_usd)}</div>
        {row.projected_overage_charge_usd != null && (
          <div
            data-billing-projection=""
            title={projectionTitle(row.projection_basis, elapsed)}
            className="text-[10px] font-sans text-[var(--color-text-muted)]"
          >
            {/* Two unbreakable halves, so the Charge column stays as narrow as "proj. $47.14". */}
            <span className="whitespace-nowrap">proj. {formatUsd(row.projected_overage_charge_usd)}</span>{' '}
            <span className="whitespace-nowrap">by month end</span>
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right text-[var(--color-text-muted)]">
        {row.hard_cap_usd != null ? `$${row.hard_cap_usd.toFixed(2)}` : '—'}
      </td>
      <td className="px-3 py-2 text-right text-[var(--color-text-muted)]">
        {row.utilization_pct != null ? `${row.utilization_pct.toFixed(0)}%` : '—'}
      </td>
      <td className="px-3 py-2 text-center">
        <StatusPill status={row.status} />
      </td>
      <td data-billing-cap-hits="" className="px-3 py-2 text-right text-xs text-[var(--color-text-muted)]">
        {/* No quota, no cap to hit; with one, 0 hits is a measured count, not a blank. */}
        {row.status === 'UNLIMITED' ? '—' : row.cap_hits}
      </td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={onEdit}
          className="text-xs text-[var(--color-accent)] hover:underline"
        >
          {canEdit ? 'Edit' : 'View'}
        </button>
      </td>
    </tr>
  )
}

function StatusPill({ status }: { status: UsageStatus }) {
  const toneClass =
    status === 'CAPPED'
      ? 'border-[var(--status-failed-bd)]/40 text-[var(--status-failed)] bg-[var(--status-failed-bg)]/10'
      : status === 'SOFT_WARN'
      ? 'border-[var(--status-broken-bd)]/40 text-[var(--status-broken)] bg-[var(--status-broken-bg)]/10'
      : status === 'OK'
      ? 'border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10'
      : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${toneClass}`}>{status}</span>
  )
}

function formatDate(iso: string): string {
  return formatDayIso(iso.slice(0, 10))
}

function formatPeriodEnd(iso: string): string {
  return formatDayIso(shiftDayIso(iso.slice(0, 10), -1))
}

// ── Quota editor modal ─────────────────────────────────────────────────────

const CAP_ACTION_OPTIONS: Array<{ value: QuotaCapAction; label: string; desc: string }> = [
  {
    value: 'SOFT_WARN',
    label: 'Soft warn',
    desc: 'Log + telemetry only — LLM work continues normally',
  },
  {
    value: 'AUTO_DOWNGRADE_TO_ML',
    label: 'Auto-downgrade to ML',
    desc: 'Route further analysis through the sklearn classifier',
  },
  {
    value: 'AUTO_DOWNGRADE_TO_RULES',
    label: 'Auto-downgrade to rules',
    desc: 'Route further analysis through the keyword rules engine',
  },
  {
    value: 'HARD_BLOCK',
    label: 'Hard block',
    desc: 'Refuse LLM analysis entirely until the next period',
  },
]

function QuotaEditor({
  projectId,
  onClose,
  onSaved,
  canEdit,
}: {
  projectId: string
  onClose: () => void
  onSaved: () => Promise<void>
  canEdit: boolean
}) {
  const { quota, isLoading } = useProjectQuota(projectId)
  const [form, setForm] = useState<LlmQuotaWrite>({
    enabled: true,
    period_type: 'MONTHLY',
    included_usd: 0,
    overage_rate_usd: 1,
    hard_cap_usd: 100,
    soft_warn_threshold_pct: 80,
    at_cap_action: 'AUTO_DOWNGRADE_TO_ML',
  })
  const [hydrated, setHydrated] = useState(false)

  // Hydrate form once the quota loads — or leave defaults if no quota exists.
  if (!hydrated && !isLoading && quota) {
    setForm({
      enabled: quota.enabled,
      period_type: quota.period_type,
      included_usd: quota.included_usd,
      overage_rate_usd: quota.overage_rate_usd,
      hard_cap_usd: quota.hard_cap_usd,
      soft_warn_threshold_pct: quota.soft_warn_threshold_pct,
      at_cap_action: quota.at_cap_action,
    })
    setHydrated(true)
  } else if (!hydrated && !isLoading && !quota) {
    setHydrated(true)
  }

  async function save() {
    try {
      await llmBudgetService.putQuota(projectId, form)
      toast.success('Quota saved')
      await onSaved()
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`)
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Edit LLM cost budget"
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-lg w-[560px] max-w-[95vw] p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-[var(--color-text)]">LLM cost budget</h3>

        {isLoading ? (
          <LoadingSpinner />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <NumberInput
                label="Included per month ($)"
                value={form.included_usd}
                onChange={(v) => setForm({ ...form, included_usd: v })}
                disabled={!canEdit}
                step={0.01}
                hint={{ key: 'included', text: 'Spend up to this amount each month is covered; spend above it is overage.' }}
              />
              <NumberInput
                label="Hard cap ($)"
                value={form.hard_cap_usd}
                onChange={(v) => setForm({ ...form, hard_cap_usd: v })}
                disabled={!canEdit}
                step={1}
              />
              <NumberInput
                label="Overage rate ($/$)"
                value={form.overage_rate_usd}
                onChange={(v) => setForm({ ...form, overage_rate_usd: v })}
                disabled={!canEdit}
                step={0.01}
                hint={{ key: 'rate', text: 'Charge = overage × rate. Shown in the table; it does not limit spend (the hard cap does).' }}
              />
              <NumberInput
                label="Soft-warn threshold (%)"
                value={form.soft_warn_threshold_pct}
                onChange={(v) =>
                  setForm({ ...form, soft_warn_threshold_pct: Math.round(v) })
                }
                disabled={!canEdit}
                step={1}
                min={1}
                max={100}
              />
            </div>

            <label className="text-xs flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.enabled}
                disabled={!canEdit}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              <span>Budget enforcement enabled</span>
            </label>

            <fieldset className="space-y-2">
              <legend className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
                At-cap action
              </legend>
              {CAP_ACTION_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className="flex items-start gap-2 text-xs cursor-pointer"
                >
                  <input
                    type="radio"
                    name="at_cap_action"
                    className="mt-0.5"
                    checked={form.at_cap_action === opt.value}
                    disabled={!canEdit}
                    onChange={() => setForm({ ...form, at_cap_action: opt.value })}
                  />
                  <span>
                    <span className="block font-semibold text-[var(--color-text)]">
                      {opt.label}
                    </span>
                    <span className="block text-[var(--color-text-muted)]">{opt.desc}</span>
                  </span>
                </label>
              ))}
            </fieldset>

            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="btn-secondary text-xs">
                Close
              </button>
              {canEdit && (
                <button
                  type="button"
                  onClick={save}
                  className="btn-primary text-xs flex items-center gap-1.5"
                >
                  <Save className="h-3 w-3" /> Save
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function NumberInput({
  label,
  value,
  onChange,
  disabled,
  step = 1,
  min,
  max,
  hint,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  disabled?: boolean
  step?: number
  min?: number
  max?: number
  /** A line under the input saying what the value does (`data-billing-hint={key}`). */
  hint?: { key: string; text: string }
}) {
  return (
    <label className="text-xs block">
      <span className="text-[var(--color-text-muted)]">{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
      />
      {hint && (
        <span data-billing-hint={hint.key} className="mt-1 block text-[10.5px] leading-snug text-[var(--color-text-muted)]">
          {hint.text}
        </span>
      )}
    </label>
  )
}
