/**
 * Instance defaults (W22b ACT-06) — the last card on /settings/retention (G-D mounts it, admin branch only).
 *
 * Every retention field a project leaves unset inherits these, field by field. The card issues NO request until
 * `Open` (the hook's key is `null` while collapsed). Opened, it edits the four day windows, the keep-last floor and
 * the purge-window hour (an empty input = the code default, sent as `clear`), each with what it resolves to now and
 * how many projects inherit it; `Save defaults` PUTs only what changed.
 *
 * D12: the default ships OFF. Turning it on reads the enable preview (every project without its own `enabled`,
 * listed with its runs past the window), then `Turn on` is enabled ONLY by the exact typed phrase and sends the
 * phrase with the preview's hash; a 409 `preview_stale` re-reads the preview. Turning it off asks inline first.
 * In-page only (InlineFormSection / InlineConfirm): no modal, no `confirm(`. Every name and sentence is React text.
 */
import { useState } from 'react'
import { useSWRConfig } from 'swr'
import { isAxiosError } from 'axios'
import { RefreshCw, SlidersHorizontal } from 'lucide-react'
import toast from 'react-hot-toast'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { RESULT_STATUS_TOKEN } from '@/components/ui/statusVocabulary'
import { useInstanceEnablePreview, useInstanceRetentionPolicy } from '@/hooks/useInstanceRetentionPolicy'
import { usePermissions } from '@/hooks/usePermissions'
import { retentionInstanceService } from '@/services/retentionInstanceService'
import { KEEP_LAST_RUNS_BOUNDS, RETENTION_BOUNDS } from '@/types/retention'
import type {
  InheritableField,
  InstanceEnablePreview,
  InstanceRetentionPolicy,
  InstanceRetentionPolicyWrite,
} from '@/types/retentionExport'
import { formatDay } from '@/utils/formatters'
import { toneStyle } from './exportModel'
import { serverSentence } from './holdsModel'
import { HOURS, hourLabel, markerLine } from './scheduleModel'

export const INSTANCE_DEFAULTS_TITLE = 'Instance defaults'
export const INSTANCE_DEFAULTS_INTRO = 'Every project that does not set its own value uses these.'
export const INSTANCE_LOAD_ERROR = 'Could not load the instance defaults.'
export const PREVIEW_LOAD_ERROR = 'Could not load the projects this would switch on.'
export const TURN_ON_LABEL = 'Turn on for every project without its own setting…'
export const PREVIEW_NOTE = 'Counts are before the keep-last floor and holds.'
export const NOTHING_TO_ENABLE = 'No project would change: every project sets its own value.'
export const TURN_OFF_QUESTION = 'Stop scheduled purging for every project that inherits it?'
export const DEFAULTS_SAVED = 'Instance defaults saved'
/** The SWR key of one project's policy (`useRetentionPolicy`); re-read after an instance change. */
export const PROJECT_POLICY_KEY = /^\/api\/v1\/projects\/[^/]+\/retention-policy$/

type ClearableField = Exclude<InheritableField, 'enabled'>

interface FieldSpec {
  field: ClearableField
  label: string
  min: number
  max: number
}

/** The five number fields, in the policy card's order; the window hour is a select. */
const NUMBER_FIELDS: readonly FieldSpec[] = Object.freeze([
  { field: 'raw_events_days', label: 'Raw events (days)', ...RETENTION_BOUNDS.raw_events_days },
  { field: 'runs_days', label: 'Runs & analysis (days)', ...RETENTION_BOUNDS.runs_days },
  { field: 'artifacts_days', label: 'Artifacts (days)', ...RETENTION_BOUNDS.artifacts_days },
  { field: 'audit_days', label: 'Audit trail (days)', ...RETENTION_BOUNDS.audit_days },
  { field: 'keep_last_runs', label: 'Keep the newest runs', ...KEEP_LAST_RUNS_BOUNDS },
])
const WINDOW: ClearableField = 'purge_window_start_utc'
const ALL_FIELDS: readonly ClearableField[] = [...NUMBER_FIELDS.map((f) => f.field), WINDOW]

type DefaultsForm = Record<ClearableField, string>

/** The form from the stored values: a stored value as text, a NULL (= the code default) as ''. */
function formFromPolicy(policy: InstanceRetentionPolicy): DefaultsForm {
  const form = {} as DefaultsForm
  for (const field of ALL_FIELDS) {
    const value = policy[field]
    form[field] = value == null ? '' : String(value)
  }
  return form
}

/**
 * The PUT body: a changed value as itself, an emptied stored value in `clear` (back to the code default), nothing
 * else. `error` names the first value out of its bounds (the server checks again, incl. audit ≥ runs).
 */
function defaultsPayload(
  form: DefaultsForm,
  saved: InstanceRetentionPolicy,
): { body: InstanceRetentionPolicyWrite; error: string | null } {
  const body: InstanceRetentionPolicyWrite = {}
  const clear: ClearableField[] = []
  const specs: FieldSpec[] = [...NUMBER_FIELDS, { field: WINDOW, label: 'Purge window', min: 0, max: 23 }]
  for (const { field, label, min, max } of specs) {
    const raw = form[field].trim()
    const stored = saved[field]
    if (raw === '') {
      if (stored != null) clear.push(field)
      continue
    }
    const value = Number(raw)
    if (!Number.isInteger(value) || value < min || value > max) {
      return { body: {}, error: `${label} must be a whole number from ${min} to ${max}.` }
    }
    if (value !== stored) body[field] = value
  }
  if (clear.length > 0) body.clear = clear
  return { body, error: null }
}

/** `180 days (instance default)` / `365 days (default)` — the field's value now, through the page's marker copy. */
function resolvedLine(field: ClearableField, policy: InstanceRetentionPolicy): string | null {
  const stored = policy[field]
  return markerLine(field, {
    field_sources: { [field]: stored == null ? 'default' : 'instance' },
    [field]: stored == null ? policy.code_defaults[field] ?? null : stored,
  })
}

/** `Used by 4 projects` (the projects whose value would come from here). */
function usedByLine(count: number | undefined): string {
  const n = typeof count === 'number' ? count : 0
  return `Used by ${n} ${n === 1 ? 'project' : 'projects'}`
}

/** `On since Oct 28, 2026 by Admin` (each part only when known). */
function onSinceLine(policy: InstanceRetentionPolicy): string {
  const since = policy.enabled_at ? ` since ${formatDay(policy.enabled_at)}` : ''
  const by = policy.enabled_by_name ? ` by ${policy.enabled_by_name}` : ''
  return `On${since}${by}`
}

function InlineError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="text-xs text-[var(--ui-danger-ink)]">
      {message}
    </p>
  )
}

function errorCode(err: unknown): string | null {
  if (!isAxiosError(err)) return null
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
  const code = detail && typeof detail === 'object' ? (detail as { code?: unknown }).code : null
  return typeof code === 'string' ? code : null
}

function PreviewTable({ preview }: { preview: InstanceEnablePreview }) {
  return (
    <div className="max-h-72 overflow-y-auto rounded border border-[var(--color-border)]">
      <Table density="dense" data-retention-instance-preview-table="">
        <thead>
          <tr>
            <Th className="w-[300px]">Project</Th>
            <Th className="w-[60px]">Active</Th>
            <Th numeric className="w-[130px]">Runs past window</Th>
            <Th className="w-[80px]">Window</Th>
          </tr>
        </thead>
        <tbody>
          {preview.items.map((item) => (
            <Tr key={item.project_id} data-retention-instance-preview-row="">
              <Td>
                <span className="block max-w-[300px] truncate" title={item.name}>
                  {item.name}
                </span>
              </Td>
              <Td>{item.is_active ? 'Yes' : 'No'}</Td>
              <Td numeric>{item.runs_past_window}</Td>
              <Td>{hourLabel(item.effective_after.purge_window_start_utc ?? 2)}</Td>
            </Tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

interface EnableSectionProps {
  onClose: () => void
  onEnabled: (policy: InstanceRetentionPolicy) => void
}

/** D12: the preview of every affected project, then the typed phrase. Sends nothing until `Turn on`. */
function EnableSection({ onClose, onEnabled }: EnableSectionProps) {
  const { data: preview, error: loadError, isLoading, mutate } = useInstanceEnablePreview(true)
  const [typed, setTyped] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const phrase = preview?.confirmation_phrase ?? ''
  const matches = Boolean(preview) && phrase !== '' && typed === phrase

  async function turnOn() {
    if (!preview || !matches || sending) return
    setSending(true)
    setError(null)
    try {
      const policy = await retentionInstanceService.enableInstancePolicy({
        confirmation: typed,
        preview_hash: preview.preview_hash,
      })
      toast.success(`Instance default on for ${preview.total} ${preview.total === 1 ? 'project' : 'projects'}`)
      onEnabled(policy)
    } catch (err) {
      setError(serverSentence(err, 'Could not turn the instance default on.'))
      if (errorCode(err) === 'preview_stale') {
        setTyped('')
        void mutate()
      }
    } finally {
      setSending(false)
    }
  }

  return (
    <InlineFormSection
      title="Turn on the instance default"
      headingLevel={3}
      onClose={onClose}
      canClose={!sending}
      busy={sending}
      dataAttributes={{ 'data-retention-instance-enable': '' }}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={sending} className="btn-secondary btn-sm">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void turnOn()}
            disabled={!matches || sending || !preview || preview.total === 0}
            className="btn-danger btn-sm"
          >
            {sending ? 'Turning on…' : 'Turn on'}
          </button>
        </>
      }
    >
      <div className="space-y-3 text-xs">
        {isLoading && !preview && (
          <div className="flex justify-center py-3">
            <LoadingSpinner size="sm" />
          </div>
        )}
        {loadError && !preview && <InlineError message={PREVIEW_LOAD_ERROR} />}
        {preview && preview.total === 0 && <p className="text-[var(--color-text-muted)]">{NOTHING_TO_ENABLE}</p>}
        {preview && preview.total > 0 && (
          <>
            <PreviewTable preview={preview} />
            <p data-retention-instance-preview-total="" className="text-[var(--color-text-muted)]">
              Showing {preview.items.length} of {preview.total}
            </p>
            <p className="text-[var(--color-text-muted)]">{PREVIEW_NOTE}</p>
            <label htmlFor="retention-instance-confirm" className="block">
              <span className="text-[var(--color-text)]">{`Type "${phrase}" to confirm`}</span>
              <input
                id="retention-instance-confirm"
                type="text"
                autoComplete="off"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                className="mt-1 block w-80 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-sm text-[var(--color-text)]"
              />
            </label>
          </>
        )}
        <InlineError message={error} />
      </div>
    </InlineFormSection>
  )
}

const FIELD_INPUT =
  'mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-sm text-[var(--color-text)]'

interface DefaultsSectionProps {
  policy: InstanceRetentionPolicy
  onClose: () => void
  onSaved: (policy: InstanceRetentionPolicy) => void
}

/** The six inheritable values; an empty input is the code default (sent as `clear`). */
function DefaultsSection({ policy, onClose, onSaved }: DefaultsSectionProps) {
  const [form, setForm] = useState<DefaultsForm>(() => formFromPolicy(policy))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { body, error: formError } = defaultsPayload(form, policy)
  const dirty = Object.keys(body).length > 0

  async function save() {
    if (saving) return
    if (formError) {
      setError(formError)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await retentionInstanceService.updateInstancePolicy(body)
      setForm(formFromPolicy(updated))
      toast.success(DEFAULTS_SAVED)
      onSaved(updated)
    } catch (err) {
      setError(serverSentence(err, 'Could not save the instance defaults.'))
    } finally {
      setSaving(false)
    }
  }

  const edit = (field: ClearableField, value: string) => setForm((prev) => ({ ...prev, [field]: value }))
  const help = (field: ClearableField) => (
    <>
      <span className="mt-1 block text-[var(--color-text-faint)]">{resolvedLine(field, policy)}</span>
      <span data-retention-instance-used-by={field} className="block text-[var(--color-text-faint)]">
        {usedByLine(policy.inheriting_projects[field])}
      </span>
    </>
  )

  return (
    <InlineFormSection
      title="Default values"
      headingLevel={3}
      onClose={onClose}
      canClose={!saving}
      busy={saving}
      autoFocus={false}
      dataAttributes={{ 'data-retention-instance-form': '' }}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={saving} className="btn-secondary btn-sm">
            Close
          </button>
          <button type="button" onClick={() => void save()} disabled={saving || !dirty} className="btn-primary btn-sm">
            {saving ? 'Saving…' : 'Save defaults'}
          </button>
        </>
      }
    >
      <div className="space-y-3 text-xs">
        <div className="grid gap-3 sm:grid-cols-2">
          {NUMBER_FIELDS.map(({ field, label, min, max }) => (
            <label key={field} htmlFor={`retention-instance-${field}`} className="block" data-retention-instance-field={field}>
              <span className="text-[var(--color-text-muted)]">{label}</span>
              <input
                id={`retention-instance-${field}`}
                type="number"
                inputMode="numeric"
                min={min}
                max={max}
                value={form[field]}
                placeholder={`default ${policy.code_defaults[field] ?? ''}`}
                onChange={(e) => edit(field, e.target.value)}
                className={FIELD_INPUT}
              />
              {help(field)}
            </label>
          ))}
          <label htmlFor="retention-instance-window" className="block" data-retention-instance-field={WINDOW}>
            <span className="text-[var(--color-text-muted)]">Purge window (UTC)</span>
            <select
              id="retention-instance-window"
              value={form[WINDOW]}
              onChange={(e) => edit(WINDOW, e.target.value)}
              className={FIELD_INPUT}
            >
              <option value="">{`default ${hourLabel(Number(policy.code_defaults[WINDOW] ?? 2))}`}</option>
              {HOURS.map((hour) => (
                <option key={hour.value} value={String(hour.value)}>
                  {hour.label}
                </option>
              ))}
            </select>
            {help(WINDOW)}
          </label>
        </div>
        <InlineError message={error} />
      </div>
    </InlineFormSection>
  )
}

function StateChip({ enabled }: { enabled: boolean }) {
  return (
    <span
      data-retention-instance-state={enabled ? 'on' : 'off'}
      className="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text-muted)]"
      style={enabled ? toneStyle(RESULT_STATUS_TOKEN.passed) : undefined}
    >
      {enabled ? 'On' : 'Off'}
    </span>
  )
}

export interface InstanceDefaultsCardProps {
  /** Start opened (the card still reads nothing until it is open). Default false. */
  open?: boolean
}

export default function InstanceDefaultsCard({ open: initiallyOpen = false }: InstanceDefaultsCardProps) {
  const { isAdmin } = usePermissions()
  const [open, setOpen] = useState(initiallyOpen)
  const [enabling, setEnabling] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const [turningOff, setTurningOff] = useState(false)
  const [switchError, setSwitchError] = useState<string | null>(null)
  const { data: policy, error, isLoading, mutate } = useInstanceRetentionPolicy(isAdmin && open)
  const { mutate: mutateKeys } = useSWRConfig()
  if (!isAdmin) return null

  /** A project's policy card reads its "(instance default)" values from the server: after any instance change
   *  every cached project policy re-reads (the W22b local demo saved runs 180 and the card above kept "365 days
   *  (default)" until a reload). */
  function refreshProjectPolicies() {
    void mutateKeys((key) => typeof key === 'string' && PROJECT_POLICY_KEY.test(key))
  }

  function close() {
    setOpen(false)
    setEnabling(false)
    setConfirmOff(false)
    setSwitchError(null)
  }

  async function turnOff() {
    if (turningOff) return
    setTurningOff(true)
    setSwitchError(null)
    try {
      const updated = await retentionInstanceService.updateInstancePolicy({ enabled: false })
      setConfirmOff(false)
      toast.success('Instance default off')
      void mutate(updated, { revalidate: false })
      refreshProjectPolicies()
    } catch (err) {
      setSwitchError(serverSentence(err, 'Could not turn the instance default off.'))
    } finally {
      setTurningOff(false)
    }
  }

  return (
    <section
      data-retention-instance-defaults=""
      aria-labelledby="retention-instance-defaults-heading"
      className="card space-y-3"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-2">
          <SlidersHorizontal aria-hidden="true" className="h-4 w-4 text-[var(--color-accent)]" />
          <h2
            id="retention-instance-defaults-heading"
            className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
          >
            {INSTANCE_DEFAULTS_TITLE}
          </h2>
        </div>
        {open && policy && <StateChip enabled={policy.enabled} />}
        <button
          type="button"
          onClick={() => (open ? close() : setOpen(true))}
          aria-expanded={open}
          className="btn-secondary btn-sm ml-auto"
        >
          {open ? 'Close' : 'Open'}
        </button>
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">{INSTANCE_DEFAULTS_INTRO}</p>

      {open && isLoading && !policy && (
        <div className="flex justify-center py-3">
          <LoadingSpinner size="sm" />
        </div>
      )}
      {open && error && !policy && (
        <div role="alert" data-retention-instance-error="" className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[var(--ui-danger-ink)]">{INSTANCE_LOAD_ERROR}</span>
          <button type="button" onClick={() => void mutate()} className="btn-secondary btn-sm inline-flex items-center gap-1.5">
            <RefreshCw aria-hidden="true" className="h-3 w-3" /> Retry
          </button>
        </div>
      )}

      {open && policy && (
        <>
          <DefaultsSection
            key={policy.updated_at ?? 'defaults'}
            policy={policy}
            onClose={close}
            onSaved={(updated) => {
              void mutate(updated, { revalidate: false })
              refreshProjectPolicies()
            }}
          />
          <div data-retention-instance-switch="" className="space-y-2 border-t border-[var(--color-border)] pt-3 text-xs">
            <div className="flex flex-wrap items-center gap-3">
              {policy.enabled ? (
                <>
                  <span className="text-[var(--color-text)]">{onSinceLine(policy)}</span>
                  <button
                    type="button"
                    onClick={() => setConfirmOff(true)}
                    disabled={confirmOff}
                    className="btn-secondary btn-sm"
                  >
                    Turn off
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setEnabling(true)}
                  disabled={enabling}
                  className="btn-secondary btn-sm"
                >
                  {TURN_ON_LABEL}
                </button>
              )}
            </div>
            {policy.enabled && confirmOff && (
              <InlineConfirm
                message={TURN_OFF_QUESTION}
                confirmLabel="Turn off"
                busy={turningOff}
                onConfirm={() => void turnOff()}
                onCancel={() => setConfirmOff(false)}
                testId="retention-instance-turn-off"
              />
            )}
            <InlineError message={switchError} />
          </div>
          {!policy.enabled && enabling && (
            <EnableSection
              onClose={() => setEnabling(false)}
              onEnabled={(updated) => {
                setEnabling(false)
                void mutate(updated, { revalidate: false })
                refreshProjectPolicies()
              }}
            />
          )}
        </>
      )}
    </section>
  )
}
