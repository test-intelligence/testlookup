import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, FolderTree, Plus, Star, Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import { refreshSuites, useSuites } from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import type { Project } from '@/types/projects'
import type { TestSuite } from '@/types/suites'

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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-[var(--color-bg)] p-6 ring-1 ring-[var(--color-border)]">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">New Test Suite</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
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
              className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? 'Creating…' : 'Create suite'}
            </button>
          </div>
        </form>
      </div>
    </div>
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
  const canSetDefault = hasRole('QA_LEAD')

  const { data, isLoading, error } = useSuites()
  const [showCreate, setShowCreate] = useState(false)
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
    if (!window.confirm(`Delete suite "${suite.name}"? Test cases must already be moved out.`)) {
      return
    }
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
        title="Failed to load suites"
        description="Check the backend logs or try again."
      />
    )
  }

  const items = data?.items ?? []

  return (
    <>
      <PageHeader
        title="Test Suites"
        subtitle="Project-scoped groupings of test cases. The default suite catches new cases ingested without an explicit suite name."
        actions={
          canCreate ? (
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1 rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white"
            >
              <Plus className="h-4 w-4" /> New suite
            </button>
          ) : null
        }
      />

      {items.length === 0 ? (
        <EmptyState
          title="No suites yet"
          description="Ingest a test run or create one manually to get started."
        />
      ) : (
        <div className="overflow-hidden rounded-lg ring-1 ring-[var(--color-border)]">
          <table className="w-full divide-y divide-[var(--color-border)] text-sm">
            <thead className="bg-[var(--color-bg-secondary)] text-left text-xs uppercase text-[var(--color-text-muted)]">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium text-right">Test cases</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {items.map((s) => (
                <tr
                  key={s.id}
                  className="cursor-pointer transition hover:bg-[var(--color-bg-secondary)]"
                  onClick={() => navigate(`/suites/${s.id}`)}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FolderTree className="h-4 w-4 text-[var(--color-text-muted)]" />
                      <span className="font-medium text-[var(--color-text)]">{s.name}</span>
                      {s.is_default && (
                        <span className="inline-flex items-center gap-1 rounded bg-[var(--status-broken-bg)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--status-broken)] ring-1 ring-[var(--status-broken)]/30">
                          <Star className="h-3 w-3" /> Default
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)]">
                    {s.description ?? <span className="italic">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-[var(--color-text-muted)]">
                    {s.test_case_count ?? '—'}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      {canSetDefault && !s.is_default && (
                        <button
                          onClick={() => handleSetDefault(s)}
                          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--status-broken-bg)]/10 hover:text-[var(--status-broken)]"
                          title="Make default"
                        >
                          <Star className="h-4 w-4" />
                        </button>
                      )}
                      {canEdit && !s.is_default && (
                        <button
                          onClick={() => handleDelete(s)}
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
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <CreateSuiteModal
          // Single-project mode pre-pins the project; All-Projects mode
          // passes ``null`` so the modal's picker drives the choice.
          defaultProjectId={!isAllProjects && project ? project.id : null}
          projectOptions={accessibleProjects}
          onClose={() => setShowCreate(false)}
          onCreated={() => refreshSuites()}
        />
      )}
    </>
  )
}
