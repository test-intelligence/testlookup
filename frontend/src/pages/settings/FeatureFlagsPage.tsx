import { useState } from 'react'
import { ArrowLeft, Plus, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
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

/**
 * ADMIN-only feature flag management. Lists every flag, supports toggling
 * the global kill switch, editing the rollout percent inline, and creating
 * new flags. Role/project allow-lists are edited via the "Edit" button that
 * opens a JSON text area — keeps the UI small while allowing full control.
 *
 * Every change produces a ``settings_audit_log`` row on the backend so
 * enterprise customers can reconstruct flag history.
 */
export default function FeatureFlagsPage() {
  const { isAdmin } = usePermissions()
  const { flags, isLoading, isError, refresh } = useFeatureFlags()
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<FeatureFlagCreate>({
    key: '',
    description: '',
    enabled_global: false,
    rollout_percent: 100,
  })
  const [editing, setEditing] = useState<FeatureFlag | null>(null)

  if (!isAdmin) {
    return (
      <EmptyState
        title="Admin access required"
        description="Only administrators can manage feature flags."
      />
    )
  }

  if (isLoading) return <LoadingSpinner size="lg" />
  if (isError) return <EmptyState title="Failed to load feature flags" />

  async function toggleGlobal(flag: FeatureFlag) {
    try {
      await featureFlagService.update(flag.key, {
        enabled_global: !flag.enabled_global,
      })
      await refresh()
      toast.success(`${flag.key}: ${flag.enabled_global ? 'disabled' : 'enabled'}`)
    } catch (err) {
      toast.error(`Failed to update ${flag.key}: ${(err as Error).message}`)
    }
  }

  async function updateRollout(flag: FeatureFlag, percent: number) {
    try {
      await featureFlagService.update(flag.key, { rollout_percent: percent })
      await refresh()
    } catch (err) {
      toast.error(`Failed to update rollout: ${(err as Error).message}`)
    }
  }

  async function deleteFlag(flag: FeatureFlag) {
    if (!confirm(`Delete feature flag "${flag.key}"? This cannot be undone.`)) return
    try {
      await featureFlagService.remove(flag.key)
      await refresh()
      toast.success(`${flag.key} deleted`)
    } catch (err) {
      toast.error(`Failed to delete: ${(err as Error).message}`)
    }
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
      <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <Link to="/settings" className="flex items-center gap-1 hover:text-[var(--color-text)]">
          <ArrowLeft className="h-3 w-3" /> Settings
        </Link>
      </div>
      <PageHeader
        title="Feature Flags"
        subtitle="Gate capabilities by global kill switch, project, role, or rollout percent."
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

      <div className="overflow-hidden rounded-md border border-[var(--color-border)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
            <tr>
              <th className="text-left px-3 py-2">Key</th>
              <th className="text-left px-3 py-2">Description</th>
              <th className="text-center px-3 py-2">Global</th>
              <th className="text-center px-3 py-2">Rollout %</th>
              <th className="text-center px-3 py-2">Scope</th>
              <th className="text-right px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {flags.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-[var(--color-text-muted)]">
                  No feature flags defined yet.
                </td>
              </tr>
            )}
            {flags.map((flag) => (
              <tr key={flag.id} className="border-t border-[var(--color-border)]">
                <td className="px-3 py-2 font-mono text-xs text-[var(--color-text)]">{flag.key}</td>
                <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] max-w-xs truncate">
                  {flag.description || '—'}
                </td>
                <td className="px-3 py-2 text-center">
                  <button
                    type="button"
                    onClick={() => toggleGlobal(flag)}
                    className={`text-xs px-2 py-1 rounded border ${
                      flag.enabled_global
                        ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
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
                      if (v !== flag.rollout_percent) updateRollout(flag, v)
                    }}
                    className="w-14 px-1 py-0.5 text-xs text-center bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
                  />
                </td>
                <td className="px-3 py-2 text-center text-[10px] text-[var(--color-text-muted)]">
                  {flag.enabled_projects?.length || flag.enabled_roles?.length
                    ? `${flag.enabled_projects?.length ?? 0}p / ${flag.enabled_roles?.length ?? 0}r`
                    : 'all'}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => setEditing(flag)}
                    className="text-xs text-[var(--color-accent)] hover:underline mr-2"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteFlag(flag)}
                    className="text-xs text-rose-400 hover:underline"
                  >
                    <Trash2 className="h-3 w-3 inline" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditFlagModal
          flag={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await refresh()
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}

function EditFlagModal({
  flag,
  onClose,
  onSaved,
}: {
  flag: FeatureFlag
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [projectsJson, setProjectsJson] = useState(
    JSON.stringify(flag.enabled_projects ?? [], null, 2),
  )
  const [rolesJson, setRolesJson] = useState(
    JSON.stringify(flag.enabled_roles ?? [], null, 2),
  )
  const [description, setDescription] = useState(flag.description ?? '')

  async function save() {
    let projects: string[] | null
    let roles: string[] | null
    try {
      projects = JSON.parse(projectsJson)
      roles = JSON.parse(rolesJson)
    } catch (err) {
      toast.error('Invalid JSON for scope fields')
      return
    }
    try {
      await featureFlagService.update(flag.key, {
        description,
        enabled_projects: Array.isArray(projects) && projects.length ? projects : null,
        enabled_roles: Array.isArray(roles) && roles.length ? roles : null,
      })
      await onSaved()
      toast.success('Flag updated')
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`)
    }
  }

  return (
    <div
      role="dialog"
      aria-label={`Edit ${flag.key}`}
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-lg w-[520px] max-w-[95vw] p-5 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Edit flag: <span className="font-mono">{flag.key}</span>
        </h3>
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
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-secondary text-xs">
            Cancel
          </button>
          <button type="button" onClick={save} className="btn-primary text-xs">
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
