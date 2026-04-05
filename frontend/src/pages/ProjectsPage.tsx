import { useState, useEffect } from 'react'
import { Edit3, FlaskConical, Plus, Trash2, X } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import { projectsService } from '@/services/projectsService'
import { useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import type { Project, ProjectUpdate } from '@/types/projects'
import { fromNow } from '@/utils/formatters'
import toast from 'react-hot-toast'

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

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [showModal, setShowModal] = useState(false)
  const [editProject, setEditProject] = useState<Project | null>(null)
  const [form, setForm] = useState<NewProjectForm>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const { setActiveProject } = useProjectStore()
  const { isAdmin, isQaLead } = usePermissions()
  const canEdit = isAdmin || isQaLead

  const load = () => projectsService.list().then(setProjects).catch(() => {})
  useEffect(() => { load() }, [])

  const handleNameChange = (name: string) => {
    setForm(f => ({ ...f, name, slug: slugify(name) }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim() || !form.slug.trim()) return
    setSaving(true)
    try {
      const payload: Record<string, string> = { name: form.name, slug: form.slug }
      if (form.description) payload.description = form.description
      if (form.jira_project_key) payload.jira_project_key = form.jira_project_key
      if (form.ocp_namespace) payload.ocp_namespace = form.ocp_namespace
      await projectsService.create(payload)
      toast.success('Project created')
      setShowModal(false)
      setForm(EMPTY_FORM)
      load()
    } catch {
      toast.error('Failed to create project')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (p: Project) => {
    if (!confirm(`Archive project "${p.name}"? It will be hidden but not deleted.`)) return
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
      <PageHeader title="Projects" subtitle="Manage test projects and their integrations"
        actions={canEdit ? (
          <button className="btn-primary flex items-center gap-2 text-sm"
            onClick={() => { setForm(EMPTY_FORM); setShowModal(true) }}>
            <Plus className="h-4 w-4" /> New Project
          </button>
        ) : undefined}
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {projects.map(p => (
          <div key={p.id} className="card hover:border-[var(--color-border-light)] transition-colors group relative">
            <div className="cursor-pointer" onClick={() => setActiveProject(p)}>
              <div className="flex items-start gap-3">
                <div className="p-2.5 bg-white/10 rounded-xl">
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
                        <span key={t} className="text-[10px] px-1.5 py-0.5 bg-white/10 text-[var(--color-text)] rounded">{t}</span>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-[var(--color-text-faint)] mt-2">Created {fromNow(p.created_at)}</p>
                </div>
              </div>
            </div>
            {canEdit && (
              <div className="absolute top-3 right-3 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={(e) => { e.stopPropagation(); setEditProject(p) }}
                  className="p-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors" title="Edit">
                  <Edit3 className="h-3.5 w-3.5" />
                </button>
                <button onClick={(e) => { e.stopPropagation(); handleDelete(p) }}
                  className="p-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded text-red-400 hover:text-red-300 transition-colors" title="Archive">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>
        ))}

        {projects.length === 0 && (
          <div className="col-span-full flex flex-col items-center py-16 text-[var(--color-text-muted)]">
            <FlaskConical className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
            <p className="font-medium">No projects yet</p>
            <p className="text-sm mt-1">Create your first project to start ingesting test reports</p>
          </div>
        )}
      </div>

      {/* Create Project Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-[var(--color-bg)]/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-2xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
              <h2 className="font-semibold text-[var(--color-text)]">New Project</h2>
              <button onClick={() => setShowModal(false)} className="btn-ghost p-1"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Project Name *</label>
                <input type="text" className="input" placeholder="e.g. Payment Gateway API"
                  value={form.name} onChange={e => handleNameChange(e.target.value)} required autoFocus />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Slug *</label>
                <input type="text" className="input font-mono text-sm" placeholder="e.g. payment-gateway-api"
                  value={form.slug} onChange={e => setForm(f => ({ ...f, slug: slugify(e.target.value) }))} required />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Description</label>
                <textarea className="input resize-none" rows={2} placeholder="Optional"
                  value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Jira Project Key</label>
                  <input type="text" className="input font-mono text-sm" placeholder="e.g. PAY"
                    value={form.jira_project_key} onChange={e => setForm(f => ({ ...f, jira_project_key: e.target.value.toUpperCase() }))} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">OCP Namespace</label>
                  <input type="text" className="input text-sm" placeholder="e.g. qa-testing"
                    value={form.ocp_namespace} onChange={e => setForm(f => ({ ...f, ocp_namespace: e.target.value }))} />
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button type="button" className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
                <button type="submit" className="btn-primary flex-1" disabled={saving}>
                  {saving ? 'Creating…' : 'Create Project'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Project Modal */}
      {editProject && (
        <EditProjectModal project={editProject} onClose={() => setEditProject(null)} onSaved={() => { setEditProject(null); load() }} />
      )}
    </div>
  )
}

// ── Edit Project Modal ──────────────────────────────────────────────────────

function EditProjectModal({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? '')
  const [jiraKey, setJiraKey] = useState(project.jira_project_key ?? '')
  const [splunkIndex, setSplunkIndex] = useState(project.splunk_index ?? '')
  const [ocpNamespace, setOcpNamespace] = useState(project.ocp_namespace ?? '')
  const [jenkinsPattern, setJenkinsPattern] = useState(project.jenkins_job_pattern ?? '')
  const [startDate, setStartDate] = useState(fmtDate(project.start_date))
  const [endDate, setEndDate] = useState(fmtDate(project.end_date))
  const [tagsInput, setTagsInput] = useState((project.tags ?? []).join(', '))
  const [saving, setSaving] = useState(false)

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { toast.error('Name is required'); return }
    setSaving(true)
    try {
      const updates: ProjectUpdate = {}
      if (name !== project.name) updates.name = name.trim()
      if (description !== (project.description ?? '')) updates.description = description || null
      if (jiraKey !== (project.jira_project_key ?? '')) updates.jira_project_key = jiraKey.toUpperCase() || null
      if (splunkIndex !== (project.splunk_index ?? '')) updates.splunk_index = splunkIndex || null
      if (ocpNamespace !== (project.ocp_namespace ?? '')) updates.ocp_namespace = ocpNamespace || null
      if (jenkinsPattern !== (project.jenkins_job_pattern ?? '')) updates.jenkins_job_pattern = jenkinsPattern || null
      if (startDate !== fmtDate(project.start_date)) updates.start_date = startDate ? new Date(startDate).toISOString() : null
      if (endDate !== fmtDate(project.end_date)) updates.end_date = endDate ? new Date(endDate).toISOString() : null
      const newTags = tagsInput.split(',').map(t => t.trim()).filter(Boolean)
      const oldTags = project.tags ?? []
      if (JSON.stringify(newTags) !== JSON.stringify(oldTags)) updates.tags = newTags

      if (Object.keys(updates).length === 0) {
        toast('No changes to save')
        onClose()
        return
      }

      await projectsService.update(project.id, updates)
      toast.success('Project updated')
      onSaved()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to update project')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-[var(--color-bg)]/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-2xl w-full max-w-lg shadow-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
          <h2 className="font-semibold text-[var(--color-text)]">Edit Project</h2>
          <button onClick={onClose} className="btn-ghost p-1"><X className="h-4 w-4" /></button>
        </div>
        <form onSubmit={handleSave} className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Project Name *</label>
            <input type="text" className="input" value={name} onChange={e => setName(e.target.value)} required />
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Slug</label>
            <input type="text" className="input font-mono text-sm bg-[var(--color-bg-card)]/50" value={project.slug} readOnly disabled />
            <p className="text-xs text-[var(--color-text-faint)] mt-1">Slug cannot be changed after creation</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Description</label>
            <textarea className="input resize-none" rows={3} value={description}
              onChange={e => setDescription(e.target.value)} placeholder="Project description" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Start Date</label>
              <input type="date" className="input" value={startDate} onChange={e => setStartDate(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">End Date</label>
              <input type="date" className="input" value={endDate} onChange={e => setEndDate(e.target.value)} />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Tags (comma-separated)</label>
            <input type="text" className="input" value={tagsInput} onChange={e => setTagsInput(e.target.value)}
              placeholder="e.g. backend, payments, critical" />
          </div>

          <div className="border-t border-[var(--color-border)] pt-4">
            <p className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-3">Integrations</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Jira Project Key</label>
                <input type="text" className="input font-mono text-sm" value={jiraKey}
                  onChange={e => setJiraKey(e.target.value.toUpperCase())} placeholder="PAY" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Splunk Index</label>
                <input type="text" className="input text-sm" value={splunkIndex}
                  onChange={e => setSplunkIndex(e.target.value)} placeholder="main" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">OCP Namespace</label>
                <input type="text" className="input text-sm" value={ocpNamespace}
                  onChange={e => setOcpNamespace(e.target.value)} placeholder="qa-testing" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Jenkins Job Pattern</label>
                <input type="text" className="input text-sm" value={jenkinsPattern}
                  onChange={e => setJenkinsPattern(e.target.value)} placeholder="**/payment-*" />
              </div>
            </div>
          </div>

          <div className="flex gap-3 pt-2">
            <button type="button" className="btn-secondary flex-1" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary flex-1" disabled={saving}>
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
