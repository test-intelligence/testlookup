import type { ReactNode } from 'react'
import { useState } from 'react'
import useSWR from 'swr'
import { fromNow, formatDateTime } from '@/utils/formatters'
import { Th } from '@/components/ui/Table'
import { Plus, Trash2 } from 'lucide-react'
import { groupFeatureFlags, MUTATING_FLAG_KEYS } from '@/config/featureFlagGroups'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useFeatureFlags } from '@/hooks/useFeatureFlags'
import {
  featureFlagService,
  type FeatureFlag,
  type FeatureFlagCreate,
} from '@/services/featureFlagService'
import { usePermissions } from '@/hooks/usePermissions'
import { helpTopicParam } from '@/components/help/helpTopics'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import InlineRowSection from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'

const HELP_TOPIC = helpTopicParam('/settings/feature-flags')

/**
 * ADMIN-only feature flag management. Lists every flag, supports toggling
 * the global kill switch, editing the rollout percent inline, and creating
 * new flags. Role/project allow-lists are edited via the "Edit" button that
 * opens a JSON text area — keeps the UI small while allowing full control.
 *
 * Every change produces a ``settings_audit_log`` row on the backend so
 * enterprise customers can reconstruct flag history.
 *
 * W27b PAN-06: no modal and no native confirm. A flag row's Edit opens
 * `Edit <key>` in a full-width row under that flag's row, inside its group;
 * Delete flag asks in that same row place.
 */
export default function FeatureFlagsPage() {
  const { isAdmin } = usePermissions()
  // Asks only for an admin: the list answers anyone else 403, which toasted
  // "Requires at least ADMIN role" over this page's own message.
  const { flags, isLoading, isError, refresh } = useFeatureFlags(isAdmin)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<FeatureFlagCreate>({
    key: '',
    description: '',
    enabled_global: false,
    rollout_percent: 100,
  })
  // W27b: one flag form open in the table, under its flag's row (the key is the flag key), and one Delete
  // question at a time (nothing is sent until its own button).
  const editSlot = useInlineSlot<string>()
  const closeEdit = useSlotCloser(editSlot)
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  // W17 ADM-07: the "why" sent with the next ON / OFF or rollout change (cleared after it lands),
  // and the one flag whose History is open under its row.
  const [reason, setReason] = useState('')
  const [historyKey, setHistoryKey] = useState<string | null>(null)

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <PageHeader compact title="Feature Flags" helpTopic={HELP_TOPIC} />
        <EmptyState
          title="Admin access required"
          description="Only administrators can manage feature flags."
        />
      </div>
    )
  }

  if (isLoading) return <LoadingSpinner size="lg" />
  if (isError) return <EmptyState title="Could not load feature flags" />

  async function toggleGlobal(flag: FeatureFlag) {
    try {
      await featureFlagService.update(flag.key, {
        enabled_global: !flag.enabled_global,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      })
      setReason('')
      await refresh()
      toast.success(`${flag.key}: ${flag.enabled_global ? 'disabled' : 'enabled'}`)
    } catch (err) {
      toast.error(`Failed to update ${flag.key}: ${(err as Error).message}`)
    }
  }

  async function updateRollout(flag: FeatureFlag, percent: number) {
    try {
      await featureFlagService.update(flag.key, {
        rollout_percent: percent,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      })
      setReason('')
      await refresh()
    } catch (err) {
      toast.error(`Failed to update rollout: ${(err as Error).message}`)
    }
  }

  async function deleteFlag(flag: FeatureFlag) {
    try {
      await featureFlagService.remove(flag.key)
      await refresh()
      toast.success(`${flag.key} deleted`)
    } catch (err) {
      toast.error(`Failed to delete: ${(err as Error).message}`)
    }
  }

  const grouped = groupFeatureFlags(flags)
  const rowActions = {
    onToggle: (flag: FeatureFlag) => void toggleGlobal(flag),
    onRollout: (flag: FeatureFlag, percent: number) => void updateRollout(flag, percent),
    onEdit: (flag: FeatureFlag) => editSlot.open(flag.key),
    onDelete: (flag: FeatureFlag) => setConfirmingDelete(flag.key),
    onHistory: (flag: FeatureFlag) => setHistoryKey((k) => (k === flag.key ? null : flag.key)),
  }
  /** What opens under a flag's row: its Delete question and / or its Edit form. */
  const belowRow = (flag: FeatureFlag): ReactNode => {
    const asking = confirmingDelete === flag.key
    const editing = editSlot.openKey === flag.key
    const history = historyKey === flag.key
    if (!asking && !editing && !history) return null
    return (
      <div className="space-y-3">
        {history && <FlagHistory flagKey={flag.key} onClose={() => setHistoryKey(null)} />}
        {asking && (
          <InlineConfirm
            message={<>Delete feature flag &quot;<strong>{flag.key}</strong>&quot;? This cannot be undone.</>}
            confirmLabel="Delete"
            tone="danger"
            onConfirm={() => { setConfirmingDelete(null); void deleteFlag(flag) }}
            onCancel={() => setConfirmingDelete(null)}
          />
        )}
        {editing && (
          <EditFlagModal
            flag={flag}
            slot={editSlot}
            onClose={() => closeEdit(flag.key)}
            onSaved={async () => {
              await refresh()
              closeEdit(flag.key)
            }}
          />
        )}
      </div>
    )
  }

  async function createFlag() {
    if (!draft.key.match(/^[a-z][a-z0-9_]*$/)) {
      toast.error('Key must be snake_case (lowercase + underscores)')
      return
    }
    try {
      await featureFlagService.create(draft)
      await refresh()
      setCreating(false)
      setDraft({
        key: '',
        description: '',
        enabled_global: false,
        rollout_percent: 100,
      })
      toast.success(`${draft.key} created`)
    } catch (err) {
      toast.error(`Failed to create: ${(err as Error).message}`)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Feature Flags"
        subtitle="Gate capabilities by global kill switch, project, role, or rollout percent."
        helpTopic={HELP_TOPIC}
        actions={
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="btn-primary text-xs flex items-center gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" /> New Flag
          </button>
        }
      />

      {creating && (
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 space-y-3">
          <h4 className="text-sm font-semibold text-[var(--color-text)]">New feature flag</h4>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs">
              <span className="text-[var(--color-text-muted)]">Key (snake_case)</span>
              <input
                value={draft.key}
                onChange={(e) => setDraft({ ...draft, key: e.target.value })}
                placeholder="cypress_ingest"
                className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
              />
            </label>
            <label className="text-xs">
              <span className="text-[var(--color-text-muted)]">Rollout %</span>
              <input
                type="number"
                min={0}
                max={100}
                value={draft.rollout_percent ?? 100}
                onChange={(e) =>
                  setDraft({ ...draft, rollout_percent: Number(e.target.value) })
                }
                className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
              />
            </label>
          </div>
          <label className="text-xs block">
            <span className="text-[var(--color-text-muted)]">Description</span>
            <textarea
              value={draft.description ?? ''}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              rows={2}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
            />
          </label>
          <label className="text-xs flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.enabled_global ?? false}
              onChange={(e) => setDraft({ ...draft, enabled_global: e.target.checked })}
            />
            <span>Enable globally on create</span>
          </label>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="btn-secondary text-xs"
            >
              Cancel
            </button>
            <button type="button" onClick={createFlag} className="btn-primary text-xs">
              Create
            </button>
          </div>
        </div>
      )}

      {/* W17 ADM-07: the "why" of the next ON / OFF or rollout change; Edit has its own field. */}
      <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="whitespace-nowrap">Reason for the next change (optional)</span>
        <input
          type="text"
          value={reason}
          maxLength={500}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. turn on for the demo — never a secret"
          aria-label="Reason for the next change"
          className="w-96 px-2 py-1 text-xs bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded text-[var(--color-text)]"
        />
      </label>

      {/* Scrolls rather than clips: the 34-character async_decision_report_supersession key
          widened the table past the settings column at 1280 px and hid Scope and Edit. */}
      <div className="overflow-x-auto rounded-md border border-[var(--color-border)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
            <tr>
              <th className="text-left px-3 py-2">Key</th>
              <th className="text-left px-3 py-2">Description</th>
              <th className="text-center px-3 py-2">Global</th>
              <th className="text-center px-3 py-2">Rollout %</th>
              <th className="text-center px-3 py-2">Scope</th>
              <Th className="text-left px-3 py-2">Last changed</Th>
              <th className="text-right px-3 py-2"></th>
            </tr>
          </thead>
          {flags.length === 0 && (
            <tbody>
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-[var(--color-text-muted)]">
                  No feature flags defined yet.
                </td>
              </tr>
            </tbody>
          )}
          {/* W04 AIC-07: grouped flags first, each group its own <tbody>
              under a header row, then every other flag, ungrouped, in the
              server's order. */}
          {grouped.groups.map(({ group, flags: members }) => (
            <tbody key={group.id} data-flag-group-body={group.id}>
              <tr data-flag-group={group.id} className="border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]/60">
                <th colSpan={7} scope="colgroup" className="px-3 py-2 text-left font-normal">
                  <span className="block text-xs font-semibold text-[var(--color-text)]">{group.title}</span>
                  <span className="block text-[11px] text-[var(--color-text-muted)]">{group.blurb}</span>
                </th>
              </tr>
              {members.map((flag) => (
                <FlagRow key={flag.id} flag={flag} {...rowActions} below={belowRow(flag)} />
              ))}
            </tbody>
          ))}
          {grouped.ungrouped.length > 0 && (
            <tbody>
              {grouped.ungrouped.map((flag) => (
                <FlagRow key={flag.id} flag={flag} {...rowActions} below={belowRow(flag)} />
              ))}
            </tbody>
          )}
        </table>
      </div>

    </div>
  )
}

/**
 * One flag as one table row (`data-flag-key`); the ON/OFF button is named "Toggle {key}". What opens for it
 * (W27b: its Edit form, its Delete question) is a full-width row right after it, in the same `<tbody>`, with
 * no `data-flag-key`.
 */
function FlagRow({
  flag,
  onToggle,
  onRollout,
  onEdit,
  onDelete,
  onHistory,
  below,
}: {
  flag: FeatureFlag
  onToggle: (flag: FeatureFlag) => void
  onRollout: (flag: FeatureFlag, percent: number) => void
  onEdit: (flag: FeatureFlag) => void
  onDelete: (flag: FeatureFlag) => void
  onHistory: (flag: FeatureFlag) => void
  below?: ReactNode
}) {
  return (
    <>
    <tr data-flag-key={flag.key} className="border-t border-[var(--color-border)]">
      <td className="px-3 py-2 font-mono text-xs text-[var(--color-text)]">
        <span className="inline-flex items-center gap-1.5">
          {flag.key}
          {MUTATING_FLAG_KEYS.has(flag.key) && (
            <span
              data-flag-mutating=""
              title="This stage writes data (it creates defects), unlike the read-only stages"
              className="font-sans text-[9.5px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-[var(--status-broken-bd)]/50 text-[var(--status-broken)] bg-[var(--status-broken-bg)]/20"
            >
              MUTATING
            </span>
          )}
        </span>
      </td>
      <td
        className="px-3 py-2 text-xs text-[var(--color-text-muted)] max-w-0 w-full truncate"
        title={flag.description || undefined}
      >
        {flag.description || '—'}
      </td>
      <td className="px-3 py-2 text-center">
        <button
          type="button"
          onClick={() => onToggle(flag)}
          className={`text-xs px-2 py-1 rounded border ${
            flag.enabled_global
              ? 'border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10'
              : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
          }`}
          aria-label={`Toggle ${flag.key}`}
        >
          {flag.enabled_global ? 'ON' : 'OFF'}
        </button>
      </td>
      <td className="px-3 py-2 text-center">
        <input
          type="number"
          min={0}
          max={100}
          defaultValue={flag.rollout_percent}
          onBlur={(e) => {
            const v = Number(e.target.value)
            if (v !== flag.rollout_percent) onRollout(flag, v)
          }}
          className="w-14 px-1 py-0.5 text-xs text-center bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
        />
      </td>
      <td className="px-3 py-2 text-center text-[10px] text-[var(--color-text-muted)]">
        {flag.enabled_projects?.length || flag.enabled_roles?.length
          ? `${flag.enabled_projects?.length ?? 0}p / ${flag.enabled_roles?.length ?? 0}r`
          : 'all'}
      </td>
      {/* W17 ADM-07: the time opens the History; the cell wraps so the table still fits the settings
          column (a separate History button pushed Edit / Delete out of view at 1280). */}
      <td
        className="px-3 py-2 text-xs text-[var(--color-text-muted)] max-w-[10rem]"
        data-testid="flag-last-changed"
        title={flag.last_change_reason ? `Reason: ${flag.last_change_reason}` : 'No reason given'}
      >
        <button
          type="button"
          onClick={() => onHistory(flag)}
          aria-label={`History of ${flag.key}`}
          className="text-left text-[var(--color-accent)] hover:underline"
        >
          {changedWhen(flag.updated_at)}
        </button>
        {flag.updated_by_name ? <div>by <span className="text-[var(--color-text)]">{flag.updated_by_name}</span></div> : null}
        {flag.last_change_reason && <div className="truncate italic">“{flag.last_change_reason}”</div>}
      </td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={() => onEdit(flag)}
          className="text-xs text-[var(--color-accent)] hover:underline mr-2"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={() => onDelete(flag)}
          aria-label={`Delete flag ${flag.key}`}
          title={`Delete flag ${flag.key}`}
          className="text-xs text-[var(--status-failed)] hover:underline"
        >
          <Trash2 className="h-3 w-3 inline" />
        </button>
      </td>
    </tr>
    {below && <InlineRowSection colSpan={7}>{below}</InlineRowSection>}
    </>
  )
}

/** `Edit <key>`, in the page under its flag's row (W27b; it was a modal dialog). */
function EditFlagModal({
  flag,
  onClose,
  onSaved,
  slot,
}: {
  flag: FeatureFlag
  onClose: () => void
  onSaved: () => Promise<void>
  slot?: InlineSlot
}) {
  const [projectsJson, setProjectsJson] = useState(
    JSON.stringify(flag.enabled_projects ?? [], null, 2),
  )
  const [rolesJson, setRolesJson] = useState(
    JSON.stringify(flag.enabled_roles ?? [], null, 2),
  )
  const [description, setDescription] = useState(flag.description ?? '')
  const [editReason, setEditReason] = useState('')

  async function save() {
    let projects: string[] | null
    let roles: string[] | null
    try {
      projects = JSON.parse(projectsJson)
      roles = JSON.parse(rolesJson)
    } catch {
      toast.error('Invalid JSON for scope fields')
      return
    }
    try {
      await featureFlagService.update(flag.key, {
        description,
        enabled_projects: Array.isArray(projects) && projects.length ? projects : null,
        enabled_roles: Array.isArray(roles) && roles.length ? roles : null,
        ...(editReason.trim() ? { reason: editReason.trim() } : {}),
      })
      await onSaved()
      toast.success('Flag updated')
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`)
    }
  }

  return (
    <InlineFormSection
      title={<>Edit flag: <span className="font-mono">{flag.key}</span></>}
      ariaLabel={`Edit ${flag.key}`}
      headingLevel={3}
      onClose={onClose}
      showClose={false}
      slot={slot}
      footer={
        <>
          <button type="button" onClick={onClose} className="btn-secondary text-xs">
            Cancel
          </button>
          <button type="button" onClick={save} className="btn-primary text-xs">
            Save
          </button>
        </>
      }
    >
      <div className="max-w-2xl space-y-3">
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">Description</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">
            Enabled projects (JSON array of UUIDs — empty = all)
          </span>
          <textarea
            value={projectsJson}
            onChange={(e) => setProjectsJson(e.target.value)}
            rows={3}
            spellCheck={false}
            className="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">
            Enabled roles (JSON array — empty = all)
          </span>
          <textarea
            value={rolesJson}
            onChange={(e) => setRolesJson(e.target.value)}
            rows={2}
            spellCheck={false}
            className="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">Reason for this change (optional, never a secret)</span>
          <input
            type="text"
            value={editReason}
            maxLength={500}
            onChange={(e) => setEditReason(e.target.value)}
            className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
      </div>
    </InlineFormSection>
  )
}

/** "2 hours ago"; a time ahead of this browser's clock (skew) reads as the date, never "in 26 days". */
function changedWhen(iso: string): string {
  return new Date(iso).getTime() > Date.now() ? formatDateTime(iso) : fromNow(iso)
}

/** W17 ADM-07: a flag's changes, newest first, in the page under its row. */
function FlagHistory({ flagKey, onClose }: { flagKey: string; onClose: () => void }) {
  const { data, error, isLoading } = useSWR(['feature-flag-history', flagKey], () => featureFlagService.history(flagKey))
  const rows = Array.isArray(data) ? data : []
  return (
    <section aria-label={`History of ${flagKey}`} className="space-y-2 text-xs">
      <div className="flex items-center justify-between">
        <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">
          History: <span className="font-mono">{flagKey}</span>
        </h3>
        <button type="button" onClick={onClose} className="btn-secondary text-xs">Close</button>
      </div>
      {isLoading && !data ? (
        <LoadingSpinner size="sm" />
      ) : error ? (
        <p className="text-[var(--ui-danger-ink)]">Could not load the history.</p>
      ) : rows.length === 0 ? (
        <p className="text-[var(--color-text-muted)]">No recorded changes.</p>
      ) : (
        <ul className="m-0 list-none space-y-1 p-0">
          {rows.map((h, i) => (
            <li key={`${h.created_at}-${i}`} className="flex flex-wrap gap-x-2 text-[var(--color-text-muted)]">
              <span className="text-[var(--color-text)]">{h.created_at ? formatDateTime(h.created_at) : '—'}</span>
              <span>{h.actor_name ?? 'unknown'}</span>
              <span>{h.action}{h.changed_fields.length ? `: ${h.changed_fields.join(', ')}` : ''}</span>
              <span className="italic">{h.reason ? `“${h.reason}”` : 'no reason given'}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
