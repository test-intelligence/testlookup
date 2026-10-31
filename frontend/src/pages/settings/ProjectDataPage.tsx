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
 * Two-step confirmation: clicking a button opens a typed confirmation that
 * requires typing the project name verbatim before the Delete button enables.
 * Matches the GitHub repo-delete UX. The backend re-validates the typed
 * name; the frontend gate is a forcing function against autopilot
 * clicks, not a security check.
 *
 * W27b PAN-06: the typed confirmation renders IN THE PAGE, inside the Danger
 * zone right under the row whose button opened it (it was a modal dialog),
 * one at a time; it keeps its field, its exact-match rule and `aria-disabled`.
 *
 * W24 ADM-05 (phase 1): two cards ABOVE the Danger zone — Export settings and
 * catalog (QA lead or ADMIN with the project) and Import as a new project
 * (ADMIN). The page reads the newest export / import and hands each card its
 * read's error; a QA lead sees the Export card only (no Import card, no
 * Danger-zone control, no imports read). The reset flow is unchanged.
 */
import { Fragment, useMemo, useState } from 'react'
import { AlertTriangle, Loader2, ShieldAlert, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  projectsService,
  type ProjectResetMode,
  type ProjectResetResponse,
} from '@/services/projectsService'
import { helpTopicParam } from '@/components/help/helpTopics'
import { formatNumber } from '@/utils/formatters'
import { useMyImports, useProjectExports } from '@/hooks/useProjectDataJobs'
import ProjectExportCard from './ProjectExportCard'
import ProjectImportCard from './ProjectImportCard'
import { newestJob, PAGE_SUBTITLE, UI_SENTENCES } from './projectDataModel'

const HELP_TOPIC = helpTopicParam('/settings/project-data')

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
      'test_suites, canonical_test_cases',
      'suite_memberships, suite_membership_events',
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
  /** W27b: the Danger zone's slot (one typed confirmation at a time; typing marks it dirty). */
  slot?: InlineSlot
}

function ResetConfirmModal({ spec, projectId, projectName, onClose, onSuccess, slot }: ConfirmModalProps) {
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
    <InlineFormSection
      title={
        <span className="inline-flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-[var(--status-failed)]" aria-hidden="true" />
          {spec.title}
        </span>
      }
      titleId="project-data-confirm-title"
      onClose={onClose}
      canClose={!submitting}
      slot={slot}
      footer={
        <>
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
            className="btn-danger inline-flex items-center gap-1.5"
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
        </>
      }
    >
      <div className="max-w-2xl">
        <p className="text-sm text-[var(--color-text-secondary)]">{spec.blurb}</p>

        <div className="mt-4 rounded border border-[var(--status-failed-bd)]/40 bg-[var(--status-failed-bg)]/10 p-3 text-xs">
          <p className="font-semibold text-[var(--status-failed)]">This will permanently delete:</p>
          <ul className="mt-1 ml-4 list-disc space-y-0.5 text-[var(--status-failed)]/90">
            {spec.deletes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-3 font-semibold text-[var(--status-passed)]">Will be kept:</p>
          <ul className="mt-1 ml-4 list-disc space-y-0.5 text-[var(--status-passed)]/90">
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
            className="mt-1 w-full max-w-md rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </div>
      </div>
    </InlineFormSection>
  )
}

export default function ProjectDataPage() {
  const project = useProjectStore((s) => s.activeProject)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { isAdmin, canAccessManagement } = usePermissions()
  // W24: export = QA lead or ADMIN with a project selected (the route's rule); import = ADMIN.
  const canSeePage = Boolean(isAdmin || canAccessManagement)
  const canExport = Boolean(canSeePage && project && !isAllProjects)
  const canImport = Boolean(isAdmin)

  // W27b: one typed confirmation open in the Danger zone, under its row (the key is the mode).
  const slot = useInlineSlot<ProjectResetMode>()
  const closeConfirm = useSlotCloser(slot)
  const [lastResult, setLastResult] = useState<ProjectResetResponse | null>(null)

  const projectName = project?.name ?? ''
  const projectId = project?.id ?? ''
  // The newest export of the project and the caller's newest import (no read the role may not make).
  const exportsRead = useProjectExports(canExport ? projectId : null)
  const importsRead = useMyImports(canImport)

  const canReset = useMemo(
    () => Boolean(isAdmin && project && !isAllProjects),
    [isAdmin, project, isAllProjects],
  )

  if (!canSeePage) {
    return <EmptyState title={UI_SENTENCES.below_qa_lead_title} description={UI_SENTENCES.below_qa_lead_body} />
  }

  // No form fields here (two actions behind a typed confirmation), so no
  // second column (UX redesign P5): only the max-w-3xl cap goes.
  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Project Data"
        subtitle={PAGE_SUBTITLE}
        helpTopic={HELP_TOPIC}
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
          <div className="rounded border border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-3 py-2 text-sm text-[var(--status-broken)]">
            Select a specific project from the picker — reset is per-project and not available in
            All-Projects mode.
          </div>
        )}
      </section>

      {/* Last result */}
      {lastResult && (
        <section className="card border-[var(--status-passed-bd)]/40 bg-[var(--status-passed-bg)]/10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--status-passed)]">
            Reset complete ({lastResult.mode})
          </h2>
          <ul className="mt-2 ml-4 list-disc space-y-0.5 text-xs text-[var(--status-passed)]/90">
            {Object.entries(lastResult.deleted).map(([table, count]) => (
              <li key={table}>
                <span className="font-mono">{table}</span>: {formatNumber(count)} row
                {count === 1 ? '' : 's'} deleted
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* W24: Export (QA lead / ADMIN with the project) and Import (ADMIN), above the Danger zone. */}
      {(canExport || canImport) && (
        <div className="flex flex-col gap-4" data-project-transfer="">
          {canExport && (
            <ProjectExportCard
              key={projectId}
              loadError={exportsRead.error}
              projectId={projectId}
              latest={newestJob(exportsRead.data)}
              onChanged={exportsRead.mutate}
            />
          )}
          {canImport && (
            <ProjectImportCard
              loadError={importsRead.error}
              latest={newestJob(importsRead.data)}
              onChanged={importsRead.mutate}
            />
          )}
        </div>
      )}

      {/* Danger zone (ADMIN) */}
      {isAdmin && (
        <section
          className="card space-y-4 border-[var(--status-failed-bd)]/40"
          aria-labelledby="danger-zone-heading"
        >
          <header className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--status-failed)]">
            <AlertTriangle className="h-4 w-4" />
            <h2 id="danger-zone-heading">Danger zone</h2>
          </header>
          <p className="text-xs text-[var(--color-text-muted)]">
            These actions cannot be undone. A typed-name confirmation guards every reset; the backend
            re-validates and writes a settings_audit_log entry for every successful run.
          </p>

          <div className="divide-y divide-[var(--color-border)] rounded border border-[var(--status-failed-bd)]/30">
            {MODE_SPECS.map((spec) => (
              <Fragment key={spec.mode}>
              <div className="flex items-start justify-between gap-4 p-4">
                <div className="min-w-0">
                  <h3 className="text-sm font-medium text-[var(--color-text)]">{spec.title}</h3>
                  <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">{spec.blurb}</p>
                </div>
                <button
                  type="button"
                  onClick={() => slot.open(spec.mode)}
                  disabled={!canReset}
                  className="inline-flex flex-shrink-0 items-center gap-1.5 rounded border border-[var(--status-failed-bd)]/60 bg-[var(--status-failed-bg)]/30 px-3 py-1.5 text-xs font-medium text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/60 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Trash2 className="h-3.5 w-3.5" /> {spec.buttonLabel}
                </button>
              </div>
              {/* W27b: the typed confirmation, right under the row whose button opened it. */}
              {slot.openKey === spec.mode && project && !isAllProjects && (
                <div data-in-page-row="" className="p-4">
                  <ResetConfirmModal
                    spec={spec}
                    projectId={projectId}
                    projectName={projectName}
                    slot={slot}
                    onClose={() => closeConfirm(spec.mode)}
                    onSuccess={(resp) => {
                      setLastResult(resp)
                      closeConfirm(spec.mode)
                      toast.success(
                        `Reset complete: ${Object.values(resp.deleted).reduce((a, b) => a + b, 0)} rows deleted`,
                      )
                    }}
                  />
                </div>
              )}
              </Fragment>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
