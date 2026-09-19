import { useEffect, useId, useState } from 'react'
import { Save } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { appSettingsService, type IntegrationsConfigRead, type IntegrationsConfigUpdate } from '@/services/appSettingsService'
import { usePermissions } from '@/hooks/usePermissions'

function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled: boolean }) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled}
        className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
      {label}
    </label>
  )
}

// The label is wired to the input by id. Without it every field this renders —
// Jira URL, user, project key, and the token inputs — is an unnamed edit box to
// a screen reader, however clear the visible text is.
function Field({ label, value, onChange, disabled, placeholder, type = 'text' }: {
  label: string; value: string; onChange: (v: string) => void; disabled: boolean; placeholder?: string; type?: string
}) {
  const id = useId()
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-[var(--color-text-muted)] mb-1">{label}</label>
      <input id={id} type={type} value={value} onChange={e => onChange(e.target.value)} disabled={disabled} placeholder={placeholder}
        className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
    </div>
  )
}

export default function IntegrationsPage() {
  const { isAdmin } = usePermissions()
  const [config, setConfig] = useState<IntegrationsConfigRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState<IntegrationsConfigUpdate>({})

  useEffect(() => {
    appSettingsService.getIntegrationsConfig()
      .then(cfg => {
        setConfig(cfg)
        setForm({
          jira_enabled: cfg.jira_enabled,
          jira_domain: cfg.jira_domain ?? '',
          jira_email: cfg.jira_email ?? '',
          jira_default_project_key: cfg.jira_default_project_key,
          splunk_enabled: cfg.splunk_enabled,
          splunk_base_url: cfg.splunk_base_url ?? '',
          ocp_enabled: cfg.ocp_enabled,
          ocp_api_url: cfg.ocp_api_url ?? '',
          ocp_default_namespace: cfg.ocp_default_namespace,
          slack_enabled: cfg.slack_enabled,
          slack_default_channel: cfg.slack_default_channel,
          teams_enabled: cfg.teams_enabled,
          github_repo: cfg.github_repo ?? '',
        })
      })
      .catch(() => toast.error('Failed to load integrations'))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await appSettingsService.updateIntegrationsConfig(form)
      setConfig(updated)
      toast.success('Integrations saved')
    } catch {
      toast.error('Failed to save integrations')
    } finally {
      setSaving(false)
    }
  }

  function upd(field: keyof IntegrationsConfigUpdate, value: unknown) {
    setForm(prev => ({ ...prev, [field]: value }))
  }

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (!config) return null

  return (
    <div className="space-y-6">
      <PageHeader title="Integrations" subtitle="Jira, Splunk, OpenShift, Slack, Teams, and GitHub" />

      <form onSubmit={handleSave} className="space-y-6 max-w-2xl">
        {/* Jira */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Jira</h3>
            <Toggle label="Enabled" checked={form.jira_enabled ?? false} onChange={v => upd('jira_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Domain" value={form.jira_domain ?? ''} onChange={v => upd('jira_domain', v)} disabled={!isAdmin} placeholder="your-company.atlassian.net" />
            <Field label="Email" value={form.jira_email ?? ''} onChange={v => upd('jira_email', v)} disabled={!isAdmin} placeholder="user@company.com" />
            <Field label="Default Project Key" value={form.jira_default_project_key ?? ''} onChange={v => upd('jira_default_project_key', v)} disabled={!isAdmin} placeholder="QA" />
            <div>
              <label htmlFor="integration-secret-0" className="block text-xs text-[var(--color-text-muted)] mb-1">API Token {config.jira_token_set && <span className="text-[var(--status-passed)]">(set)</span>}</label>
              <input id="integration-secret-0" type="password" placeholder={config.jira_token_set ? '••••••••' : 'Enter token'} disabled={!isAdmin}
                onChange={e => upd('jira_api_token', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {/* Splunk */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Splunk</h3>
            <Toggle label="Enabled" checked={form.splunk_enabled ?? false} onChange={v => upd('splunk_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Base URL" value={form.splunk_base_url ?? ''} onChange={v => upd('splunk_base_url', v)} disabled={!isAdmin} placeholder="https://splunk.company.com:8089" />
            <div>
              <label htmlFor="integration-secret-1" className="block text-xs text-[var(--color-text-muted)] mb-1">API Token {config.splunk_token_set && <span className="text-[var(--status-passed)]">(set)</span>}</label>
              <input id="integration-secret-1" type="password" placeholder={config.splunk_token_set ? '••••••••' : 'Enter token'} disabled={!isAdmin}
                onChange={e => upd('splunk_api_token', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {/* OpenShift */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">OpenShift / Kubernetes</h3>
            <Toggle label="Enabled" checked={form.ocp_enabled ?? false} onChange={v => upd('ocp_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="API URL" value={form.ocp_api_url ?? ''} onChange={v => upd('ocp_api_url', v)} disabled={!isAdmin} placeholder="https://api.cluster.example.com:6443" />
            <Field label="Default Namespace" value={form.ocp_default_namespace ?? ''} onChange={v => upd('ocp_default_namespace', v)} disabled={!isAdmin} placeholder="qa-testing" />
            <div className="col-span-2">
              <label htmlFor="integration-secret-2" className="block text-xs text-[var(--color-text-muted)] mb-1">Service Account Token {config.ocp_token_set && <span className="text-[var(--status-passed)]">(set)</span>}</label>
              <input id="integration-secret-2" type="password" placeholder={config.ocp_token_set ? '••••••••' : 'Enter SA token'} disabled={!isAdmin}
                onChange={e => upd('ocp_sa_token', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {/* Slack */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Slack</h3>
            <Toggle label="Enabled" checked={form.slack_enabled ?? false} onChange={v => upd('slack_enabled', v)} disabled={!isAdmin} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label={`Webhook URL${config.slack_webhook_set ? ' (set)' : ''}`} type="password" value={form.slack_webhook_url ?? ''} onChange={v => upd('slack_webhook_url', v || undefined)} disabled={!isAdmin} placeholder={config.slack_webhook_set ? '••••••••' : 'https://hooks.slack.com/services/...'} />
            <Field label="Default Channel" value={form.slack_default_channel ?? ''} onChange={v => upd('slack_default_channel', v)} disabled={!isAdmin} placeholder="#qa-alerts" />
          </div>
        </div>

        {/* Teams */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">Microsoft Teams</h3>
            <Toggle label="Enabled" checked={form.teams_enabled ?? false} onChange={v => upd('teams_enabled', v)} disabled={!isAdmin} />
          </div>
          <Field label={`Webhook URL${config.teams_webhook_set ? ' (set)' : ''}`} type="password" value={form.teams_webhook_url ?? ''} onChange={v => upd('teams_webhook_url', v || undefined)} disabled={!isAdmin} placeholder={config.teams_webhook_set ? '••••••••' : 'https://outlook.office.com/webhook/...'} />
        </div>

        {/* GitHub */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">GitHub</h3>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Repository" value={form.github_repo ?? ''} onChange={v => upd('github_repo', v)} disabled={!isAdmin} placeholder="org/repo" />
            <div>
              <label htmlFor="integration-secret-3" className="block text-xs text-[var(--color-text-muted)] mb-1">Token {config.github_token_set && <span className="text-[var(--status-passed)]">(set)</span>}</label>
              <input id="integration-secret-3" type="password" placeholder={config.github_token_set ? '••••••••' : 'ghp_...'} disabled={!isAdmin}
                onChange={e => upd('github_token', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {isAdmin && (
          <button type="submit" disabled={saving}
            className="flex items-center gap-2 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-5 py-2.5 rounded-lg transition-colors">
            <Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save Integrations'}
          </button>
        )}
      </form>
    </div>
  )
}
