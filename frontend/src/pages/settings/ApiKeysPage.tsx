import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Key, Loader2, Plus, ShieldAlert, Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { apiKeyService } from '@/services/apiKeyService'
import { refreshApiKeys, useApiKeys } from '@/hooks/useApiKeys'
import { usePermissions } from '@/hooks/usePermissions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { ApiKey, ApiKeyCreatedResponse } from '@/types/apiKey'

const STREAM_WRITE_SCOPE = 'stream:write'

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Clipboard access denied — copy manually')
    }
  }
  return (
    <button
      type="button"
      onClick={onCopy}
      className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? 'Copied' : label}
    </button>
  )
}

function GenerateKeyForm({
  projectId,
  projectLabel,
  onCreated,
  onCancel,
}: {
  projectId: string
  projectLabel: string
  onCreated: (key: ApiKeyCreatedResponse) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [expiresDays, setExpiresDays] = useState<number | ''>(90)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      const created = await apiKeyService.create({
        name: name.trim(),
        scopes: [STREAM_WRITE_SCOPE],
        project_id: projectId,
        expires_days: typeof expiresDays === 'number' ? expiresDays : null,
      })
      await refreshApiKeys()
      onCreated(created)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to generate API key'
      toast.error(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="card space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Generate streaming key</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Project-scoped key with the <code className="px-1 bg-[var(--color-bg-secondary)] rounded">{STREAM_WRITE_SCOPE}</code> scope.
            Bound to <span className="text-[var(--color-text-secondary)]">{projectLabel}</span>.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="space-y-1">
          <span className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Name</span>
          <input
            type="text"
            required
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="ci-runner-prod"
            className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
            maxLength={100}
            minLength={2}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Expires (days)</span>
          <input
            type="number"
            min={1}
            max={365}
            value={expiresDays}
            onChange={e => setExpiresDays(e.target.value === '' ? '' : Number(e.target.value))}
            className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
          />
          <span className="text-[10px] text-[var(--color-text-muted)]">Leave blank for no expiry</span>
        </label>
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting || !name.trim()}
          className="inline-flex items-center gap-1.5 rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Key className="h-3.5 w-3.5" />}
          {submitting ? 'Generating…' : 'Generate'}
        </button>
      </div>
    </form>
  )
}

function CreatedKeyModal({
  created,
  baseUrl,
  onClose,
}: {
  created: ApiKeyCreatedResponse
  baseUrl: string
  onClose: () => void
}) {
  const curlSnippet = useMemo(
    () =>
      `# Send test results AND a final run_complete event so the run finalises
# (creates the TestRun row + triggers AI analysis). Without run_complete
# the session stays active and the run won't appear in Runs / Overview.
curl -X POST ${baseUrl}/api/v1/stream/ingest \\
  -H "X-API-Key: ${created.raw_key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "run_id": "ci-build-1",
    "events": [
      {"event_type":"test_result","test_name":"smoke","status":"PASSED","duration_ms":15},
      {"event_type":"run_complete"}
    ]
  }'`,
    [created.raw_key, baseUrl],
  )

  const pythonSnippet = useMemo(
    () =>
      `import asyncio
from testlookup_reporter import LiveStream

async def main():
    async with LiveStream(
        base_url="${baseUrl}",
        api_key="${created.raw_key}",
        run_id="ci-build-1",
    ) as s:
        await s.record("test_login", "PASSED", 120)
        await s.record("test_logout", "FAILED", 340, error="AssertionError")

asyncio.run(main())`,
    [created.raw_key, baseUrl],
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="card w-full max-w-2xl space-y-4 border-emerald-700/40">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-emerald-300">API key generated</h3>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              Copy the key now — it won't be shown again. Store it in your CI secret manager.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="rounded border border-emerald-700/40 bg-emerald-950/20 p-3">
          <div className="flex items-center justify-between gap-2">
            <code className="font-mono text-sm text-emerald-300 break-all">{created.raw_key}</code>
            <CopyButton value={created.raw_key} label="Copy key" />
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">curl</p>
            <CopyButton value={curlSnippet} label="Copy snippet" />
          </div>
          <pre className="overflow-x-auto rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-text-secondary)]">
            {curlSnippet}
          </pre>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Python (testlookup-reporter ≥ 1.1)</p>
            <CopyButton value={pythonSnippet} label="Copy snippet" />
          </div>
          <pre className="overflow-x-auto rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-text-secondary)]">
            {pythonSnippet}
          </pre>
        </div>

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

export default function ApiKeysPage() {
  const { isAdmin } = usePermissions()
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const project = useProjectStore(s => s.activeProject)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const projectId = isAllProjects ? null : (activeProjectId ?? null)
  const projectLabel = project?.name ?? 'this project'

  const { data: keys, isLoading, error } = useApiKeys(projectId)
  const [showForm, setShowForm] = useState(false)
  const [created, setCreated] = useState<ApiKeyCreatedResponse | null>(null)
  const [revokingId, setRevokingId] = useState<string | null>(null)

  // Same-origin base URL is what clients will hit; useful for snippet generation.
  const baseUrl = typeof window !== 'undefined' ? window.location.origin : ''

  useEffect(() => {
    if (error) toast.error('Failed to load API keys')
  }, [error])

  const onRevoke = async (key: ApiKey) => {
    if (!window.confirm(`Revoke "${key.name}"? Active clients using this key will start failing immediately.`)) return
    setRevokingId(key.id)
    try {
      await apiKeyService.revoke(key.id)
      await refreshApiKeys()
      toast.success('API key revoked')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to revoke key'
      toast.error(detail)
    } finally {
      setRevokingId(null)
    }
  }

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader title="API Keys" subtitle="Generate streaming keys per project" />
        <EmptyState
          icon={<Key className="h-10 w-10" />}
          title="Select a project"
          description="API keys are scoped to a single project. Pick a project from the top bar to manage its keys."
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="API Keys"
        subtitle={`Streaming and integration keys for ${projectLabel}`}
        actions={
          isAdmin ? (
            <button
              type="button"
              onClick={() => setShowForm(true)}
              disabled={!projectId || showForm}
              className="inline-flex items-center gap-1.5 rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" />
              Generate streaming key
            </button>
          ) : null
        }
      />

      {!isAdmin && (
        <div className="card flex items-center gap-3 border-amber-700/30 bg-amber-900/10 py-3 px-4">
          <ShieldAlert className="h-4 w-4 text-amber-400 flex-shrink-0" />
          <p className="text-sm text-amber-300">
            Project-scoped streaming keys can only be generated by an Administrator. Ask an admin to mint one for this project.
          </p>
        </div>
      )}

      {showForm && projectId && (
        <GenerateKeyForm
          projectId={projectId}
          projectLabel={projectLabel}
          onCreated={(key) => {
            setShowForm(false)
            setCreated(key)
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-10"><LoadingSpinner size="lg" /></div>
        ) : !keys || keys.length === 0 ? (
          <div className="px-4 py-10">
            <EmptyState
              icon={<Key className="h-10 w-10" />}
              title="No API keys yet"
              description={
                isAdmin
                  ? 'Generate a streaming key to start ingesting test execution status from CI.'
                  : 'No keys have been issued for this project yet.'
              }
            />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)]/40 text-[var(--color-text-muted)]">
              <tr className="text-left">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Hint</th>
                <th className="px-4 py-2 font-medium">Scopes</th>
                <th className="px-4 py-2 font-medium">Created</th>
                <th className="px-4 py-2 font-medium">Last used</th>
                <th className="px-4 py-2 font-medium">Expires</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {keys.map(key => (
                <tr key={key.id} className="border-t border-[var(--color-border)]">
                  <td className="px-4 py-2 text-[var(--color-text)]">{key.name}</td>
                  <td className="px-4 py-2 font-mono text-xs text-[var(--color-text-muted)]">{key.key_hint}</td>
                  <td className="px-4 py-2">
                    {key.scopes.length === 0 ? (
                      <span className="text-xs text-[var(--color-text-muted)]">full access</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map(scope => (
                          <span
                            key={scope}
                            className="rounded bg-[var(--color-bg-secondary)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--color-text-secondary)]"
                          >
                            {scope}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-text-muted)]">{formatDateTime(key.created_at)}</td>
                  <td className="px-4 py-2 text-[var(--color-text-muted)]">{formatDateTime(key.last_used_at)}</td>
                  <td className="px-4 py-2 text-[var(--color-text-muted)]">{formatDateTime(key.expires_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onRevoke(key)}
                      disabled={revokingId === key.id}
                      className="inline-flex items-center gap-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-50"
                    >
                      {revokingId === key.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {created && (
        <CreatedKeyModal
          created={created}
          baseUrl={baseUrl}
          onClose={() => setCreated(null)}
        />
      )}
    </div>
  )
}
