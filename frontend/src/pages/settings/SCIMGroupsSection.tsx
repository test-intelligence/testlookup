/**
 * Settings › SSO › SCIM (W20 ADM-09): the groups the IdP pushed over SCIM, and what each grants.
 *
 * Each group lists its member count and its project grants (project → role). Saving a group's grants reconciles its
 * members at once: they gain, change or lose the memberships the group gives. A membership a person granted is never
 * touched; one SCIM created shows "via SCIM group <name>" on the project's members.
 */
import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { listSCIMGroups, putSCIMGroupMappings, type SCIMGroupSummary } from '@/services/ssoService'
import { projectsService } from '@/services/projectsService'
import { extractErrorMessage } from '@/services/apiErrors'

const ROLE_LABEL: Record<string, string> = { VIEWER: 'Viewer', TESTER: 'Tester', QA_ENGINEER: 'QA Engineer', QA_LEAD: 'QA Lead' }
type Row = { project_id: string; role: string }

const errorOf = (err: unknown, fallback: string) =>
  extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail, fallback)

function GroupCard({ group, projects, roles, onSaved }: {
  group: SCIMGroupSummary
  projects: { id: string; name: string }[]
  roles: string[]
  onSaved: (groups: SCIMGroupSummary[], reconciled: number) => void
}) {
  const [rows, setRows] = useState<Row[]>(() => group.mappings.map((m) => ({ project_id: m.project_id, role: m.role })))
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const field = 'input text-sm py-1.5'

  async function save() {
    setBusy(true)
    setMessage(null)
    try {
      const r = await putSCIMGroupMappings(group.id, rows.filter((x) => x.project_id))
      setMessage({ ok: true, text: `Saved — ${r.reconciled_users} ${r.reconciled_users === 1 ? 'member' : 'members'} reconciled.` })
      onSaved(r.groups, r.reconciled_users)
    } catch (err) {
      setMessage({ ok: false, text: errorOf(err, 'The grants could not be saved') })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card p-3 space-y-2" data-testid="scim-group" aria-label={`SCIM group ${group.display_name}`}>
      <div className="flex items-baseline gap-2">
        <h4 className="text-sm font-medium text-[var(--color-text)]">{group.display_name}</h4>
        <span className="text-xs text-[var(--color-text-muted)]">{group.member_count} {group.member_count === 1 ? 'member' : 'members'}</span>
      </div>
      {rows.length === 0 && <p className="text-xs text-[var(--color-text-muted)]">Grants nothing yet.</p>}
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2" data-testid="scim-group-row">
          <select aria-label={`Project ${i + 1}`} value={r.project_id} className={`${field} flex-1 min-w-0`}
            onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, project_id: e.target.value } : x)))}>
            <option value="">Choose a project…</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select aria-label={`Role ${i + 1}`} value={r.role} className={`${field} !w-40 shrink-0`}
            onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, role: e.target.value } : x)))}>
            {roles.map((role) => <option key={role} value={role}>{ROLE_LABEL[role] ?? role}</option>)}
          </select>
          <button type="button" aria-label={`Remove grant ${i + 1}`} onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}
            className="p-1 text-[var(--color-text-muted)] hover:text-[var(--ui-danger)]">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => setRows((rs) => [...rs, { project_id: '', role: roles.includes('QA_ENGINEER') ? 'QA_ENGINEER' : roles[0] }])}
          className="flex items-center gap-1 text-xs text-[var(--color-accent)] hover:underline">
          <Plus className="h-3.5 w-3.5" /> Grant a project
        </button>
        <button type="button" onClick={save} disabled={busy} className="btn-primary btn-sm disabled:opacity-50">
          {busy ? 'Saving…' : 'Save grants'}
        </button>
        {message && <span role={message.ok ? 'status' : 'alert'} className={`text-xs ${message.ok ? 'text-[var(--status-passed)]' : 'text-[var(--ui-danger)]'}`}>{message.text}</span>}
      </div>
    </div>
  )
}

export default function SCIMGroupsSection() {
  const [groups, setGroups] = useState<SCIMGroupSummary[] | null>(null)
  const [roles, setRoles] = useState<string[]>([])
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([listSCIMGroups(), projectsService.list()])
      .then(([g, p]) => {
        if (!live) return
        setGroups(g.groups)
        setRoles(g.roles)
        setProjects(p.map((x) => ({ id: x.id, name: x.name })))
      })
      .catch((err) => { if (live) setError(errorOf(err, 'The SCIM groups could not be read')) })
    return () => { live = false }
  }, [])

  return (
    <section className="space-y-3" data-testid="scim-groups" aria-label="SCIM groups">
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">SCIM groups</h2>
        <p className="text-xs text-[var(--color-text-muted)]">
          Groups your IdP pushes over SCIM. Grant a group projects and its members get them — and lose them when they leave
          the group. Access a person granted is never changed.
        </p>
      </div>
      {error && <p role="alert" className="text-xs text-[var(--ui-danger)]">{error}</p>}
      {groups && groups.length === 0 && <p className="text-sm text-[var(--color-text-muted)]">No groups pushed yet.</p>}
      {groups?.map((g) => (
        <GroupCard key={g.id} group={g} projects={projects} roles={roles}
          onSaved={(next) => setGroups(next)} />
      ))}
    </section>
  )
}
