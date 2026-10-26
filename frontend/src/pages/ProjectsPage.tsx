/**
 * /projects: the project cards.
 *
 * W27b PAN-05: the forms render IN THE PAGE (no modal, no native confirm):
 * New Project opens under the page header, a card's Edit opens `Edit Project`
 * as a full-width row of the cards grid right after that card, one project
 * form at a time; a card's Archive asks in that same full-width row (contract
 * D-20's fallback: the card is too narrow for the strip's buttons with 40 px to
 * spare). The card body's click, which selects the project, is never an
 * ancestor of either.
 */
import { Fragment, useState, useEffect, useCallback } from 'react'
import { Edit3, FlaskConical, Plus, Trash2 } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'
import { projectsService } from '@/services/projectsService'
import { useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import type { Project, ProjectUpdate } from '@/types/projects'
import { formatDay, fromNow } from '@/utils/formatters'
import { fieldErrorsFrom } from '@/utils/fieldErrors'
import toast from 'react-hot-toast'
import { helpTopicParam } from '@/components/help/helpTopics'
import {
  EDIT_PROJECT_ERROR_FIELDS,
  NEW_PROJECT_ERROR_FIELDS,
  OCP_NAMESPACE_HELP,
  SPLUNK_INDEX_HELP,
  carriesValidatedIntegrationField,
  saveFailureMessage,
} from './projectIntegrationFields'

const HELP_TOPIC = helpTopicParam('/projects')

interface NewProjectForm {
  name: string
  slug: string
  description: string
  jira_project_key: string
  ocp_namespace: string
}

const EMPTY_FORM: NewProjectForm = {
  name: '', slug: '', description: '', jira_project_key: '', ocp_namespace: '',
}

function slugify(s: string) {
  return s.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '').slice(0, 100)
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return ''
  return iso.slice(0, 10)
}

/** W10: the server's 422 sentence for one field, under its input (the input names it in aria-describedby). */
function FieldError({ id, field, message }: { id: string; field: string; message?: string }) {
  if (!message) return null
  return <p id={id} role="alert" data-field-error={field} className="text-xs text-[var(--ui-danger-ink)] mt-1">{message}</p>
}

/** The ids an input is described by: its help line (always) and its error line (when shown). */
function describedBy(...ids: Array<string | false | null | undefined>): string | undefined {
  const list = ids.filter(Boolean)
  return list.length ? list.join(' ') : undefined
}

/** A copy of `errors` without `field` (the reader is editing it again). */
function withoutError(errors: Record<string, string>, field: string): Record<string, string> {
  if (!(field in errors)) return errors
  const next = { ...errors }
  delete next[field]
  return next
}

type ProjectForm = 'new' | `edit:${string}`

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  // W27b: one project form at a time (New Project under the header, or one card's Edit after its card).
  const slot = useInlineSlot<ProjectForm>()
  const closeForm = useSlotCloser(slot)
  // A card's Archive question, inside that card (one at a time; it sends nothing until its own button).
  const [confirmingArchive, setConfirmingArchive] = useState<string | null>(null)
  const [form, setForm] = useState<NewProjectForm>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  // W10: a field-level 422 on create, shown under its field.
  const [newErrors, setNewErrors] = useState<Record<string, string>>({})
  const setActiveProject = useProjectStore(s => s.setActiveProject)
  const refreshProjects = useProjectStore(s => s.refreshProjects)
  const { isAdmin, isQaLead } = usePermissions()
  const canEdit = isAdmin || isQaLead

  const load = useCallback(
    () => refreshProjects().then(setProjects).catch(() => {}),
    [refreshProjects],
  )
  useEffect(() => {
    void load()
  }, [load])

  const handleNameChange = (name: string) => {
    setForm(f => ({ ...f, name, slug: slugify(name) }))
    setNewErrors(e => withoutError(withoutError(e, 'name'), 'slug'))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim() || !form.slug.trim()) return
    setSaving(true)
    setNewErrors({})
    try {
      const payload: Record<string, string> = { name: form.name, slug: form.slug }
      if (form.description) payload.description = form.description
      if (form.jira_project_key) payload.jira_project_key = form.jira_project_key
      if (form.ocp_namespace) payload.ocp_namespace = form.ocp_namespace
      const created = await projectsService.create(payload)
      toast.success('Project created')
      closeForm('new')
      setForm(EMPTY_FORM)
      // Refresh the shared store so the TopBar dropdown updates immediately,
      // then make the new project the active selection.
      await load()
      if (created?.id) setActiveProject(created)
    } catch (err) {
      // W10 INT-03: a field-level 422 (an OpenShift namespace that is not a DNS label) shows under its field.
      const errors = fieldErrorsFrom(err, NEW_PROJECT_ERROR_FIELDS)
      if (errors) setNewErrors(errors)
      else toast.error('Failed to create project')
    } finally {
      setSaving(false)
    }
  }

  // W17 ADM-04: an administrator can switch to the archived projects and restore one.
  const [view, setView] = useState<'active' | 'archived'>('active')

  const handleDelete = async (p: Project) => {
    try {
      await projectsService.delete(p.id)
      toast.success('Project archived')
      load()
    } catch {
      toast.error('Failed to archive project')
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader compact title="Projects" subtitle="Manage test projects and their integrations"
        helpTopic={HELP_TOPIC}
        actions={canEdit ? (
          <button className="btn-primary flex items-center gap-2 text-sm"
            onClick={() => {
              // In the page the button stays reachable while the form is open: never wipe what is typed there.
              if (slot.openKey === 'new') return
              setForm(EMPTY_FORM)
              setNewErrors({})
              slot.open('new')
            }}>
            <Plus className="h-4 w-4" /> New Project
          </button>
        ) : undefined}
      />

      {/* W27b: New Project, in the page right under the header (it was a modal dialog). */}
      {slot.openKey === 'new' && (
        <InlineFormSection
          title="New Project"
          titleId="new-project-title"
          closeLabel="Close new project dialog"
          onClose={() => closeForm('new')}
          canClose={!saving}
          slot={slot}
          onSubmit={handleSubmit}
          footer={
            <>
              <button type="button" className="btn-secondary" onClick={() => closeForm('new')}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? 'Creating…' : 'Create Project'}
              </button>
            </>
          }
        >
          <div className="max-w-2xl space-y-4">
            <div>
              <label htmlFor="np-name" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Project Name *</label>
              <input id="np-name" type="text" className="input" placeholder="e.g. Payment Gateway API"
                value={form.name} onChange={e => handleNameChange(e.target.value)} required autoFocus
                aria-describedby={describedBy(newErrors.name && 'np-name-error')} />
              <FieldError id="np-name-error" field="name" message={newErrors.name} />
            </div>
            <div>
              <label htmlFor="np-slug" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Slug *</label>
              <input id="np-slug" type="text" className="input font-mono text-sm" placeholder="e.g. payment-gateway-api"
                value={form.slug} onChange={e => { setForm(f => ({ ...f, slug: slugify(e.target.value) })); setNewErrors(x => withoutError(x, 'slug')) }} required
                aria-describedby={describedBy(newErrors.slug && 'np-slug-error')} />
              <FieldError id="np-slug-error" field="slug" message={newErrors.slug} />
            </div>
            <div>
              <label htmlFor="np-desc" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Description</label>
              <textarea id="np-desc" className="input resize-none" rows={2} placeholder="Optional"
                value={form.description} onChange={e => { setForm(f => ({ ...f, description: e.target.value })); setNewErrors(x => withoutError(x, 'description')) }}
                aria-describedby={describedBy(newErrors.description && 'np-desc-error')} />
              <FieldError id="np-desc-error" field="description" message={newErrors.description} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="np-jira" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Jira Project Key</label>
                <input id="np-jira" type="text" className="input font-mono text-sm" placeholder="e.g. PAY"
                  value={form.jira_project_key} onChange={e => { setForm(f => ({ ...f, jira_project_key: e.target.value.toUpperCase() })); setNewErrors(x => withoutError(x, 'jira_project_key')) }}
                  aria-describedby={describedBy(newErrors.jira_project_key && 'np-jira-error')} />
                <FieldError id="np-jira-error" field="jira_project_key" message={newErrors.jira_project_key} />
              </div>
              <div className="min-w-0">
                <label htmlFor="np-ocp" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">OCP Namespace</label>
                <input id="np-ocp" type="text" className="input text-sm" placeholder="e.g. qa-testing"
                  value={form.ocp_namespace} onChange={e => { setForm(f => ({ ...f, ocp_namespace: e.target.value })); setNewErrors(x => withoutError(x, 'ocp_namespace')) }}
                  aria-describedby={describedBy(newErrors.ocp_namespace && 'np-ocp-error')} />
                <FieldError id="np-ocp-error" field="ocp_namespace" message={newErrors.ocp_namespace} />
              </div>
            </div>
          </div>
        </InlineFormSection>
      )}

      {isAdmin && (
        <div className="flex gap-1" role="group" aria-label="Which projects">
          {(['active', 'archived'] as const).map(v => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={view === v ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
            >
              {v === 'active' ? 'Active' : 'Archived'}
            </button>
          ))}
        </div>
      )}

      {isAdmin && view === 'archived' ? (
        <ArchivedProjects onRestored={() => { void load() }} />
      ) : (
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {projects.map(p => {
          const editKey: ProjectForm = `edit:${p.id}`
          return (
          <Fragment key={p.id}>
          <div className="card hover:border-[var(--color-border-light)] transition-colors group relative">
            <div className="cursor-pointer" onClick={() => setActiveProject(p)}>
              <div className="flex items-start gap-3">
                <div className="p-2.5 bg-[var(--color-bg-hover)] rounded-xl">
                  <FlaskConical className="h-5 w-5 text-[var(--color-text)]" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-[var(--color-text)] group-hover:text-[var(--color-text-secondary)] transition-colors">{p.name}</p>
                  <p className="text-xs text-[var(--color-text-muted)] font-mono mt-0.5">{p.slug}</p>
                  {p.description && <p className="text-sm text-[var(--color-text-muted)] mt-2 line-clamp-2">{p.description}</p>}
                  <div className="flex flex-wrap gap-3 mt-3 text-xs text-[var(--color-text-muted)]">
                    {p.jira_project_key && <span>Jira: {p.jira_project_key}</span>}
                    {p.ocp_namespace && <span>OCP: {p.ocp_namespace}</span>}
                    {p.start_date && <span>Start: {fmtDate(p.start_date)}</span>}
                    {p.end_date && <span>End: {fmtDate(p.end_date)}</span>}
                  </div>
                  {p.tags && p.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {p.tags.map(t => (
                        <span key={t} className="text-[10px] px-1.5 py-0.5 bg-[var(--color-bg-hover)] text-[var(--color-text)] rounded">{t}</span>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-[var(--color-text-secondary)] mt-2">Created {fromNow(p.created_at)}</p>
                </div>
              </div>
            </div>
            {canEdit && (
              <div className="absolute top-3 right-3 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={(e) => { e.stopPropagation(); slot.open(editKey) }}
                  className="p-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors" title="Edit">
                  <Edit3 className="h-3.5 w-3.5" />
                </button>
                <button onClick={(e) => { e.stopPropagation(); setConfirmingArchive(p.id) }}
                  className="p-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded text-[var(--status-failed)] hover:text-[var(--status-failed)] transition-colors" title="Archive">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>
          {/* W27b: a full-width row of the grid right after the card holds what its controls open: the Archive
              question and Edit Project. (Contract D-20's fallback: inside the ≈ 199 px card, Cancel + Archive left
              21 px of one-line slack at 1280 on Windows fonts, under the 40 px rule; CI's fonts are wider.) Neither
              is inside the select-on-click card body. */}
          {(confirmingArchive === p.id || slot.openKey === editKey) && (
            <div data-in-page-row="" className="col-span-full space-y-3">
              {confirmingArchive === p.id && (
                <InlineConfirm
                  message={<>Archive project &quot;<strong>{p.name}</strong>&quot;? It will be hidden but not deleted.</>}
                  confirmLabel="Archive"
                  tone="danger"
                  onConfirm={() => { setConfirmingArchive(null); void handleDelete(p) }}
                  onCancel={() => setConfirmingArchive(null)}
                />
              )}
              {slot.openKey === editKey && (
                <EditProjectModal
                  project={p}
                  slot={slot}
                  onClose={() => closeForm(editKey)}
                  onSaved={() => { closeForm(editKey); load() }}
                />
              )}
            </div>
          )}
          </Fragment>
          )
        })}

        {projects.length === 0 && (
          <div className="col-span-full flex flex-col items-center py-16 text-[var(--color-text-muted)]">
            <FlaskConical className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
            <p className="font-medium">No projects yet</p>
            <p className="text-sm mt-1">Create your first project to start ingesting test reports</p>
          </div>
        )}
      </div>
      )}

    </div>
  )
}

/** W17 ADM-04: the archived projects (ADMIN), each with when / by whom and Restore. */
function ArchivedProjects({ onRestored }: { onRestored: () => void }) {
  const [rows, setRows] = useState<Project[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [asking, setAsking] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const read = useCallback(
    () => projectsService.listWithArchived()
      .then(all => { setFailed(false); setRows((Array.isArray(all) ? all : []).filter(p => p.is_active === false)) })
      .catch(() => setFailed(true)),
    [],
  )
  useEffect(() => { void read() }, [read])

  const restore = async (p: Project) => {
    setBusy(true)
    try {
      const answer = await projectsService.restore(p.id)
      const n = answer?.api_keys_still_revoked ?? 0
      toast.success(`${p.name} restored. ${n} API key${n === 1 ? '' : 's'} stay revoked.`)
      setAsking(null)
      void read()
      onRestored()
    } catch {
      toast.error(`Could not restore ${p.name}.`)
    } finally {
      setBusy(false)
    }
  }

  if (failed) return <p className="text-sm text-[var(--ui-danger-ink)]">Could not load the archived projects.</p>
  if (rows === null) return <p className="text-sm text-[var(--color-text-muted)]">Loading archived projects…</p>
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-text-muted)]" data-testid="no-archived-projects">No archived projects.</p>
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="archived-projects">
      {rows.map(p => (
        <Fragment key={p.id}>
          <div className="card opacity-80" data-archived-project={p.slug}>
            <p className="font-semibold text-[var(--color-text)]">{p.name}</p>
            <p className="text-xs text-[var(--color-text-muted)] font-mono mt-0.5">{p.slug}</p>
            <p className="text-xs text-[var(--color-text-muted)] mt-2">
              {p.archived_at
                ? <>Archived on {formatDay(p.archived_at)}{p.archived_by_name ? <> by {p.archived_by_name}</> : null}</>
                : 'Archived (date not recorded)'}
            </p>
            <button type="button" className="btn-secondary btn-sm mt-3" onClick={() => setAsking(p.id)} disabled={busy}>
              Restore
            </button>
          </div>
          {asking === p.id && (
            <div data-in-page-row="" className="col-span-full">
              <InlineConfirm
                message={<>Restore project &quot;<strong>{p.name}</strong>&quot;? Its members and runs come back with it.
                  Its API keys stay revoked (create new ones in API Keys), and its default QA lead stays disabled (reset
                  its password from Members to re-enable it).</>}
                confirmLabel="Restore"
                onConfirm={() => { void restore(p) }}
                onCancel={() => setAsking(null)}
              />
            </div>
          )}
        </Fragment>
      ))}
    </div>
  )
}

// ── Edit Project (in the page, after its card) ──────────────────────────────────────────────────────

function EditProjectModal({ project, onClose, onSaved, slot }: { project: Project; onClose: () => void; onSaved: () => void; slot?: InlineSlot }) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? '')
  const [jiraKey, setJiraKey] = useState(project.jira_project_key ?? '')
  const [splunkIndex, setSplunkIndex] = useState(project.splunk_index ?? '')
  const [ocpNamespace, setOcpNamespace] = useState(project.ocp_namespace ?? '')
  const [startDate, setStartDate] = useState(fmtDate(project.start_date))
  const [endDate, setEndDate] = useState(fmtDate(project.end_date))
  const [tagsInput, setTagsInput] = useState((project.tags ?? []).join(', '))
  // W28 TCM-02: the project's "Require review before Active" setting (an older server sends nothing: off).
  const [requireCaseReview, setRequireCaseReview] = useState(project.require_case_review ?? false)
  const [saving, setSaving] = useState(false)
  // W10 INT-03: the server's 422 sentence per field, shown under it.
  const [errors, setErrors] = useState<Record<string, string>>({})
  const clearError = (field: string) => setErrors(prev => withoutError(prev, field))

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { toast.error('Name is required'); return }
    setSaving(true)
    setErrors({})
    try {
      const updates: ProjectUpdate = {}
      if (name !== project.name) updates.name = name.trim()
      if (description !== (project.description ?? '')) updates.description = description || null
      if (jiraKey !== (project.jira_project_key ?? '')) updates.jira_project_key = jiraKey.toUpperCase() || null
      // A cleared index / namespace is sent as "" (a null was dropped by the PUT, so it could not be cleared);
      // a stored "" reads as unset, handing the key back to the deployment's value.
      if (splunkIndex !== (project.splunk_index ?? '')) updates.splunk_index = splunkIndex
      if (ocpNamespace !== (project.ocp_namespace ?? '')) updates.ocp_namespace = ocpNamespace
      if (startDate !== fmtDate(project.start_date)) updates.start_date = startDate ? new Date(startDate).toISOString() : null
      if (endDate !== fmtDate(project.end_date)) updates.end_date = endDate ? new Date(endDate).toISOString() : null
      const newTags = tagsInput.split(',').map(t => t.trim()).filter(Boolean)
      const oldTags = project.tags ?? []
      if (JSON.stringify(newTags) !== JSON.stringify(oldTags)) updates.tags = newTags
      // Sent only when it changed, so an untouched form's PUT stays exactly the fields the reader edited.
      if (requireCaseReview !== (project.require_case_review ?? false)) updates.require_case_review = requireCaseReview

      if (Object.keys(updates).length === 0) {
        toast('No changes to save')
        onClose()
        return
      }

      // A PUT carrying the Splunk index or the OpenShift namespace (the fields the server validates with fixed
      // sentences) asks for no global toast: its 422 shows under the field. Any other edit is today's call.
      if (carriesValidatedIntegrationField(updates)) {
        await projectsService.update(project.id, updates, { suppressToast: true })
      } else {
        await projectsService.update(project.id, updates)
      }
      toast.success('Project updated')
      onSaved()
    } catch (err: unknown) {
      const fieldErrors = fieldErrorsFrom(err, EDIT_PROJECT_ERROR_FIELDS)
      if (fieldErrors) setErrors(fieldErrors)
      else toast.error(saveFailureMessage(err, 'Failed to update project'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <InlineFormSection
      title="Edit Project"
      titleId="edit-project-title"
      closeLabel="Close edit project dialog"
      onClose={onClose}
      canClose={!saving}
      slot={slot}
      onSubmit={handleSave}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </>
      }
    >
        <div className="max-w-2xl space-y-4">
          <div>
            <label htmlFor="ep-name" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Project Name *</label>
            <input id="ep-name" type="text" className="input" value={name} onChange={e => { setName(e.target.value); clearError('name') }} required
              aria-describedby={describedBy(errors.name && 'ep-name-error')} />
            <FieldError id="ep-name-error" field="name" message={errors.name} />
          </div>
          <div>
            <label htmlFor="ep-slug" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Slug</label>
            <input id="ep-slug" type="text" className="input font-mono text-sm bg-[var(--color-bg-card)]/50" value={project.slug} readOnly disabled />
            <p className="text-xs text-[var(--color-text-faint)] mt-1">Slug cannot be changed after creation</p>
          </div>
          <div>
            <label htmlFor="ep-desc" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Description</label>
            <textarea id="ep-desc" className="input resize-none" rows={3} value={description}
              onChange={e => { setDescription(e.target.value); clearError('description') }} placeholder="Project description"
              aria-describedby={describedBy(errors.description && 'ep-desc-error')} />
            <FieldError id="ep-desc-error" field="description" message={errors.description} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="ep-start" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Start Date</label>
              <input id="ep-start" type="date" className="input" value={startDate} onChange={e => setStartDate(e.target.value)} />
            </div>
            <div>
              <label htmlFor="ep-end" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">End Date</label>
              <input id="ep-end" type="date" className="input" value={endDate} onChange={e => setEndDate(e.target.value)} />
            </div>
          </div>

          <div>
            <label htmlFor="ep-tags" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Tags (comma-separated)</label>
            <input id="ep-tags" type="text" className="input" value={tagsInput} onChange={e => setTagsInput(e.target.value)}
              placeholder="e.g. backend, payments, critical" />
          </div>

          <div>
            {/* Inline flow, not a flex row: the label wraps under itself at any width. */}
            <div className="text-sm">
              <input id="ep-require-review" type="checkbox" className="mr-2 align-middle" checked={requireCaseReview}
                onChange={e => setRequireCaseReview(e.target.checked)} aria-describedby="ep-require-review-help" />
              <label htmlFor="ep-require-review" className="align-middle text-[var(--color-text)]">
                Require review before a test case becomes Active
              </label>
            </div>
            <p id="ep-require-review-help" className="text-xs text-[var(--color-text-faint)] mt-1">
              When on, a test case needs an approved review before it can be made Active. A QA lead or administrator can approve their own case with a reason.
            </p>
          </div>

          <div className="border-t border-[var(--color-border)] pt-4">
            <p className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-3">Integrations</p>
            {/* W10 D22: no Jenkins Job Pattern (nothing read it; a stored value is kept). The other two say what
                they drive; each help / error line wraps under its input inside its column. */}
            <div className="grid grid-cols-2 gap-3">
              <div className="min-w-0">
                <label htmlFor="ep-jira" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Jira Project Key</label>
                <input id="ep-jira" type="text" className="input font-mono text-sm" value={jiraKey}
                  onChange={e => { setJiraKey(e.target.value.toUpperCase()); clearError('jira_project_key') }} placeholder="PAY"
                  aria-describedby={describedBy(errors.jira_project_key && 'ep-jira-error')} />
                <FieldError id="ep-jira-error" field="jira_project_key" message={errors.jira_project_key} />
              </div>
              <div className="min-w-0">
                <label htmlFor="ep-splunk" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Splunk Index</label>
                <input id="ep-splunk" type="text" className="input text-sm" value={splunkIndex}
                  onChange={e => { setSplunkIndex(e.target.value); clearError('splunk_index') }} placeholder="main"
                  aria-describedby={describedBy('ep-splunk-help', errors.splunk_index && 'ep-splunk-error')}
                  aria-invalid={errors.splunk_index ? true : undefined} />
                <p id="ep-splunk-help" className="text-xs text-[var(--color-text-faint)] mt-1">{SPLUNK_INDEX_HELP}</p>
                <FieldError id="ep-splunk-error" field="splunk_index" message={errors.splunk_index} />
              </div>
              <div className="min-w-0">
                <label htmlFor="ep-ocp" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">OCP Namespace</label>
                <input id="ep-ocp" type="text" className="input text-sm" value={ocpNamespace}
                  onChange={e => { setOcpNamespace(e.target.value); clearError('ocp_namespace') }} placeholder="qa-testing"
                  aria-describedby={describedBy('ep-ocp-help', errors.ocp_namespace && 'ep-ocp-error')}
                  aria-invalid={errors.ocp_namespace ? true : undefined} />
                <p id="ep-ocp-help" className="text-xs text-[var(--color-text-faint)] mt-1">{OCP_NAMESPACE_HELP}</p>
                <FieldError id="ep-ocp-error" field="ocp_namespace" message={errors.ocp_namespace} />
              </div>
            </div>
          </div>
        </div>
    </InlineFormSection>
  )
}
