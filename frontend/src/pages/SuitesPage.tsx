/**
 * `/suites`: the project's test suites. New suite opens IN THE PAGE (W27b
 * PAN-05), under the page header; a suite's Delete asks in a strip in a
 * full-width row right under that suite's row (it was the browser's native
 * confirmation prompt). Edit (W13 TCL-07) opens the suite's form in that same
 * row: opening one closes the other.
 */
import { Fragment, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight, FolderTree, Pencil, Plus, Star, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { InlineRowSection } from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { SuiteEditForm } from '@/components/suites/SuiteEditForm'
import { helpTopicParam } from '@/components/help/helpTopics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import { refreshSuites, useSuites } from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import type { Project } from '@/types/projects'
import type { TestSuite } from '@/types/suites'
import { formatDateTime, shortAgo } from '@/utils/formatters'
import { useSuiteAggregates, type SuiteAggregate } from './suite/useSuiteAggregates'

/** A run column with no value for this suite: a dash, and why on hover. */
function NoValue({ title }: { title: string }) {
  return (
    <span title={title} className="text-[var(--color-text-muted)]">
      —
    </span>
  )
}

/**
 * The suite's run columns (UX redesign P4), from the per-name aggregates. A
 * suite no run has carried by name (a new or a default catch-all suite) has
 * no row there: every cell is a dash, never a 0 %.
 */
function OwnerCell({ aggregate }: { aggregate: SuiteAggregate }) {
  const owner = aggregate.owner_full_name || aggregate.owner_email
  return (
    <td className="px-4 py-3 whitespace-nowrap text-[var(--color-text-muted)]" data-col="owner">
      {owner ? (
        <span
          className={aggregate.owner_is_fallback ? 'italic' : undefined}
          title={aggregate.owner_is_fallback ? "No owner set on this suite: the project's default QA lead" : undefined}
        >
          {owner}
          {aggregate.owner_is_fallback && ' (project default)'}
        </span>
      ) : (
        <NoValue title="No owner resolved" />
      )}
    </td>
  )
}

function AggregateCells({ aggregate }: { aggregate: SuiteAggregate | undefined }) {
  const none = 'No run has reported this suite'
  if (!aggregate || aggregate.empty) {
    // No row, or an `empty` one (W13 TCL-07: a suite no result reports yet):
    // the four run cells say "no run"; an empty row still names its owner.
    return (
      <>
        <td className="px-4 py-3 text-right"><NoValue title={none} /></td>
        <td className="px-4 py-3"><NoValue title={none} /></td>
        <td className="px-4 py-3 text-right"><NoValue title={none} /></td>
        <td className="px-4 py-3 text-right"><NoValue title={none} /></td>
        {aggregate ? <OwnerCell aggregate={aggregate} /> : <td className="px-4 py-3"><NoValue title="No owner resolved" /></td>}
      </>
    )
  }
  const failing = aggregate.failed_count ?? 0
  return (
    <>
      <td className="px-4 py-3 text-right tabular-nums text-[var(--color-text)]" data-col="pass-rate">
        {aggregate.pass_rate != null ? `${Number(aggregate.pass_rate).toFixed(1)}%` : <NoValue title="No test has a pass or fail result yet (never run, or every latest result skipped)" />}
      </td>
      <td className="px-4 py-3 whitespace-nowrap text-[var(--color-text-muted)]" data-col="last-run">
        {aggregate.last_run_at ? (
          aggregate.last_run_id ? (
            <Link
              to={`/runs/${aggregate.last_run_id}`}
              onClick={(e) => e.stopPropagation()}
              title={formatDateTime(aggregate.last_run_at)}
              className="hover:text-[var(--color-accent)] hover:underline"
            >
              {shortAgo(aggregate.last_run_at)}
            </Link>
          ) : (
            <span title={formatDateTime(aggregate.last_run_at)}>{shortAgo(aggregate.last_run_at)}</span>
          )
        ) : (
          <NoValue title={none} />
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-[var(--color-text-muted)]" data-col="executions">
        {aggregate.total_executions ?? <NoValue title={none} />}
      </td>
      <td
        className={`px-4 py-3 text-right tabular-nums ${failing > 0 ? 'font-medium text-[var(--status-failed)]' : 'text-[var(--color-text-muted)]'}`}
        data-col="failing"
      >
        {failing}
      </td>
      <OwnerCell aggregate={aggregate} />
    </>
  )
}

interface CreateModalProps {
  // Pre-resolved project for single-project mode. When ``null``, the
  // modal renders a project picker and reads its choice from
  // ``projectOptions``. Pairing the two props keeps the single-project
  // path zero-config — callers that already know the project pass
  // ``defaultProjectId`` and never need to touch ``projectOptions``.
  defaultProjectId: string | null
  projectOptions: Project[]
  onClose: () => void
  onCreated: () => void
}

function CreateSuiteModal({
  defaultProjectId,
  projectOptions,
  onClose,
  onCreated,
}: CreateModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  // When ``defaultProjectId`` is supplied (single-project mode) we lock
  // the picker to it. In All-Projects mode the picker starts empty and
  // the user must choose explicitly — there's no sensible default to
  // pre-fill across N projects, and silently picking the alphabetically
  // first one would cause "I created a suite in the wrong place"
  // mistakes that are hard to undo.
  const [pickedProjectId, setPickedProjectId] = useState<string>(defaultProjectId ?? '')
  const showProjectPicker = defaultProjectId === null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    const projectId = defaultProjectId ?? pickedProjectId
    if (!projectId) return
    setSaving(true)
    try {
      await suitesService.create({
        project_id: projectId,
        name: name.trim(),
        description: description.trim() || null,
      })
      const targetName = projectOptions.find((p) => p.id === projectId)?.name
      toast.success(
        targetName
          ? `Suite "${name.trim()}" created in ${targetName}`
          : `Suite "${name.trim()}" created`,
      )
      onCreated()
      onClose()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to create suite'
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  // In the page (W27b PAN-05), right under the page header; its X is labelled `Close`.
  return (
    <InlineFormSection title="New Test Suite" titleId="new-suite-title" onClose={onClose}>
        <form onSubmit={handleSubmit} className="max-w-md space-y-4">
          {showProjectPicker && (
            // All-Projects mode: the page-level scope doesn't pin a
            // single project, so the user must choose one for this
            // suite. Showing only projects the user is a member of (the
            // store-cached list) is the right scope — backend would
            // reject any other id with 403 anyway.
            <div>
              <label htmlFor="suite-project" className="text-sm text-[var(--color-text-muted)]">Project</label>
              <select
                id="suite-project"
                value={pickedProjectId}
                onChange={(e) => setPickedProjectId(e.target.value)}
                required
                className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
              >
                <option value="">Select a project…</option>
                {projectOptions.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              {projectOptions.length === 0 && (
                <p className="mt-1 text-xs text-[var(--status-broken)]">
                  No projects available — you must be a member of at least one project to create a suite.
                </p>
              )}
            </div>
          )}
          <div>
            <label htmlFor="suite-name" className="text-sm text-[var(--color-text-muted)]">Name</label>
            <input
              id="suite-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              minLength={1}
              maxLength={500}
              className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
              placeholder="Regression, Smoke, API Tests..."
            />
          </div>
          <div>
            <label htmlFor="suite-description" className="text-sm text-[var(--color-text-muted)]">Description (optional)</label>
            <textarea
              id="suite-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded px-3 py-1.5 text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                saving ||
                !name.trim() ||
                (showProjectPicker && !pickedProjectId)
              }
              className="btn-primary"
            >
              {saving ? 'Creating…' : 'Create suite'}
            </button>
          </div>
        </form>
    </InlineFormSection>
  )
}

export default function SuitesPage() {
  const navigate = useNavigate()
  const project = useProjectStore((s) => s.activeProject)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // Member-of project list, populated by the store on app boot and
  // refreshed by TopBar / login flows. Reading directly from the store
  // avoids a second projects fetch just for the create-suite picker.
  const accessibleProjects = useProjectStore((s) => s.projects)
  const { canAccessManagement, hasRole } = usePermissions()
  const canEdit = canAccessManagement || hasRole('QA_ENGINEER')
  // Make default and Delete are QA lead on the API (`routers/suites.py`);
  // creating a suite is QA engineer. Delete was offered to a QA engineer and
  // answered 403.
  const canSetDefault = hasRole('QA_LEAD')
  const canDelete = hasRole('QA_LEAD')

  const { data, isLoading, error } = useSuites()
  // The run columns join by suite name, which is only unambiguous inside one
  // project (`useSuiteAggregates`): All-Projects shows the catalog columns only.
  const aggregates = useSuiteAggregates(!isAllProjects)
  const [showCreate, setShowCreate] = useState(false)
  // The suite whose Delete is being asked about (the strip under its row).
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  // The suite whose Edit form is open under its row (W13 TCL-07), `edit:<id>`.
  // It shares the row's place with the Delete strip: opening one closes the
  // other (a half-typed edit asks first).
  const rowSlot = useInlineSlot()
  const openEdit = (suiteId: string) => {
    setConfirmingDelete(null)
    rowSlot.open(`edit:${suiteId}`)
  }
  const askDelete = (suiteId: string) =>
    rowSlot.guard(() => {
      rowSlot.close()
      setConfirmingDelete(suiteId)
    })
  // Create is allowed when the user has the role AND either a specific
  // project is active OR they have at least one accessible project to
  // pick from in All-Projects mode. The second condition saves a
  // failure-after-submit when the user has zero memberships.
  const canCreate =
    canEdit && (!isAllProjects ? !!project : accessibleProjects.length > 0)

  async function handleSetDefault(suite: TestSuite) {
    try {
      await suitesService.setDefault(suite.id)
      toast.success(`"${suite.name}" is now the default suite`)
      refreshSuites()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to set default'
      toast.error(message)
    }
  }

  async function handleDelete(suite: TestSuite) {
    if (suite.is_default) {
      toast.error('The default suite cannot be deleted')
      return
    }
    // Asked by the strip under the suite's row; only its Delete calls this.
    try {
      await suitesService.remove(suite.id)
      toast.success(`Suite "${suite.name}" deleted`)
      refreshSuites()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Delete failed'
      toast.error(message)
    }
  }

  if (!project && !isAllProjects) {
    return <EmptyState title="Select a project" description="Choose a project to view its test suites." />
  }
  if (isLoading) return <LoadingSpinner size="lg" />
  if (error) {
    return (
      <EmptyState
        title="Could not load suites"
        description="Check the backend logs or try again."
      />
    )
  }

  const items = data?.items ?? []
  const showRunColumns = !isAllProjects

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Test Suites"
        subtitle="Project-scoped groupings of test cases. The default suite catches new cases ingested without an explicit suite name."
        helpTopic={helpTopicParam('/suites')}
        actions={
          canCreate ? (
            <button
              onClick={() => setShowCreate(true)}
              className="btn-primary inline-flex items-center gap-1"
            >
              <Plus className="h-4 w-4" /> New suite
            </button>
          ) : null
        }
      />

      {showCreate && (
        <CreateSuiteModal
          // Single-project mode pre-pins the project; All-Projects mode
          // passes ``null`` so the form's picker drives the choice.
          defaultProjectId={!isAllProjects && project ? project.id : null}
          projectOptions={accessibleProjects}
          onClose={() => setShowCreate(false)}
          onCreated={() => refreshSuites()}
        />
      )}

      {items.length === 0 ? (
        <EmptyState
          title="No suites yet"
          description="Ingest a test run or create one manually to get started."
        />
      ) : (
        <div className="space-y-2">
          <div data-primary="" className="overflow-x-auto rounded-lg ring-1 ring-[var(--color-border)]">
            <table className="w-full divide-y divide-[var(--color-border)] text-sm">
              <thead className="bg-[var(--color-bg-secondary)] text-left text-xs uppercase text-[var(--color-text-muted)]">
                <tr>
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">Description</th>
                  <th className="px-4 py-2 font-medium text-right">Test cases</th>
                  {showRunColumns && (
                    <>
                      <th className="px-4 py-2 font-medium text-right" title="Of the suite's tests, the share whose latest result passed">Pass rate</th>
                      <th className="px-4 py-2 font-medium">Last run</th>
                      <th className="px-4 py-2 font-medium text-right" title="Every execution of the suite's tests, all runs">Executions</th>
                      <th className="px-4 py-2 font-medium text-right" title="Tests whose latest result failed">Failing</th>
                      <th className="px-4 py-2 font-medium">Owner</th>
                    </>
                  )}
                  <th className="px-4 py-2 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {items.map((s) => (
                  <Fragment key={s.id}>
                  <tr
                    className="cursor-pointer transition hover:bg-[var(--color-bg-secondary)]"
                    onClick={() => navigate(`/suites/${s.id}`)}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <FolderTree className="h-4 w-4 text-[var(--color-text-muted)]" />
                        {/* One line: "All Tests" broke as "All / Tests" in a squeezed column (P4 baselines). */}
                        <span data-suite-name="" className="whitespace-nowrap font-medium text-[var(--color-text)]">{s.name}</span>
                        {s.is_default && (
                          <span className="inline-flex items-center gap-1 rounded bg-[var(--status-broken-bg)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--status-broken)] ring-1 ring-[var(--status-broken)]/30">
                            <Star className="h-3 w-3" /> Default
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-[var(--color-text-muted)]" title={s.description ?? undefined}>
                      {/* w-0 min-w-full: the description truncates in whatever width the
                          names and the numbers leave it, and never sets the column's width
                          (a truncated cell still sized it to the whole text: the list ran
                          21 px past its card at 1280 px on CI's font). */}
                      <div data-suite-description="" className="w-0 min-w-full max-w-[280px] truncate">
                        {/* An emptied description is stored as "" (TCL-07): a dash too. */}
                        {s.description || <span className="italic">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-[var(--color-text-muted)]">
                      {s.test_case_count ?? '—'}
                    </td>
                    {showRunColumns && <AggregateCells aggregate={aggregates.byName.get(s.name.trim())} />}
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {canEdit && (
                          <button
                            type="button"
                            onClick={() => openEdit(s.id)}
                            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
                            title="Edit"
                            aria-label={`Edit ${s.name}`}
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                        )}
                        {canSetDefault && !s.is_default && (
                          <button
                            onClick={() => handleSetDefault(s)}
                            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--status-broken-bg)]/10 hover:text-[var(--status-broken)]"
                            title="Make default"
                          >
                            <Star className="h-4 w-4" />
                          </button>
                        )}
                        {canDelete && !s.is_default && (
                          <button
                            onClick={() => askDelete(s.id)}
                            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--status-failed-bg)]/10 hover:text-[var(--status-failed)]"
                            title="Delete"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        )}
                        <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />
                      </div>
                    </td>
                  </tr>
                  {canEdit && rowSlot.openKey === `edit:${s.id}` && (
                    <InlineRowSection colSpan={showRunColumns ? 9 : 4} dataAttributes={{ 'data-suite-edit-row': s.id }}>
                      <SuiteEditForm
                        suite={s}
                        slot={rowSlot}
                        onClose={() => rowSlot.close()}
                        onSaved={() => rowSlot.close()}
                      />
                    </InlineRowSection>
                  )}
                  {confirmingDelete === s.id && (
                    <InlineRowSection colSpan={showRunColumns ? 9 : 4}>
                      <InlineConfirm
                        message={<>Delete suite &quot;<strong>{s.name}</strong>&quot;? Test cases must already be moved out.</>}
                        confirmLabel="Delete"
                        tone="danger"
                        onConfirm={() => { setConfirmingDelete(null); void handleDelete(s) }}
                        onCancel={() => setConfirmingDelete(null)}
                      />
                    </InlineRowSection>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {/* The run columns come from a second read: say when it failed (the
              dashes would otherwise read as "never ran"), and why All-Projects
              has none. */}
          {showRunColumns && aggregates.error ? (
            <p data-aggregates-note="" className="text-xs text-[var(--status-failed)]">
              Run columns could not be loaded: pass rate, last run, executions, failing and owner are empty.
            </p>
          ) : !showRunColumns ? (
            <p data-aggregates-note="" className="text-xs text-[var(--color-text-muted)]">
              Select a project to see each suite&apos;s pass rate, last run, executions, failing tests and owner.
            </p>
          ) : null}
        </div>
      )}
    </div>
  )
}
