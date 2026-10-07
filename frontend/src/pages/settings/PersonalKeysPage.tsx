/**
 * My API keys (UX redesign P5, Settings › Account): the keys the signed-in
 * person owns, listed, minted and revoked as them (`/api/v1/keys` with no
 * project — owner-only on every verb). Not a project's streaming keys
 * (`/settings/api-keys`).
 *
 * It was a tab of the Users page, which only a QA lead or admin can open;
 * the API lets every QA engineer own keys, so a QA engineer had keys and no
 * screen for them. A role below QA engineer cannot own one: the page says
 * so instead of failing the list with a 403.
 *
 * Named PersonalKeysPage, not MyApiKeysPage: `.gitignore`'s `*apikey*` matches
 * that name case-insensitively on Windows, where git would silently leave the
 * file out of a commit.
 */
import { useState } from 'react'
import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { useApiKeys, refreshApiKeys } from '@/hooks/useApiKeys'
import { userManagementService } from '@/services/userManagementService'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import ScopedLink from '@/components/ui/ScopedLink'
import { copyTextToClipboard } from '@/utils/clipboard'
import { AVAILABLE_SCOPES } from '@/pages/keyScopeOptions'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import { helpTopicParam } from '@/components/help/helpTopics'
import { usePermissions } from '@/hooks/usePermissions'

const HELP_TOPIC = helpTopicParam('/settings/my-api-keys')

export default function PersonalKeysPage() {
  const { canGenerateApiKeys } = usePermissions()
  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="My API keys"
        subtitle="Keys you own, for your own scripts and pipelines; each acts as you"
        helpTopic={HELP_TOPIC}
      />
      {canGenerateApiKeys ? (
        <ApiKeysTab canGenerateApiKeys />
      ) : (
        <EmptyState
          title="Your role cannot own API keys"
          description="A key acts as you, and owning one needs the QA engineer role or above. Ask a QA lead if you need one."
        />
      )}
    </div>
  )
}

// ── My API Keys Tab ───────────────────────────────────────────

/**
 * The signed-in user's own keys: listed, minted and revoked as them
 * (`/api/v1/keys` with no project — owner-only on every verb). Not the
 * project's streaming keys, which live at `/settings/api-keys`.
 */
function ApiKeysTab({ canGenerateApiKeys }: { canGenerateApiKeys: boolean }) {
  const { data: keys, isLoading, error, mutate } = useApiKeys()
  const [showCreateModal, setShowCreateModal] = useState(false)

  async function handleRevoke(keyId: string, name: string) {
    if (!confirm(`Revoke your key "${name}"? This cannot be undone.`)) return
    try {
      await userManagementService.revokeApiKey(keyId)
      refreshApiKeys()
      toast.success('API key revoked')
    } catch {
      toast.error('Failed to revoke key')
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p data-my-api-keys-intro="" className="text-xs text-[var(--color-text-muted)]">
          API keys you own. A key acts as you, with your role narrowed by its scopes. A project&rsquo;s
          CI keys are under{' '}
          {/* ScopedLink: that page shows one project at a time; "a project's"
              already says so, so the hint stays in the title. */}
          <ScopedLink to="/settings/api-keys" hintInTitleOnly className="text-[var(--color-accent)] hover:underline">Streaming API keys</ScopedLink>.
        </p>
        {canGenerateApiKeys && (
          <button onClick={() => setShowCreateModal(true)}
            className="flex shrink-0 items-center gap-1.5 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] text-sm px-3 py-1.5 rounded transition-colors">
            <Plus className="h-4 w-4" /> Generate key
          </button>
        )}
      </div>
      {/* A failed read is not "You have no API keys": that would invite
          someone to mint a second key beside the one they already have. */}
      {error && !keys ? (
        <DataUnavailable error={error} onRetry={() => void mutate()} testId="my-api-keys-unavailable" />
      ) : (
      <div className="bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]/80">
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Name</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Key</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Scopes</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Expires</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Last Used</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {(keys ?? []).map((k) => (
              <tr key={k.id} className="hover:bg-[var(--color-bg-hover)]/30 transition-colors">
                <td className="px-4 py-3 font-medium text-[var(--color-text)]">{k.name}</td>
                <td className="px-4 py-3"><code className="text-xs text-[var(--status-passed)] bg-[var(--color-bg-card)] px-2 py-0.5 rounded">{k.key_hint}</code></td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.length > 0 ? k.scopes.map((s) => (
                      <span key={s} className="text-xs bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] px-1.5 py-0.5 rounded">{s}</span>
                    )) : <span className="text-xs text-[var(--color-text-muted)]">full access</span>}
                  </div>
                </td>
                <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">{k.expires_at ? new Date(k.expires_at).toLocaleDateString() : <span className="text-[var(--color-text-muted)]">Never</span>}</td>
                <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">{k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : <span className="text-[var(--color-text-muted)]">—</span>}</td>
                <td className="px-4 py-3">
                  <button onClick={() => handleRevoke(k.id, k.name)} className="text-[var(--status-failed)] hover:text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/20 p-1 rounded transition-colors">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {(keys ?? []).length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-muted)]">You have no API keys. Generate one to authenticate a script or pipeline as you.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      )}
      {showCreateModal && <CreateApiKeyModal onClose={() => setShowCreateModal(false)} />}
    </div>
  )
}

// ── Create API Key Modal ──────────────────────────────────────


function CreateApiKeyModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>([])
  const [expiresDays, setExpiresDays] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  function toggleScope(s: string) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      const result = await userManagementService.createApiKey(name, scopes, expiresDays ? parseInt(expiresDays) : undefined)
      setCreatedKey(result.raw_key)
      refreshApiKeys()
    } catch {
      toast.error('Failed to generate API key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="generate-api-key-title" className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-md p-6 space-y-4">
        <h2 id="generate-api-key-title" className="text-lg font-semibold text-[var(--color-text)]">Generate my API key</h2>
        {createdKey ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 p-3 bg-[var(--status-broken-bg)]/30 border border-[var(--status-broken-bd)]/50 rounded text-[var(--status-broken)] text-xs">
              <RefreshCw className="h-4 w-4 flex-shrink-0" />
              <span>Copy this key now — it will not be shown again.</span>
            </div>
            <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded p-3">
              <code className="text-xs text-[var(--status-passed)] break-all">{createdKey}</code>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={async () => {
                  const ok = await copyTextToClipboard(createdKey)
                  if (ok) toast.success('Copied!')
                  else toast.error('Clipboard access denied — copy manually')
                }}
                className="text-sm text-[var(--color-text)] hover:text-[var(--color-text-secondary)] px-3 py-1.5">Copy</button>
              <button onClick={onClose} className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded">Done</button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name | expiry in two columns (P5 item 4); the scope chips below. */}
            <div data-create-key-fields="" className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="user-field-11" className="block text-sm text-[var(--color-text-muted)] mb-1">Key name</label>
                <input id="user-field-11" required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. GitHub Actions CI"
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]" />
              </div>
              <div>
                <label htmlFor="user-field-12" className="block text-sm text-[var(--color-text-muted)] mb-1">Expiry (days, optional)</label>
                <input id="user-field-12" type="number" min={1} max={365} value={expiresDays} onChange={(e) => setExpiresDays(e.target.value)} placeholder="Never expires"
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]" />
              </div>
            </div>
            <div>
              <span id="apikey-scopes" className="block text-sm text-[var(--color-text-muted)] mb-2">Scopes (leave empty for full access)</span>
              <div role="group" aria-labelledby="apikey-scopes" className="flex flex-wrap gap-2">
                {AVAILABLE_SCOPES.map((s) => (
                  <button key={s} type="button" onClick={() => toggleScope(s)}
                    className={`text-xs px-2 py-1 rounded border transition-colors ${scopes.includes(s) ? 'bg-white/10 border-[var(--color-border-light)] text-[var(--color-text-secondary)]' : 'bg-[var(--color-bg-hover)] border-[var(--color-border-light)] text-[var(--color-text-muted)] hover:border-[var(--color-border-light)]'}`}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="submit" disabled={loading || !name}
                className="bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-4 py-2 rounded transition-colors">
                {loading ? 'Generating…' : 'Generate'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
