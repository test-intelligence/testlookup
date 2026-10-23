import { useEffect, useId, useState, type ReactNode } from 'react'
import { Save } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ScopedLink from '@/components/ui/ScopedLink'
import { helpTopicParam } from '@/components/help/helpTopics'
import { appSettingsService, type IntegrationsConfigRead, type IntegrationsConfigUpdate } from '@/services/appSettingsService'
import { usePermissions } from '@/hooks/usePermissions'
import { fieldErrorsFrom } from '@/utils/fieldErrors'
import {
  ENV_HINT,
  INTEGRATION_FORM_FIELDS,
  NO_CHANGES,
  SAVED,
  SAVE_FAILED,
  SECRET_SET,
  SLACK_CHANNEL_HELP_LEAD,
  SLACK_CHANNEL_HELP_LINK,
  SLACK_CHANNEL_HELP_TO,
  changedIntegrationFields,
  environmentFields,
  initialIntegrationForm,
  secretStateLabel,
  showsEnvHint,
} from './integrationsForm'

const HELP_TOPIC = helpTopicParam('/settings/integrations')

/**
 * The card body's field grid: two fields a row while the cards are stacked,
 * one a row once the cards themselves sit two-up (≥ 1280 px) — two columns of
 * fields either way, none narrower than a URL needs.
 */
const FIELD_GRID = 'grid grid-cols-2 gap-3 xl:grid-cols-1'

const INPUT = 'w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50'

function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled: boolean }) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled}
        className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
      {label}
    </label>
  )
}

/**
 * The lines under an input: the environment hint, a help line, the field's
 * 422 sentence. Each is a `text-xs` block that wraps inside the card; the input
 * is described by every one shown.
 */
function useFieldLines(name: string, hint: boolean, error: string | undefined, helpId?: string) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [hint ? hintId : null, helpId ?? null, error ? errorId : null].filter(Boolean).join(' ') || undefined
  const lines = (
    <>
      {hint && (
        <p id={hintId} data-env-hint={name} className="mt-1 text-xs text-[var(--color-text-muted)]">{ENV_HINT}</p>
      )}
      {error && (
        <p id={errorId} role="alert" data-field-error={name} className="mt-1 text-xs text-[var(--ui-danger-ink)]">{error}</p>
      )}
    </>
  )
  return { id, describedBy, lines }
}

// The label is wired to the input by id. Without it every field this renders —
// Jira URL, user, project key, and the token inputs — is an unnamed edit box to
// a screen reader, however clear the visible text is.
function Field({ name, label, value, onChange, disabled, placeholder, type = 'text', hint = false, error, help, helpId }: {
  name: string; label: string; value: string; onChange: (v: string) => void; disabled: boolean; placeholder?: string; type?: string
  /** The shown value comes from the deployment's environment. */
  hint?: boolean
  /** The server's sentence for this field (a 422). */
  error?: string
  /** A help line under the input (it carries `helpId`). */
  help?: ReactNode
  helpId?: string
}) {
  const { id, describedBy, lines } = useFieldLines(name, hint, error, help ? helpId : undefined)
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-xs text-[var(--color-text-muted)] mb-1">{label}</label>
      <input id={id} type={type} value={value} onChange={e => onChange(e.target.value)} disabled={disabled} placeholder={placeholder}
        aria-describedby={describedBy} aria-invalid={error ? true : undefined}
        className={INPUT} />
      {help}
      {lines}
    </div>
  )
}

/** A token input: never prefilled; its label says whether one is stored here or by the environment. */
function SecretField({ id, label, state, value, onChange, disabled, placeholder, error, name, className }: {
  id: string; label: string; state: string | null; value: string; onChange: (v: string) => void; disabled: boolean
  /** Shown while none is stored (a stored one shows dots). */
  placeholder: string; error?: string; name: string; className?: string
}) {
  const { describedBy, lines } = useFieldLines(name, false, error)
  return (
    <div className={className ?? 'min-w-0'}>
      <label htmlFor={id} className="block text-xs text-[var(--color-text-muted)] mb-1">
        {label} {state && <span data-secret-state={name} className={state === SECRET_SET ? 'text-[var(--status-passed)]' : 'text-[var(--color-text-muted)]'}>{state}</span>}
      </label>
      <input id={id} type="password" value={value} placeholder={state ? '••••••••' : placeholder} disabled={disabled}
        onChange={e => onChange(e.target.value)} aria-describedby={describedBy} aria-invalid={error ? true : undefined}
        className={INPUT} />
      {lines}
    </div>
  )
}

export default function IntegrationsPage() {
  const { isAdmin } = usePermissions()
  const [config, setConfig] = useState<IntegrationsConfigRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  // `initial` is the form as loaded (or as last saved): Save sends only what differs from it.
  const [initial, setInitial] = useState<IntegrationsConfigUpdate>({})
  const [form, setForm] = useState<IntegrationsConfigUpdate>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [noChanges, setNoChanges] = useState(false)
  const slackHelpId = useId()

  useEffect(() => {
    appSettingsService.getIntegrationsConfig()
      .then(cfg => {
        const loaded = initialIntegrationForm(cfg)
        setConfig(cfg)
        setInitial(loaded)
        setForm(loaded)
      })
      .catch(() => toast.error('Could not load integrations'))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    const changed = changedIntegrationFields(initial, form)
    if (Object.keys(changed).length === 0) {
      setNoChanges(true)
      return
    }
    setNoChanges(false)
    setFieldErrors({})
    setSaving(true)
    try {
      const updated = await appSettingsService.updateIntegrationsConfig(changed, { suppressToast: true })
      const saved = initialIntegrationForm(updated)
      setConfig(updated)
      setInitial(saved)
      setForm(saved)
      toast.success(SAVED)
    } catch (err) {
      // A field-level 422 (a refused host, a malformed URL or namespace) shows
      // under its field and the form keeps what was typed; anything else is one toast.
      const errors = fieldErrorsFrom(err, INTEGRATION_FORM_FIELDS)
      if (errors) setFieldErrors(errors)
      else toast.error(SAVE_FAILED)
    } finally {
      setSaving(false)
    }
  }

  function upd(field: keyof IntegrationsConfigUpdate, value: unknown) {
    setForm(prev => ({ ...prev, [field]: value }))
    setNoChanges(false)
    setFieldErrors(prev => {
      if (!(field in prev)) return prev
      const next = { ...prev }
      delete next[field]
      return next
    })
  }

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (!config) return null

  const envFields = environmentFields(config)
  const envHint = (key: Parameters<typeof showsEnvHint>[0]) => showsEnvHint(key, envFields, initial, form)

  return (
    <div className="space-y-4">
      <PageHeader compact helpTopic={HELP_TOPIC} title="Integrations" subtitle="Jira, Splunk, OpenShift, Slack and Teams" />

      {/* Two columns of cards at ≥ 1280 px (P5 item 4) instead of one
          `max-w-2xl` column. */}
      <form onSubmit={handleSave} data-integrations-form="" className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        {/* Jira */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Jira</h3>
            <Toggle label="Enabled" checked={form.jira_enabled ?? false} onChange={v => upd('jira_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className={FIELD_GRID}>
            <Field name="jira_domain" label="Domain" value={form.jira_domain ?? ''} onChange={v => upd('jira_domain', v)} disabled={!isAdmin} placeholder="your-company.atlassian.net"
              hint={envHint('jira_domain')} error={fieldErrors.jira_domain} />
            <Field name="jira_email" label="Email" value={form.jira_email ?? ''} onChange={v => upd('jira_email', v)} disabled={!isAdmin} placeholder="user@company.com"
              hint={envHint('jira_email')} error={fieldErrors.jira_email} />
            <Field name="jira_default_project_key" label="Default Project Key" value={form.jira_default_project_key ?? ''} onChange={v => upd('jira_default_project_key', v)} disabled={!isAdmin} placeholder="QA"
              hint={envHint('jira_default_project_key')} error={fieldErrors.jira_default_project_key} />
            <SecretField id="integration-secret-0" name="jira_api_token" label="API Token" state={secretStateLabel('jira_api_token', config, envFields)}
              value={form.jira_api_token ?? ''} onChange={v => upd('jira_api_token', v)} disabled={!isAdmin}
              placeholder="Enter token" error={fieldErrors.jira_api_token} />
          </div>
        </div>

        {/* Splunk */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Splunk</h3>
            <Toggle label="Enabled" checked={form.splunk_enabled ?? false} onChange={v => upd('splunk_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className={FIELD_GRID}>
            <Field name="splunk_base_url" label="Base URL" value={form.splunk_base_url ?? ''} onChange={v => upd('splunk_base_url', v)} disabled={!isAdmin} placeholder="https://splunk.company.com:8089"
              hint={envHint('splunk_base_url')} error={fieldErrors.splunk_base_url} />
            <SecretField id="integration-secret-1" name="splunk_api_token" label="API Token" state={secretStateLabel('splunk_api_token', config, envFields)}
              value={form.splunk_api_token ?? ''} onChange={v => upd('splunk_api_token', v)} disabled={!isAdmin}
              placeholder="Enter token" error={fieldErrors.splunk_api_token} />
          </div>
        </div>

        {/* OpenShift */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">OpenShift / Kubernetes</h3>
            <Toggle label="Enabled" checked={form.ocp_enabled ?? false} onChange={v => upd('ocp_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className={FIELD_GRID}>
            <Field name="ocp_api_url" label="API URL" value={form.ocp_api_url ?? ''} onChange={v => upd('ocp_api_url', v)} disabled={!isAdmin} placeholder="https://api.cluster.example.com:6443"
              hint={envHint('ocp_api_url')} error={fieldErrors.ocp_api_url} />
            <Field name="ocp_default_namespace" label="Default Namespace" value={form.ocp_default_namespace ?? ''} onChange={v => upd('ocp_default_namespace', v)} disabled={!isAdmin} placeholder="qa-testing"
              hint={envHint('ocp_default_namespace')} error={fieldErrors.ocp_default_namespace} />
            {/* `xl:col-span-1`: a bare `col-span-2` in the one-column xl grid
                would open an implicit second column. */}
            <SecretField id="integration-secret-2" name="ocp_sa_token" label="Service Account Token" state={secretStateLabel('ocp_sa_token', config, envFields)}
              value={form.ocp_sa_token ?? ''} onChange={v => upd('ocp_sa_token', v)} disabled={!isAdmin}
              placeholder="Enter SA token" error={fieldErrors.ocp_sa_token}
              className="col-span-2 xl:col-span-1 min-w-0" />
          </div>
        </div>

        {/* Slack — INT-02 (D21): no Default Channel. An incoming webhook posts to
            the one channel it was created for; other channels are routed per
            team in Team channels. */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Slack</h3>
            <Toggle label="Enabled" checked={form.slack_enabled ?? false} onChange={v => upd('slack_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className={FIELD_GRID}>
            <Field name="slack_webhook_url" label={webhookLabel(secretStateLabel('slack_webhook_url', config, envFields))} type="password"
              value={form.slack_webhook_url ?? ''} onChange={v => upd('slack_webhook_url', v)} disabled={!isAdmin}
              placeholder={config.slack_webhook_set ? '••••••••' : 'https://hooks.slack.com/services/...'}
              error={fieldErrors.slack_webhook_url} helpId={slackHelpId}
              help={
                <p id={slackHelpId} data-slack-channel-help="" className="mt-1 text-xs text-[var(--color-text-muted)]">
                  {SLACK_CHANNEL_HELP_LEAD}
                  {/* ScopedLink: Team channels shows one project at a time; the
                      sentence already says "per team", so the hint stays in the title. */}
                  <ScopedLink to={SLACK_CHANNEL_HELP_TO} hintInTitleOnly className="text-[var(--color-accent)] hover:underline">{SLACK_CHANNEL_HELP_LINK}</ScopedLink>.
                </p>
              } />
          </div>
        </div>

        {/* Teams */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Microsoft Teams</h3>
            <Toggle label="Enabled" checked={form.teams_enabled ?? false} onChange={v => upd('teams_enabled', v)} disabled={!isAdmin} />
          </div>
          <Field name="teams_webhook_url" label={webhookLabel(secretStateLabel('teams_webhook_url', config, envFields))} type="password"
            value={form.teams_webhook_url ?? ''} onChange={v => upd('teams_webhook_url', v)} disabled={!isAdmin}
            placeholder={config.teams_webhook_set ? '••••••••' : 'https://outlook.office.com/webhook/...'}
            error={fieldErrors.teams_webhook_url} />
        </div>

        {/* GitHub is configured in ONE place, per project (P5 item 3): the
            card that sat here wrote a global repo + token that only the
            Integration Health probe read, while checks and PR comments come
            from /settings/github. */}
        <p data-github-pointer="" className="text-xs text-[var(--color-text-muted)] xl:col-span-2">
          GitHub checks and pull-request comments are set up per project in{' '}
          {/* ScopedLink: /settings/github shows one project at a time; the
              sentence already says "per project", so the hint stays in the title. */}
          <ScopedLink to="/settings/github" hintInTitleOnly className="text-[var(--color-accent)] hover:underline">GitHub settings</ScopedLink>.
        </p>

        {isAdmin && (
          <div className="flex flex-wrap items-center gap-3 xl:col-span-2">
            <button type="submit" disabled={saving}
              className="btn-primary flex items-center gap-2">
              <Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save Integrations'}
            </button>
            {noChanges && (
              <p role="status" data-no-changes="" className="text-xs text-[var(--color-text-muted)]">{NO_CHANGES}</p>
            )}
          </div>
        )}
      </form>
    </div>
  )
}

/** "Webhook URL", plus where the stored one comes from. */
function webhookLabel(state: string | null): string {
  return state ? `Webhook URL ${state}` : 'Webhook URL'
}
