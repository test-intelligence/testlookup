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
import type { TestSuite } from '@/types/suites'

interface CreateModalProps {
  projectId: string
  onClose: () => void
  onCreated: () => void
}

function CreateSuiteModal({ projectId, onClose, onCreated }: CreateModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      await suitesService.create({
        project_id: projectId,
        name: name.trim(),
        description: description.trim() || null,
      })
      toast.success(`Suite "${name.trim()}" created`)
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
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">Name</label>
            <input
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
            <label className="text-sm text-[var(--color-text-muted)]">Description (optional)</label>
            <textarea
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
              disabled={saving || !name.trim()}
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
  const { canAccessManagement, hasRole } = usePermissions()
  const canEdit = canAccessManagement || hasRole('QA_ENGINEER')
  const canSetDefault = hasRole('QA_LEAD')

  const { data, isLoading, error } = useSuites()
  const [showCreate, setShowCreate] = useState(false)

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
          canEdit && !isAllProjects && project ? (
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
                        <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400 ring-1 ring-amber-500/30">
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
                          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-amber-500/10 hover:text-amber-400"
                          title="Make default"
                        >
                          <Star className="h-4 w-4" />
                        </button>
                      )}
                      {canEdit && !s.is_default && (
                        <button
                          onClick={() => handleDelete(s)}
                          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-red-500/10 hover:text-red-400"
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

      {showCreate && project && (
        <CreateSuiteModal
          projectId={project.id}
          onClose={() => setShowCreate(false)}
          onCreated={() => refreshSuites()}
        />
      )}
    </>
  )
}
