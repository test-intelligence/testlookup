import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle2, GitBranch, Save, TestTube, Trash2, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  githubIntegrationService,
  type GitHubConnectionTestResponse,
  type GitHubIntegrationRead,
  type GitHubIntegrationWrite,
} from '@/services/githubIntegrationService'

/**
 * GitHub Checks integration settings — Tier 1 item 5.
 *
 * QA_LEAD+ configures a single GitHub repo per project. On every
 * ingested test run with a full 40-char commit SHA, TestLookup posts
 * a check run to that commit so developers see a green/red check on
 * their PR alongside the CI results.
 *
 * The page is project-scoped — when viewing in All Projects mode,
 * prompts the user to pick a specific project.
 */
export default function GitHubIntegrationPage() {
  const { canAccessManagement: canEdit } = usePermissions()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = useProjectStore((s) => s.activeProject)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || !activeProjectId

  const [loading, setLoading] = useState(true)
  const [existing, setExisting] = useState<GitHubIntegrationRead | null>(null)
  const [form, setForm] = useState<GitHubIntegrationWrite>({
    enabled: true,
    repo_owner: '',
    repo_name: '',
    api_base_url: 'https://api.github.com',
  })
  const [patDraft, setPatDraft] = useState<string>('')
  const [testing, setTesting] = useState(false)
  const [lastTest, setLastTest] = useState<GitHubConnectionTestResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      if (isAllProjects || !activeProjectId) return
      setLoading(true)
      try {
        const data = await githubIntegrationService.get(activeProjectId)
        if (cancelled) return
        setExisting(data)
        setForm({
          enabled: data.enabled,
          repo_owner: data.repo_owner,
          repo_name: data.repo_name,
          api_base_url: data.api_base_url,
        })
      } catch (err: unknown) {
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status === 404) {
          setExisting(null)
        } else if (!cancelled) {
          toast.error('Failed to load GitHub integration')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [activeProjectId, isAllProjects])

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader title="GitHub Integration" subtitle="Per-project GitHub Checks API configuration" actions={<ExperimentalBadge />} />
        <EmptyState
          title="Select a project"
          description="GitHub integration settings are configured per project. Switch the project selector in the top bar to continue."
        />
      </div>
    )
  }

  if (loading) return <LoadingSpinner size="lg" />

  async function save() {
    if (!activeProjectId || isAllProjects) return
    try {
      const payload: GitHubIntegrationWrite = {
        ...form,
        pat: patDraft ? patDraft : undefined,
      }
      const saved = await githubIntegrationService.upsert(activeProjectId, payload)
      setExisting(saved)
      setPatDraft('')
      toast.success('GitHub integration saved')
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`)
    }
  }

  async function test() {
    if (!activeProjectId || isAllProjects || testing) return
    setTesting(true)
    try {
      const result = await githubIntegrationService.test(activeProjectId)
      setLastTest(result)
      if (result.success) toast.success('Connection OK')
      else toast.error(result.message)
    } catch (err) {
      toast.error(`Test failed: ${(err as Error).message}`)
    } finally {
      setTesting(false)
    }
  }

  async function remove() {
    if (!activeProjectId || isAllProjects) return
    if (!confirm('Delete GitHub integration for this project?')) return
    try {
      await githubIntegrationService.remove(activeProjectId)
      setExisting(null)
      setForm({
        enabled: true,
        repo_owner: '',
        repo_name: '',
        api_base_url: 'https://api.github.com',
      })
      setPatDraft('')
      setLastTest(null)
      toast.success('Integration removed')
    } catch (err) {
      toast.error(`Failed to remove: ${(err as Error).message}`)
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
        title="GitHub Integration"
        subtitle={`Post check runs for ${activeProject?.name || 'this project'}`}
      />

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-[var(--color-accent)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Repository</h3>
          {existing && (
            <span
              className={`ml-auto text-[10px] px-2 py-0.5 rounded border ${
                existing.enabled
                  ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                  : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
              }`}
            >
              {existing.enabled ? 'ENABLED' : 'DISABLED'}
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <FieldText
            label="Owner / Organization"
            value={form.repo_owner}
            onChange={(v) => setForm({ ...form, repo_owner: v })}
            disabled={!canEdit}
            placeholder="acme-corp"
          />
          <FieldText
            label="Repository name"
            value={form.repo_name}
            onChange={(v) => setForm({ ...form, repo_name: v })}
            disabled={!canEdit}
            placeholder="webapp"
          />
        </div>

        <FieldText
          label="API base URL"
          value={form.api_base_url}
          onChange={(v) => setForm({ ...form, api_base_url: v })}
          disabled={!canEdit}
          placeholder="https://api.github.com (or https://ghe.corp.com/api/v3)"
        />

        <FieldText
          label={`Personal Access Token${existing?.has_pat ? ' (leave empty to keep the stored token)' : ''}`}
          value={patDraft}
          onChange={setPatDraft}
          disabled={!canEdit}
          placeholder="ghp_..."
          type="password"
        />

        <label className="text-xs flex items-center gap-2">
          <input
            type="checkbox"
            checked={form.enabled}
            disabled={!canEdit}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          <span>Post a check run on every ingested test run</span>
        </label>

        {lastTest && (
          <div
            className={`rounded-md border p-2 text-xs ${
              lastTest.success
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                : 'border-rose-500/40 bg-rose-500/10 text-rose-300'
            }`}
          >
            {lastTest.success ? (
              <CheckCircle2 className="h-3.5 w-3.5 inline mr-1" />
            ) : (
              <XCircle className="h-3.5 w-3.5 inline mr-1" />
            )}
            {lastTest.message}
            {lastTest.repo_html_url && (
              <>
                {' · '}
                <a
                  href={lastTest.repo_html_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline"
                >
                  Open repo
                </a>
              </>
            )}
          </div>
        )}

        {existing?.last_error && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-300">
            <strong>Last error:</strong> {existing.last_error}
            {existing.last_error_at && (
              <span className="text-[var(--color-text-faint)] ml-1">
                ({new Date(existing.last_error_at).toLocaleString()})
              </span>
            )}
          </div>
        )}

        {existing?.last_posted_at && (
          <div className="text-xs text-[var(--color-text-faint)]">
            Last posted: {new Date(existing.last_posted_at).toLocaleString()}
          </div>
        )}

        {canEdit && (
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={save}
              className="btn-primary text-xs flex items-center gap-1.5"
            >
              <Save className="h-3 w-3" /> Save
            </button>
            <button
              type="button"
              onClick={test}
              disabled={testing}
              className="btn-secondary text-xs flex items-center gap-1.5"
            >
              <TestTube className="h-3 w-3" />
              {testing ? 'Testing…' : 'Test connection'}
            </button>
            {existing && (
              <button
                type="button"
                onClick={remove}
                className="ml-auto text-xs text-rose-400 hover:underline flex items-center gap-1"
              >
                <Trash2 className="h-3 w-3" /> Remove
              </button>
            )}
          </div>
        )}
      </section>

      <section className="text-xs text-[var(--color-text-muted)] space-y-1">
        <p>
          <strong>How it works.</strong> On every ingested test run with a
          full 40-character commit SHA, TestLookup posts a check run to{' '}
          <code>POST /repos/&#123;owner&#125;/&#123;repo&#125;/check-runs</code>{' '}
          containing pass/fail counts, branch, build number, and a deep link
          to the Run Intelligence page.
        </p>
        <p>
          <strong>Offline mode:</strong> when <code>AI_OFFLINE_MODE=true</code>{' '}
          the integration is a silent no-op regardless of the feature flag.
        </p>
        <p>
          <strong>Feature flag:</strong> the subsystem is gated behind{' '}
          <code>github_checks</code> in <em>Settings → Feature Flags</em>.
          Requires a Personal Access Token with <code>repo</code> scope (or{' '}
          <code>checks:write</code> on a fine-grained token).
        </p>
      </section>
    </div>
  )
}

function FieldText({
  label,
  value,
  onChange,
  disabled,
  placeholder,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  disabled?: boolean
  placeholder?: string
  type?: 'text' | 'password'
}) {
  return (
    <label className="text-xs block">
      <span className="text-[var(--color-text-muted)]">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
      />
    </label>
  )
}
