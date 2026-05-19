/**
 * Project Data Management — ADMIN-only destructive controls.
 *
 * Two reset modes:
 *   • runs  — drops every TestRun (+ cascaded test_cases, ai_analyses,
 *             clusters, agent pipeline rows). Keeps the project's
 *             test_suites / canonical_test_cases catalog so the next
 *             ingest can re-link.
 *   • full  — runs the ``runs`` deletes plus every project-scoped
 *             catalog/RAG/policy/baseline table. Returns the project
 *             to a green-field state.
 *
 * Two-step confirmation: clicking a button opens a modal that requires
 * typing the project name verbatim before the Delete button enables.
 * Matches the GitHub repo-delete UX. The backend re-validates the typed
 * name; the frontend gate is a forcing function against autopilot
 * clicks, not a security check.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, Loader2, ShieldAlert, Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  projectsService,
  type ProjectResetMode,
  type ProjectResetResponse,
} from '@/services/projectsService'

interface ModeSpec {
  mode: ProjectResetMode
  title: string
  buttonLabel: string
  blurb: string
  deletes: string[]
  keeps: string[]
}

const MODE_SPECS: ModeSpec[] = [
  {
    mode: 'runs',
    title: 'Delete test runs',
    buttonLabel: 'Delete test runs',
    blurb:
      'Drops every TestRun in this project. Cascades to test cases, AI analyses, failure clusters, and agent pipeline rows. The project’s suites and canonical test catalog are kept so the next ingest re-links cleanly.',
    deletes: [
      'test_runs',
      'test_cases (CASCADE)',
      'ai_analysis (CASCADE)',
      'failure_clusters (CASCADE)',
      'agent_pipeline_runs (CASCADE)',
      'release_decisions (CASCADE)',
    ],
    keeps: [
      'test_suites and the canonical test catalog',
      'releases, policies, perf baselines',
      'managed test cases (RAG), test plans, strategies, knowledge sources',
    ],
  },
  {
    mode: 'full',
    title: 'Reset project to fresh state',
    buttonLabel: 'Wipe project data',
    blurb:
      'Runs the test-runs delete and then also wipes the project’s catalog, RAG-authored data, releases, policies, and baselines. The project, its members, API keys, and AI/SSO config survive — everything else is reset.',
    deletes: [
      'Everything in “Delete test runs”',
      'test_suites, canonical_test_cases, suite_memberships',
      'releases, release_gate_policies, perf_baselines',
      'flaky_quarantine_requests',
      'managed_test_cases, test_plans, test_strategies',
      'knowledge_sources (cascades to chunks + batches)',
    ],
    keeps: [
      'Project row + members + API keys',
      'AI configuration, SSO configuration',
      'Feature flag overrides for this project',
    ],
  },
]

interface ConfirmModalProps {
  spec: ModeSpec
  projectId: string
  projectName: string
  onClose: () => void
  onSuccess: (resp: ProjectResetResponse) => void
}

function ResetConfirmModal({ spec, projectId, projectName, onClose, onSuccess }: ConfirmModalProps) {
  const [typed, setTyped] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const matches = typed === projectName

  async function handleConfirm() {
    if (!matches || submitting) return
    setSubmitting(true)
    try {
      const resp = await projectsService.reset(projectId, {
        mode: spec.mode,
        confirmation_name: typed,
      })
      onSuccess(resp)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Reset failed'
      toast.error(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="w-full max-w-lg rounded-lg border border-red-700/50 bg-[var(--color-bg)] p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-red-400" />
            <h2 className="text-lg font-semibold text-[var(--color-text)]">{spec.title}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">{spec.blurb}</p>

        <div className="mt-4 rounded border border-red-700/40 bg-red-900/10 p-3 text-xs">
          <p className="font-semibold text-red-300">This will permanently delete:</p>
          <ul className="mt-1 ml-4 list-disc space-y-0.5 text-red-200/90">
            {spec.deletes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-3 font-semibold text-emerald-300">Will be kept:</p>
          <ul className="mt-1 ml-4 list-disc space-y-0.5 text-emerald-200/90">
            {spec.keeps.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>

        <div className="mt-4">
          <label htmlFor="reset-confirm-input" className="block text-xs text-[var(--color-text-muted)]">
            Type <code className="rounded bg-[var(--color-bg-secondary)] px-1 py-0.5 font-mono text-[var(--color-text)]">{projectName}</code> to confirm.
          </label>
          <input
            id="reset-confirm-input"
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={submitting}
            placeholder={projectName}
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!matches || submitting}
            aria-disabled={!matches || submitting}
            className="inline-flex items-center gap-1.5 rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-red-900/40 disabled:text-red-200/60"
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Deleting…
              </>
            ) : (
              <>
                <Trash2 className="h-3.5 w-3.5" /> {spec.buttonLabel}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function ProjectDataPage() {
  const project = useProjectStore((s) => s.activeProject)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { isAdmin } = usePermissions()

  const [activeSpec, setActiveSpec] = useState<ModeSpec | null>(null)
  const [lastResult, setLastResult] = useState<ProjectResetResponse | null>(null)

  const projectName = project?.name ?? ''
  const projectId = project?.id ?? ''

  const canReset = useMemo(
    () => Boolean(isAdmin && project && !isAllProjects),
    [isAdmin, project, isAllProjects],
  )

  if (!isAdmin) {
    return (
      <EmptyState
        title="Admin access required"
        description="Project data reset is restricted to platform administrators."
      />
    )
  }

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Project Data"
        subtitle="Reset destructive project data when you need a clean slate. ADMIN only."
      />

      {/* Context card */}
      <section className="card space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
          <ShieldAlert className="h-4 w-4 text-[var(--color-text-muted)]" />
          Active project
        </h2>
        {project && !isAllProjects ? (
          <div className="text-sm">
            <div className="text-[var(--color-text)]">{project.name}</div>
            <div className="font-mono text-xs text-[var(--color-text-muted)]">{project.id}</div>
          </div>
        ) : (
          <div className="rounded border border-amber-700/40 bg-amber-900/10 px-3 py-2 text-sm text-amber-300">
            Select a specific project from the picker — reset is per-project and not available in
            All-Projects mode.
          </div>
        )}
      </section>

      {/* Last result */}
      {lastResult && (
        <section className="card border-emerald-700/40 bg-emerald-900/10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-emerald-200">
            Reset complete ({lastResult.mode})
          </h2>
          <ul className="mt-2 ml-4 list-disc space-y-0.5 text-xs text-emerald-100/90">
            {Object.entries(lastResult.deleted).map(([table, count]) => (
              <li key={table}>
                <span className="font-mono">{table}</span>: {count.toLocaleString()} row
                {count === 1 ? '' : 's'} deleted
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Danger zone */}
      <section
        className="card space-y-4 border-red-700/40"
        aria-labelledby="danger-zone-heading"
      >
        <header className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-red-300">
          <AlertTriangle className="h-4 w-4" />
          <h2 id="danger-zone-heading">Danger zone</h2>
        </header>
        <p className="text-xs text-[var(--color-text-muted)]">
          These actions cannot be undone. A typed-name confirmation guards every reset; the backend
          re-validates and writes a settings_audit_log entry for every successful run.
        </p>

        <div className="divide-y divide-[var(--color-border)] rounded border border-red-700/30">
          {MODE_SPECS.map((spec) => (
            <div key={spec.mode} className="flex items-start justify-between gap-4 p-4">
              <div className="min-w-0">
                <h3 className="text-sm font-medium text-[var(--color-text)]">{spec.title}</h3>
                <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">{spec.blurb}</p>
              </div>
              <button
                type="button"
                onClick={() => setActiveSpec(spec)}
                disabled={!canReset}
                className="inline-flex flex-shrink-0 items-center gap-1.5 rounded border border-red-600/60 bg-red-900/30 px-3 py-1.5 text-xs font-medium text-red-200 hover:bg-red-900/60 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Trash2 className="h-3.5 w-3.5" /> {spec.buttonLabel}
              </button>
            </div>
          ))}
        </div>
      </section>

      {activeSpec && project && !isAllProjects && (
        <ResetConfirmModal
          spec={activeSpec}
          projectId={projectId}
          projectName={projectName}
          onClose={() => setActiveSpec(null)}
          onSuccess={(resp) => {
            setLastResult(resp)
            setActiveSpec(null)
            toast.success(
              `Reset complete: ${Object.values(resp.deleted).reduce((a, b) => a + b, 0)} rows deleted`,
            )
          }}
        />
      )}
    </div>
  )
}
