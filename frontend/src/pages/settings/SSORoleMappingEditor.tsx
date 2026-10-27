/**
 * Settings › SSO (W20 ADM-08a): edit how IdP groups map to TestLookup roles, inside a configuration's card.
 *
 * Rows of IdP group → role, the default role (no group matched), the SAML attribute that carries the groups; Save
 * writes the existing PATCH. "Preview" asks the server what a user in the typed groups would get under the rows as
 * edited (saved or not): the role, the groups that matched, and whether the deployment's provisioning ceiling
 * (`SSO_MAX_PROVISIONED_ROLE`) clamped it.
 */
import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { previewSSORole, updateSSOConfig, type SSOConfig, type SSORolePreview } from '@/services/ssoService'
import { extractErrorMessage } from '@/services/apiErrors'

const ROLES: { value: string; label: string }[] = [
  { value: 'VIEWER', label: 'Viewer' },
  { value: 'TESTER', label: 'Tester' },
  { value: 'QA_ENGINEER', label: 'QA Engineer' },
  { value: 'QA_LEAD', label: 'QA Lead' },
  { value: 'ADMIN', label: 'Admin' },
]
const roleLabel = (v: string) => ROLES.find((r) => r.value === v)?.label ?? v

type Row = { group: string; role: string }

const errorOf = (err: unknown, fallback: string) =>
  extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail, fallback)

export default function SSORoleMappingEditor({ config, onSaved }: { config: SSOConfig; onSaved: () => void }) {
  const [rows, setRows] = useState<Row[]>(() =>
    Object.entries(config.role_mapping ?? {}).map(([group, role]) => ({ group, role })),
  )
  const [defaultRole, setDefaultRole] = useState(config.default_role || 'VIEWER')
  const [groupAttribute, setGroupAttribute] = useState(config.group_attribute ?? '')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [groupsInput, setGroupsInput] = useState('')
  const [preview, setPreview] = useState<SSORolePreview | null>(null)

  const mapping = (): Record<string, string> =>
    Object.fromEntries(rows.filter((r) => r.group.trim()).map((r) => [r.group.trim(), r.role]))
  const duplicate = (() => {
    const seen = new Set<string>()
    for (const r of rows) {
      const g = r.group.trim()
      if (g && seen.has(g)) return g
      seen.add(g)
    }
    return null
  })()

  const setRow = (i: number, patch: Partial<Row>) => {
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))
    setPreview(null)
  }

  async function save() {
    setSaving(true)
    setMessage(null)
    try {
      await updateSSOConfig(config.id, { role_mapping: mapping(), default_role: defaultRole, group_attribute: groupAttribute.trim() })
      setMessage({ ok: true, text: 'Role mapping saved.' })
      onSaved()
    } catch (err) {
      setMessage({ ok: false, text: errorOf(err, 'The role mapping could not be saved') })
    } finally {
      setSaving(false)
    }
  }

  async function runPreview() {
    setMessage(null)
    try {
      const groups = groupsInput.split(',').map((g) => g.trim()).filter(Boolean)
      setPreview(await previewSSORole(config.id, { groups, role_mapping: mapping(), default_role: defaultRole }))
    } catch (err) {
      setPreview(null)
      setMessage({ ok: false, text: errorOf(err, 'The preview could not run') })
    }
  }

  const field = 'input text-sm py-1.5'
  return (
    <section className="mt-3 border-t border-[var(--color-border)] pt-3 space-y-3" data-testid="sso-role-mapping" aria-label="Role mapping">
      <div>
        <h4 className="text-sm font-medium text-[var(--color-text)]">Role mapping</h4>
        <p className="text-xs text-[var(--color-text-muted)]">
          A user signing in with this IdP gets the highest role any of their groups maps to; no match gets the default role.
        </p>
      </div>
      <div className="space-y-2">
        {rows.length === 0 && <p className="text-xs text-[var(--color-text-muted)]">No groups mapped: everyone gets the default role.</p>}
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2" data-testid="sso-role-row">
            <input aria-label={`IdP group ${i + 1}`} placeholder="IdP group (e.g. qa-leads)" value={r.group}
              onChange={(e) => setRow(i, { group: e.target.value })} className={`${field} flex-1`} />
            <span className="text-xs text-[var(--color-text-muted)]">→</span>
            <select aria-label={`Role for group ${i + 1}`} value={r.role} onChange={(e) => setRow(i, { role: e.target.value })} className={field}>
              {ROLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <button type="button" aria-label={`Remove group ${i + 1}`} onClick={() => { setRows((rs) => rs.filter((_, k) => k !== i)); setPreview(null) }}
              className="p-1 text-[var(--color-text-muted)] hover:text-[var(--ui-danger)]">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        <button type="button" onClick={() => setRows((rs) => [...rs, { group: '', role: 'VIEWER' }])}
          className="flex items-center gap-1 text-xs text-[var(--color-accent)] hover:underline">
          <Plus className="h-3.5 w-3.5" /> Add group
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-[var(--color-text-secondary)] flex items-center gap-2">
          Default role
          <select aria-label="Default role" value={defaultRole} onChange={(e) => { setDefaultRole(e.target.value); setPreview(null) }} className={field}>
            {ROLES.filter((o) => o.value !== 'ADMIN').map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className="text-xs text-[var(--color-text-secondary)] flex items-center gap-2">
          Group attribute
          <input aria-label="Group attribute" placeholder="memberOf" value={groupAttribute} onChange={(e) => setGroupAttribute(e.target.value)} className={field} />
        </label>
        <button type="button" onClick={save} disabled={saving || !!duplicate}
          className="btn-primary btn-sm disabled:opacity-50">
          {saving ? 'Saving…' : 'Save role mapping'}
        </button>
      </div>
      {duplicate && <p role="alert" className="text-xs text-[var(--ui-danger)]">The group "{duplicate}" is listed twice.</p>}
      {message && <p role={message.ok ? 'status' : 'alert'} className={`text-xs ${message.ok ? 'text-[var(--status-passed)]' : 'text-[var(--ui-danger)]'}`}>{message.text}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="Groups to preview" placeholder="Preview: a user in groups (comma separated)" value={groupsInput}
          onChange={(e) => setGroupsInput(e.target.value)} className={`${field} flex-1 min-w-[16rem]`} />
        <button type="button" onClick={runPreview} className="px-3 py-1 text-xs bg-[var(--color-bg-hover)] rounded text-[var(--color-text-secondary)]">Preview</button>
      </div>
      {preview && (
        <p className="text-xs text-[var(--color-text)]" data-testid="sso-role-preview">
          → <strong>{roleLabel(preview.role)}</strong>{' '}
          {preview.source === 'mapping' ? `(matched ${preview.matched_groups.join(', ')})` : '(no group matched: the default role)'}
          {preview.clamped && ` — capped at ${roleLabel(preview.ceiling)} by this deployment's provisioning ceiling`}
        </p>
      )}
    </section>
  )
}
