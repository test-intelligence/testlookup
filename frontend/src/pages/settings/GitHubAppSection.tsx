/**
 * Settings › GitHub (W19 INT-06): authenticate a project with the deployment's GitHub App instead of a PAT.
 *
 * - `GitHubAuthMethod` — PAT or GitHub App for this project; "Install GitHub App" opens GitHub's install page
 *   with a signed, user-bound state; the installation id once recorded.
 * - `hooks/useGitHubAppCallback` — GitHub sends the browser back to this page with `installation_id` and `state`; the
 *   page posts them to the setup callback (which verifies the state) and drops them from the address bar.
 * - `GitHubAppAdminCard` (administrators) — the App id, slug, private key and webhook secret. Secrets are
 *   write-only: the page shows "(set)", never the value.
 */
import { useEffect, useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'

import {
  githubAppService,
  type GitHubAppSettingsRead,
  type GitHubAuthKind,
} from '@/services/githubIntegrationService'
import { extractErrorMessage } from '@/services/apiErrors'

function detailOf(err: unknown, fallback: string): string {
  return extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail, fallback)
}

export function GitHubAuthMethod({
  projectId,
  authKind,
  installationId,
  hasPat,
  canEdit,
  onChange,
}: {
  projectId: string
  authKind: GitHubAuthKind
  installationId: number | null
  hasPat: boolean
  canEdit: boolean
  onChange: (kind: GitHubAuthKind) => void
}) {
  const [opening, setOpening] = useState(false)

  async function install() {
    setOpening(true)
    try {
      const { url } = await githubAppService.installUrl(projectId)
      window.location.assign(url)
    } catch (err) {
      toast.error(detailOf(err, 'Could not start the GitHub App installation'))
      setOpening(false)
    }
  }

  return (
    <fieldset className="space-y-2" data-testid="github-auth-method">
      <legend className="text-xs text-[var(--color-text-muted)]">Authentication</legend>
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <label className="flex items-center gap-1.5">
          <input type="radio" name="github-auth" checked={authKind === 'pat'} disabled={!canEdit} onChange={() => onChange('pat')} />
          Personal access token{hasPat ? ' (stored)' : ''}
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" name="github-auth" checked={authKind === 'app'} disabled={!canEdit} onChange={() => onChange('app')} />
          GitHub App
        </label>
      </div>
      {authKind === 'app' && (
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="text-[var(--color-text-secondary)]" data-testid="github-installation">
            {installationId ? (
              <>Installation <strong>{installationId}</strong> — tokens are minted per call and expire within an hour.</>
            ) : (
              'Not installed for this project yet.'
            )}
          </span>
          {canEdit && (
            <button type="button" className="btn-secondary btn-sm" onClick={install} disabled={opening}>
              {installationId ? 'Reinstall GitHub App' : 'Install GitHub App'}
            </button>
          )}
        </div>
      )}
    </fieldset>
  )
}

export function GitHubAppAdminCard() {
  const [data, setData] = useState<GitHubAppSettingsRead | null>(null)
  const [appId, setAppId] = useState('')
  const [slug, setSlug] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    githubAppService
      .getSettings()
      .then((d) => {
        setData(d)
        setAppId(d.app_id ?? '')
        setSlug(d.slug ?? '')
      })
      .catch(() => setLoadError(true))
  }, [])

  async function save() {
    setSaving(true)
    try {
      const d = await githubAppService.putSettings({
        app_id: appId.trim(),
        slug: slug.trim(),
        private_key: privateKey ? privateKey : null,
        webhook_secret: webhookSecret ? webhookSecret : null,
      })
      setData(d)
      setPrivateKey('')
      setWebhookSecret('')
      toast.success('GitHub App saved')
    } catch (err) {
      toast.error(detailOf(err, 'Could not save the GitHub App'))
    } finally {
      setSaving(false)
    }
  }

  const input = 'mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded'
  return (
    <section className="card p-4 space-y-3" data-testid="github-app-admin">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-[var(--color-accent)]" />
        <h3 className="text-sm font-semibold text-[var(--color-text)]">GitHub App (deployment-wide)</h3>
        {data && (
          <span className="ml-auto text-xs text-[var(--color-text-muted)]" data-testid="github-app-status">
            {data.configured ? 'Configured' : 'Not configured'}
          </span>
        )}
      </div>
      {loadError && <p className="text-xs text-[var(--ui-danger)]">Could not load the GitHub App settings.</p>}
      <p className="text-xs text-[var(--color-text-muted)]">
        Register a GitHub App (Checks: read and write, Contents and Pull requests: read), set its Setup URL to this
        page, and paste its id, slug and private key. Projects then choose GitHub App on this page. Only the token
        service reads the key.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">App id</span>
          <input className={input} value={appId} onChange={(e) => setAppId(e.target.value)} placeholder="123456" />
        </label>
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">App slug</span>
          <input className={input} value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="testlookup-acme" />
        </label>
      </div>
      <label className="text-xs block">
        <span className="text-[var(--color-text-muted)] flex items-center gap-1">
          <KeyRound className="h-3 w-3" /> Private key (PEM){data?.private_key_set ? ' — (set); paste a new one to replace it' : ''}
        </span>
        <textarea className={`${input} font-mono h-24`} value={privateKey} onChange={(e) => setPrivateKey(e.target.value)}
          placeholder="-----BEGIN RSA PRIVATE KEY-----" />
      </label>
      <label className="text-xs block">
        <span className="text-[var(--color-text-muted)]">Webhook secret{data?.webhook_secret_set ? ' — (set)' : ''}</span>
        <input className={input} type="password" value={webhookSecret} onChange={(e) => setWebhookSecret(e.target.value)} />
      </label>
      <div className="flex justify-end">
        <button type="button" className="btn-primary btn-sm" onClick={save} disabled={saving || !appId.trim() || !slug.trim()}>
          {saving ? 'Saving…' : 'Save GitHub App'}
        </button>
      </div>
    </section>
  )
}
