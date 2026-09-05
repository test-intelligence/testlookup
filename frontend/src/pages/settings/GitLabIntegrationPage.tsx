import { useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  GitMerge,
  RefreshCw,
  Save,
  ShieldCheck,
  TestTube,
  XCircle,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import { useGitlabIntegration, testGitlabConnection } from '@/hooks/useGitlabIntegration'
import { gitlabIntegrationService } from '@/services/gitlabIntegrationService'
import {
  DEFAULT_GITLAB_CONFIG,
  type GitLabConfig,
  type GitLabConfigWrite,
  type GitLabConnectionTest,
  type MrCommentMode,
} from '@/types/gitlab'
import { formatCompactDateTime } from '@/utils/formatters'

// Exhaustiveness-checked against the MrCommentMode union: adding a mode to the
// type without a label here is a compile error.
const MR_COMMENT_LABELS: Record<MrCommentMode, string> = {
  off: 'Off — never comment on merge requests',
  failures_only: 'Failures only — comment when tests fail (default)',
  always: 'Always — comment on every merge-request run',
}

/** Narrow a raw <select> value via the label record's keys (no blind cast). */
function isMrCommentMode(value: string): value is MrCommentMode {
  return value in MR_COMMENT_LABELS
}

/** Local form shape — the write payload without the write-only token. */
type FormState = Omit<GitLabConfigWrite, 'token'>

function toFormState(config: GitLabConfig): FormState {
  return {
    enabled: config.enabled,
    base_url: config.base_url,
    project_path: config.project_path,
    mr_comment_mode: config.mr_comment_mode,
    commit_status_enabled: config.commit_status_enabled,
  }
}

/**
 * GitLab integration settings — PMF backlog Epic 3.
 *
 * QA_LEAD+ configures a single GitLab project per TestLookup project. On
 * every ingested test run with a commit SHA, TestLookup posts a commit
 * status (pipeline check) and — when the run carries merge-request context —
 * a sticky summary comment on the MR.
 *
 * The PAT is write-only per the pinned contract: it is never returned;
 * `has_token` is the only signal. When a token is stored the page shows
 * "Token set" plus "Replace token" / "Remove token" affordances; otherwise
 * an input. "Remove token" sends `token: ""` (the contract's clear semantics)
 * on the next save.
 *
 * Project-scoped — in All Projects mode we prompt the user to pick one.
 */
export default function GitLabIntegrationPage() {
  const { canAccessManagement: canEdit } = usePermissions()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = useProjectStore((s) => s.activeProject)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || !activeProjectId

  const projectKey = isAllProjects ? null : activeProjectId
  const { data: config, error, isLoading, mutate } = useGitlabIntegration(projectKey)

  const [form, setForm] = useState<FormState>(toFormState(DEFAULT_GITLAB_CONFIG))
  // Token UX: when a token is stored, the input is hidden behind "Replace
  // token"; `replacing` reveals it. `tokenDraft` is the pending new value.
  // `removingToken` arms the contract's `token: ""` clear on the next save.
  const [replacing, setReplacing] = useState(false)
  const [tokenDraft, setTokenDraft] = useState('')
  const [removingToken, setRemovingToken] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [lastTest, setLastTest] = useState<GitLabConnectionTest | null>(null)
  // Any in-progress edit (field change, token keystroke, armed removal) sets
  // `dirty`; it gates the render-time re-seed below so a background SWR
  // revalidation can never wipe unsaved edits (including a half-typed PAT).
  const [dirty, setDirty] = useState(false)

  // Seed the form from the resolved config using the React-endorsed
  // "adjust state during render" reset pattern (no effect): whenever SWR hands
  // us a new config reference (initial load or post-save mutate), reset the
  // form + token UX to it — unless the user has unsaved edits (`dirty`). SWR
  // returns a stable reference for unchanged data, so this settles after one
  // render. A successful save clears `dirty` and re-seeds explicitly.
  const [seededConfig, setSeededConfig] = useState<GitLabConfig | undefined>(undefined)
  if (config && config !== seededConfig && !dirty) {
    setSeededConfig(config)
    setForm(toFormState(config))
    setReplacing(false)
    setTokenDraft('')
    setRemovingToken(false)
  }

  function edit(patch: Partial<FormState>) {
    setForm((f) => ({ ...f, ...patch }))
    setDirty(true)
  }

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="GitLab Integration"
          subtitle="Per-project GitLab merge-request + commit-status configuration"
          actions={<ExperimentalBadge />}
        />
        <ProjectRequiredEmptyState
          description="GitLab integration settings are configured per project."
        />
      </div>
    )
  }

  if (isLoading) return <LoadingSpinner size="lg" />

  // Failed GET with nothing cached: rendering the DEFAULT-seeded form here
  // would let a single Save silently overwrite a working integration with
  // defaults — so surface the error and withhold the form entirely.
  if (error && !config) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Link to="/settings" className="flex items-center gap-1 hover:text-[var(--color-text)]">
            <ArrowLeft className="h-3 w-3" /> Settings
          </Link>
        </div>
        <PageHeader
          title="GitLab Integration"
          subtitle={`Post commit statuses and MR comments for ${activeProject?.name || 'this project'}`}
          actions={<ExperimentalBadge />}
        />
        <div className="rounded-md border border-[var(--status-failed-bd)]/40 bg-[var(--status-failed-bg)]/10 p-4 text-xs text-[var(--status-failed)] space-y-2">
          <p className="flex items-center gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <strong>Could not load the GitLab integration settings.</strong>
          </p>
          <p>
            The saved configuration could not be fetched, so the form is hidden — saving now could
            overwrite a working configuration with defaults.
          </p>
          <button
            type="button"
            onClick={() => mutate()}
            className="btn-secondary text-xs flex items-center gap-1.5"
          >
            <RefreshCw className="h-3 w-3" /> Retry
          </button>
        </div>
      </div>
    )
  }

  const hasToken = config?.has_token ?? false
  // Only reveal the token input when there is no stored token, or the user
  // clicked "Replace token" — and never while a removal is armed.
  const showTokenInput = !removingToken && (!hasToken || replacing)

  async function save() {
    if (!activeProjectId || isAllProjects) return
    setSaving(true)
    try {
      // Token semantics per the contract: omit → keep the stored PAT,
      // "" → clear it (Remove token), value → set/rotate it.
      const payload: GitLabConfigWrite = { ...form }
      if (removingToken) payload.token = ''
      else if (showTokenInput && tokenDraft) payload.token = tokenDraft
      const saved = await gitlabIntegrationService.update(activeProjectId, payload)
      setSeededConfig(saved)
      setForm(toFormState(saved))
      setTokenDraft('')
      setReplacing(false)
      setRemovingToken(false)
      setDirty(false)
      await mutate(saved, { revalidate: false })
      toast.success('GitLab integration saved')
    } catch (err) {
      // Axios failures: the shared interceptor already toasts the server's
      // error detail — a second generic toast here would only bury it.
      if (!isAxiosError(err)) toast.error(`Failed to save: ${(err as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    if (!activeProjectId || isAllProjects || testing || dirty) return
    setTesting(true)
    try {
      const result = await testGitlabConnection(activeProjectId)
      setLastTest(result)
      if (result.ok) toast.success('Connection OK')
      else toast.error(result.detail)
    } catch (err) {
      // Axios failures already toast via the shared interceptor.
      if (!isAxiosError(err)) toast.error(`Test failed: ${(err as Error).message}`)
    } finally {
      setTesting(false)
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
        title="GitLab Integration"
        subtitle={`Post commit statuses and MR comments for ${activeProject?.name || 'this project'}`}
        actions={<ExperimentalBadge />}
      />

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-[var(--color-accent)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Project</h3>
          {config && (
            <span
              className={`ml-auto text-[10px] px-2 py-0.5 rounded border ${
                config.enabled
                  ? 'border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10'
                  : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
              }`}
            >
              {config.enabled ? 'ENABLED' : 'DISABLED'}
            </span>
          )}
        </div>

        <FieldText
          label="GitLab base URL"
          value={form.base_url}
          onChange={(v) => edit({ base_url: v })}
          disabled={!canEdit}
          placeholder="https://gitlab.com"
          help="For self-managed instances: your GitLab URL (e.g. https://gitlab.corp.com)."
        />

        <FieldText
          label="Project path"
          value={form.project_path}
          onChange={(v) => edit({ project_path: v })}
          disabled={!canEdit}
          placeholder="my-group/my-project"
          help="The group/project path (or nested group/subgroup/project)."
        />

        {/* PAT — write-only per the contract. */}
        {removingToken ? (
          <div className="flex items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1 text-[var(--status-broken)]">
              <AlertTriangle className="h-3.5 w-3.5" /> Token will be removed when you save
            </span>
            <button
              type="button"
              onClick={() => setRemovingToken(false)}
              className="text-[11px] text-[var(--color-text-muted)] hover:underline"
            >
              Cancel — keep the stored token
            </button>
          </div>
        ) : showTokenInput ? (
          <div className="space-y-1">
            <FieldText
              label="Personal / Project Access Token"
              value={tokenDraft}
              onChange={(v) => {
                setTokenDraft(v)
                setDirty(true)
              }}
              disabled={!canEdit}
              placeholder="glpat-..."
              type="password"
              autoComplete="new-password"
              help="Needs the api scope (or read_api + write MR/commit-status). Stored encrypted; never displayed after saving."
            />
            {hasToken && replacing && canEdit && (
              <button
                type="button"
                onClick={() => {
                  setReplacing(false)
                  setTokenDraft('')
                }}
                className="text-[11px] text-[var(--color-text-muted)] hover:underline"
              >
                Cancel — keep the stored token
              </button>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1 text-[var(--status-passed)]">
              <ShieldCheck className="h-3.5 w-3.5" /> Token set
            </span>
            {canEdit && (
              <button
                type="button"
                onClick={() => setReplacing(true)}
                className="text-[var(--color-accent)] hover:underline"
              >
                Replace token
              </button>
            )}
            {canEdit && (
              <button
                type="button"
                onClick={() => {
                  if (
                    window.confirm(
                      'Remove the stored GitLab token? The removal is applied when you save.',
                    )
                  ) {
                    setRemovingToken(true)
                    setDirty(true)
                  }
                }}
                className="text-[var(--status-failed)] hover:underline"
              >
                Remove token
              </button>
            )}
          </div>
        )}

        <label className="text-xs flex items-center gap-2">
          <input
            type="checkbox"
            checked={form.enabled}
            disabled={!canEdit}
            onChange={(e) => edit({ enabled: e.target.checked })}
          />
          <span>Enable GitLab integration for this project</span>
        </label>

        <label className="text-xs flex items-center gap-2">
          <input
            type="checkbox"
            checked={form.commit_status_enabled}
            disabled={!canEdit}
            onChange={(e) => edit({ commit_status_enabled: e.target.checked })}
          />
          <span>Post a commit status (pipeline check) on every ingested run</span>
        </label>

        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">Merge-request comment</span>
          <select
            value={form.mr_comment_mode}
            disabled={!canEdit}
            onChange={(e) => {
              const value = e.target.value
              if (isMrCommentMode(value)) edit({ mr_comment_mode: value })
            }}
            className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          >
            {Object.entries(MR_COMMENT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[var(--color-text-faint)]">
            When a run carries merge-request context (auto-detected in CI),
            TestLookup posts a sticky summary comment on the merge request
            listing newly-failed, known-flaky, and fixed tests. Re-runs update
            the same comment. &quot;Failures only&quot; still updates an
            existing comment when the MR goes green.
          </span>
        </label>

        {lastTest && (
          <div
            className={`rounded-md border p-2 text-xs ${
              lastTest.ok
                ? 'border-[var(--status-passed-bd)]/40 bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)]'
                : 'border-[var(--status-failed-bd)]/40 bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)]'
            }`}
          >
            {lastTest.ok ? (
              <CheckCircle2 className="h-3.5 w-3.5 inline mr-1" />
            ) : (
              <XCircle className="h-3.5 w-3.5 inline mr-1" />
            )}
            {lastTest.detail}
            {lastTest.project_id_resolved && (
              <span className="text-[var(--color-text-faint)] ml-1">
                (resolved project id: {lastTest.project_id_resolved})
              </span>
            )}
          </div>
        )}

        {config?.last_error && (
          <div className="rounded-md border border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 p-2 text-xs text-[var(--status-broken)]">
            <strong>Integration health — last error:</strong> {config.last_error}
            {config.last_error_at && (
              <span className="text-[var(--color-text-faint)] ml-1">
                ({formatCompactDateTime(config.last_error_at)})
              </span>
            )}
          </div>
        )}

        {canEdit ? (
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="btn-primary text-xs flex items-center gap-1.5"
            >
              <Save className="h-3 w-3" /> {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={test}
              disabled={testing || dirty}
              title={
                dirty
                  ? 'Save your changes first — Test uses the saved configuration'
                  : undefined
              }
              className="btn-secondary text-xs flex items-center gap-1.5"
            >
              <TestTube className="h-3 w-3" />
              {testing ? 'Testing…' : 'Test connection'}
            </button>
          </div>
        ) : (
          <p className="text-xs text-[var(--color-text-faint)] pt-1">
            You need the QA Lead role or higher to edit this integration.
          </p>
        )}
      </section>

      <section className="text-xs text-[var(--color-text-muted)] space-y-1">
        <p>
          <strong>How it works.</strong> On every ingested test run with a
          commit SHA, TestLookup posts a commit status to that commit via the
          GitLab API so developers see a pass/fail pipeline check. When the run
          arrives with merge-request context (auto-detected by the SDKs/CLI in
          CI) matching this project, TestLookup upserts a single sticky comment
          on the MR summarizing newly-failed, known-flaky, and fixed tests.
        </p>
        <p>
          <strong>Self-managed.</strong> Point <em>GitLab base URL</em> at your
          own instance for self-managed / self-hosted GitLab.
        </p>
        <p>
          <strong>Offline mode:</strong> when <code>AI_OFFLINE_MODE=true</code>{' '}
          the integration is a silent no-op regardless of the toggle above.
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
  autoComplete,
  help,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  disabled?: boolean
  placeholder?: string
  type?: 'text' | 'password'
  autoComplete?: string
  help?: string
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
        autoComplete={autoComplete}
        className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
      />
      {help && <span className="mt-1 block text-[var(--color-text-faint)]">{help}</span>}
    </label>
  )
}
