import { useState } from 'react'
import { AlertTriangle, Loader2, Lock, Save, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import PageHeader from '@/components/ui/PageHeader'
import { usePermissions } from '@/hooks/usePermissions'
import { updateMfaPolicy, useMfaPolicy } from '@/hooks/useMfaPolicy'
import { MFA_ROLES, type MfaPolicy, type MfaPolicyUpdate, type MfaRole } from '@/types/mfa'
import { describeMfaError } from '@/utils/mfaErrors'

const ROLE_LABEL: Record<MfaRole, string> = {
  VIEWER: 'Viewer',
  TESTER: 'Tester',
  QA_ENGINEER: 'QA Engineer',
  QA_LEAD: 'QA Lead',
  ADMIN: 'Admin',
}

/** "" is the sentinel for "everyone" — the select cannot carry a real null. */
const EVERYONE = ''

interface Draft {
  require_mfa: boolean
  required_for_role: MfaRole | ''
  lockout_enabled: boolean
  lockout_threshold: string
  lockout_duration_minutes: string
}

function toDraft(policy: MfaPolicy): Draft {
  return {
    require_mfa: policy.require_mfa,
    required_for_role: policy.required_for_role ?? EVERYONE,
    lockout_enabled: policy.lockout_enabled,
    lockout_threshold: String(policy.lockout_threshold),
    lockout_duration_minutes: String(policy.lockout_duration_minutes),
  }
}

export default function MfaPolicyPage() {
  const { isAdmin } = usePermissions()
  // QA_LEAD can read the policy, so the fetch is not gated on isAdmin — only
  // the controls are. The backend is the real boundary on both reads and writes.
  const { data: policy, error, isLoading, mutate } = useMfaPolicy()

  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [pendingEnable, setPendingEnable] = useState(false)

  // Re-seed the editable form from the canonical policy whenever it changes,
  // without an effect (react-hooks/set-state-in-effect is an error here) —
  // the same adjust-state-during-render pattern ProfilePage uses.
  const [synced, setSynced] = useState<MfaPolicy | null>(null)
  if (policy && policy !== synced) {
    setSynced(policy)
    setDraft(toDraft(policy))
  }

  if (!isAdmin) {
    return (
      <EmptyState
        icon={<Lock className="h-6 w-6" />}
        title="Admin access required"
        description="Workspace MFA and lockout policy is restricted to platform administrators."
      />
    )
  }

  const patch = (next: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...next } : d))

  const persist = async (d: Draft) => {
    const threshold = Number(d.lockout_threshold)
    const duration = Number(d.lockout_duration_minutes)
    if (!Number.isInteger(threshold) || threshold < 3 || threshold > 100) {
      toast.error('Lockout threshold must be a whole number between 3 and 100')
      return
    }
    if (!Number.isInteger(duration) || duration < 1 || duration > 1440) {
      toast.error('Lockout duration must be a whole number between 1 and 1440 minutes')
      return
    }

    const payload: MfaPolicyUpdate = {
      require_mfa: d.require_mfa,
      lockout_enabled: d.lockout_enabled,
      lockout_threshold: threshold,
      lockout_duration_minutes: duration,
    }
    // Nulling the role needs the explicit flag — sending `required_for_role:
    // null` alone is a no-op on the backend (`exclude_none=True` merge).
    if (d.required_for_role === EVERYONE) {
      payload.clear_required_for_role = true
    } else {
      payload.required_for_role = d.required_for_role
    }

    setSaving(true)
    try {
      const updated = await updateMfaPolicy(payload)
      await mutate(updated, { revalidate: false })
      setSynced(updated)
      setDraft(toDraft(updated))
      setPendingEnable(false)
      toast.success('MFA policy saved')
    } catch (err) {
      toast.error(describeMfaError(err).message)
    } finally {
      setSaving(false)
    }
  }

  const handleSave = () => {
    if (!draft) return
    // Turning the requirement ON is the one change that locks people out of
    // their own accounts if they get it wrong — make them read the consequence.
    if (draft.require_mfa && !policy?.require_mfa) {
      setPendingEnable(true)
      return
    }
    void persist(draft)
  }

  const scopeLabel =
    draft?.required_for_role === EVERYONE
      ? 'every user in this workspace'
      : `every ${ROLE_LABEL[(draft?.required_for_role || 'ADMIN') as MfaRole]} and above`

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="MFA & Lockout Policy"
        subtitle="Workspace-wide two-factor requirement and failed-sign-in lockout. ADMIN only."
      />

      {isLoading && (
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading policy…
        </p>
      )}

      {error && !isLoading && (
        <p role="alert" className="text-sm text-[var(--status-failed)]">
          {describeMfaError(error).message}
        </p>
      )}

      {draft && (
        <>
          {/* ── MFA requirement ─────────────────────────────────────────── */}
          <section className="card space-y-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
              <ShieldCheck className="h-4 w-4 text-[var(--color-text-muted)]" />
              Require two-factor authentication
            </h2>

            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={draft.require_mfa}
                onChange={(e) => patch({ require_mfa: e.target.checked })}
              />
              <span className="text-sm text-[var(--color-text-secondary)]">
                <span className="font-medium text-[var(--color-text)]">
                  Require MFA to sign in
                </span>
                <br />
                Users in scope who have not enrolled are stopped at login and walked
                through setup before they can continue.
              </span>
            </label>

            <div>
              <label
                htmlFor="mfa-required-role"
                className="mb-1 block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                Applies to
              </label>
              <select
                id="mfa-required-role"
                className="input w-full"
                value={draft.required_for_role}
                disabled={!draft.require_mfa}
                onChange={(e) =>
                  patch({ required_for_role: e.target.value as MfaRole | '' })
                }
              >
                <option value={EVERYONE}>Everyone</option>
                {MFA_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABEL[role]} and above
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                Roles are ranked Viewer → Tester → QA Engineer → QA Lead → Admin.
              </p>
            </div>
          </section>

          {/* ── Lockout ─────────────────────────────────────────────────── */}
          <section className="card space-y-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
              <Lock className="h-4 w-4 text-[var(--color-text-muted)]" />
              Failed sign-in lockout
            </h2>

            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={draft.lockout_enabled}
                onChange={(e) => patch({ lockout_enabled: e.target.checked })}
              />
              <span className="text-sm text-[var(--color-text-secondary)]">
                <span className="font-medium text-[var(--color-text)]">
                  Lock accounts after repeated failures
                </span>
                <br />
                Applies to both password and second-factor attempts. Locked users get a
                429 telling them how long to wait.
              </span>
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label
                  htmlFor="mfa-lockout-threshold"
                  className="mb-1 block text-sm font-medium text-[var(--color-text-secondary)]"
                >
                  Failed attempts before lockout
                </label>
                <input
                  id="mfa-lockout-threshold"
                  type="number"
                  min={3}
                  max={100}
                  className="input w-full"
                  disabled={!draft.lockout_enabled}
                  value={draft.lockout_threshold}
                  onChange={(e) => patch({ lockout_threshold: e.target.value })}
                />
                <p className="mt-1 text-xs text-[var(--color-text-muted)]">3–100</p>
              </div>
              <div>
                <label
                  htmlFor="mfa-lockout-duration"
                  className="mb-1 block text-sm font-medium text-[var(--color-text-secondary)]"
                >
                  Lockout duration (minutes)
                </label>
                <input
                  id="mfa-lockout-duration"
                  type="number"
                  min={1}
                  max={1440}
                  className="input w-full"
                  disabled={!draft.lockout_enabled}
                  value={draft.lockout_duration_minutes}
                  onChange={(e) => patch({ lockout_duration_minutes: e.target.value })}
                />
                <p className="mt-1 text-xs text-[var(--color-text-muted)]">1–1440</p>
              </div>
            </div>
          </section>

          {/* ── Enable confirmation ─────────────────────────────────────── */}
          {pendingEnable && (
            <section
              role="alertdialog"
              aria-label="Confirm enabling required MFA"
              data-testid="mfa-enable-confirm"
              className="card space-y-4 border-[var(--status-broken-bd)] bg-[var(--status-broken-bg-soft)]"
            >
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
                <AlertTriangle className="h-4 w-4 text-[var(--status-broken)]" />
                Turn on required two-factor authentication?
              </h2>
              <ul className="list-disc space-y-1.5 pl-5 text-sm text-[var(--color-text-secondary)]">
                <li>
                  <strong>{scopeLabel}</strong> will be forced to enrol the next time they
                  sign in. They cannot skip it and cannot reach TestLookup until they
                  finish.
                </li>
                <li>
                  Users who sign in through your identity provider are{' '}
                  <strong>exempt</strong> — their second factor is managed by the IdP, not
                  by TestLookup.
                </li>
                <li>
                  If someone loses their device and has no recovery codes left, they are
                  locked out. Getting them back in needs an administrator to run the
                  breakglass reset script on the server — there is no self-service path.
                </li>
              </ul>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void persist(draft)}
                  className="btn-primary flex items-center gap-2 disabled:opacity-50"
                >
                  {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                  Yes, require MFA
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => setPendingEnable(false)}
                  className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </section>
          )}

          {!pendingEnable && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="btn-primary flex items-center gap-2 disabled:opacity-50"
              >
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                Save policy
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
