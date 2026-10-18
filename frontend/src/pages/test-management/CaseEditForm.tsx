/**
 * Edit a test case in the page (W28 TCM-01): an `InlineFormSection` in the
 * case pane's Details tab, in place of the read-only details.
 *
 * One column, every editable field prefilled from the case (a masked
 * parameter's value never is). Save sends ONE PATCH with `expected_version`
 * and only the fields that changed; status is not in the form (editing never
 * changes the status — the Status moves are the toolbar's).
 *
 * A 409 (someone saved since the form opened) is never retried and never
 * overwrites: the form re-reads the case and its history and says who changed
 * what (`Changed by … since you opened it`), with **Reload theirs** (the form
 * takes the fresh case) and **Keep editing** (fields the reader did not touch
 * take the fresh values; the reader's own changes stay, and the next Save
 * names the fresh version).
 */
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { useId, useMemo, useState, type ReactNode } from 'react'
import toast from 'react-hot-toast'

import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useProjectMembers } from '@/hooks/useUserManagement'
import { conflictOf, errorSentence, testCaseEditingService } from '@/services/testCaseEditingService'
import type { ManagedTestCase } from '@/types/test-management'
import {
  addParameter,
  addStep,
  AUTOMATION_STATUSES,
  changedByNames,
  differingFields,
  fieldLabels,
  formFromCase,
  moveStep,
  NO_CHANGES,
  payloadFrom,
  PRIORITIES,
  rebaseForm,
  removeParameter,
  removeStep,
  severityOptions,
  TEST_TYPES,
  TITLE_TOO_SHORT,
  touchedFields,
  updateParameter,
  updateStep,
  type CaseField,
  type CaseForm,
} from './caseEditModel'

export interface CaseEditFormProps {
  caseItem: ManagedTestCase
  /** The page's suite names (the Suite field's suggestions). */
  suiteOptions: string[]
  /** The case pane's slot: typed input asks before the pane closes, steps or switches. */
  slot?: InlineSlot
  onCancel: () => void
  /** A saved version: the host closes the form, shows it and refreshes the lists. */
  onSaved: (updated: ManagedTestCase) => void
}

interface Conflict {
  baseVersion: number
  freshVersion: number
  names: string
  changed: Array<CaseField | 'status'>
  overlap: CaseField[]
  fresh: ManagedTestCase
  /** Reload theirs / Keep editing was chosen: the notice stays (until the next Save), its buttons go. */
  resolved: boolean
}

const LABEL = 'block text-xs font-medium text-[var(--color-text-secondary)] mb-1'
const ICON_BUTTON =
  'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] ' +
  'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] ' +
  'disabled:opacity-40 disabled:pointer-events-none'

function capitalised(value: string): string {
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export default function CaseEditForm({ caseItem, suiteOptions, slot, onCancel, onSaved }: CaseEditFormProps) {
  const id = useId()
  const fid = (name: string) => `${id}-${name}`
  const [base, setBase] = useState<ManagedTestCase>(caseItem)
  const [form, setForm] = useState<CaseForm>(() => formFromCase(caseItem))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const [conflictUnreadable, setConflictUnreadable] = useState(false)

  // The owner list: the project's active members, read when the form opens (not on page load).
  const { data: members } = useProjectMembers(caseItem.project_id || null)
  const owners = useMemo(
    () => (members ?? [])
      .map((member) => ({ id: member.user_id, name: member.full_name || member.username }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [members],
  )
  const ownerOutsideList = Boolean(form.assignee_id) && !owners.some((owner) => owner.id === form.assignee_id)

  const testTypes = useMemo(() => {
    const options: string[] = [...TEST_TYPES]
    if (form.test_type && !options.includes(form.test_type)) options.push(form.test_type)
    return options
  }, [form.test_type])
  const automationStatuses = useMemo(() => {
    const options: string[] = [...AUTOMATION_STATUSES]
    if (form.automation_status && !options.includes(form.automation_status)) options.push(form.automation_status)
    return options
  }, [form.automation_status])
  const priorities = useMemo(() => {
    const options: string[] = [...PRIORITIES]
    if (form.priority && !options.includes(form.priority)) options.push(form.priority)
    return options
  }, [form.priority])

  const set = <K extends keyof CaseForm>(field: K, value: CaseForm[K]) => setForm((current) => ({ ...current, [field]: value }))
  // A structural edit (a step or a parameter added, moved, removed) is typed input too.
  const edited = () => slot?.setDirty(true)

  async function save() {
    setError(null)
    if (form.title.trim().length < 3) {
      setError(TITLE_TOO_SHORT)
      return
    }
    const changes = payloadFrom(base, form)
    if (Object.keys(changes).length === 0) {
      setError(NO_CHANGES)
      return
    }
    const summary = form.change_summary.trim()
    setConflict(null)
    setConflictUnreadable(false)
    setSaving(true)
    try {
      const updated = await testCaseEditingService.updateCase(base.id, {
        expected_version: base.version,
        ...changes,
        ...(summary ? { change_summary: summary } : {}),
      })
      toast.success('Test case saved (v' + updated.version + ')')
      onSaved(updated)
    } catch (saveError) {
      if (conflictOf(saveError)) {
        await showConflict()
      } else {
        setError(errorSentence(saveError, 'The test case could not be saved.'))
      }
    } finally {
      setSaving(false)
    }
  }

  async function showConflict() {
    try {
      const [fresh, history] = await Promise.all([
        testCaseEditingService.getCaseFresh(base.id),
        testCaseEditingService.getCaseHistoryFresh(base.id),
      ])
      const since = (history ?? []).filter((version) => version.version > base.version)
      const changed = differingFields(base, fresh)
      const mine = new Set<CaseField>(touchedFields(base, form))
      setConflict({
        baseVersion: base.version,
        freshVersion: fresh.version,
        names: changedByNames(since.map((version) => version.changed_by_name)),
        changed,
        overlap: changed.filter((field): field is CaseField => field !== 'status' && mine.has(field)),
        fresh,
        resolved: false,
      })
    } catch {
      setConflictUnreadable(true)
    }
  }

  function reloadTheirs() {
    if (!conflict) return
    setBase(conflict.fresh)
    setForm(formFromCase(conflict.fresh))
    setConflict({ ...conflict, resolved: true })
  }

  function keepEditing() {
    if (!conflict) return
    setForm(rebaseForm(base, form, conflict.fresh))
    setBase(conflict.fresh)
    setConflict({ ...conflict, resolved: true })
  }

  const field = (name: string, label: ReactNode, control: ReactNode, required = false) => (
    <div>
      <label htmlFor={fid(name)} className={LABEL}>
        {label}
        {required && <span aria-hidden="true"> *</span>}
      </label>
      {control}
    </div>
  )

  return (
    <InlineFormSection
      title="Edit test case"
      titleId="tm-case-edit-title"
      onClose={onCancel}
      canClose={!saving}
      showClose={false}
      busy={saving}
      slot={slot}
      dataAttributes={{ 'data-case-edit': '' }}
      footer={(
        <>
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          {!conflictUnreadable && (
            <button type="button" className="btn-primary flex items-center gap-2" onClick={() => void save()} disabled={saving}>
              {saving && <LoadingSpinner size="sm" />}
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          )}
        </>
      )}
    >
      <div className="space-y-4">
        {conflict && (
          <div
            role="alert"
            data-edit-conflict=""
            className="w-full rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-3 text-sm text-[var(--color-text)] [overflow-wrap:anywhere]"
          >
            <p className="m-0 font-medium">
              Changed by {conflict.names} since you opened it (v{conflict.baseVersion} → v{conflict.freshVersion}).
            </p>
            {conflict.changed.length > 0 && (
              <p className="m-0 mt-1 text-[var(--color-text-secondary)]">Changed: {fieldLabels(conflict.changed)}</p>
            )}
            {conflict.overlap.length > 0 && (
              <p className="m-0 mt-1 text-[var(--color-text-secondary)]">You also changed: {fieldLabels(conflict.overlap)}</p>
            )}
            {!conflict.resolved && (
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className="btn-secondary text-xs" onClick={reloadTheirs}>Reload theirs</button>
                <button type="button" className="btn-secondary text-xs" onClick={keepEditing}>Keep editing</button>
              </div>
            )}
          </div>
        )}
        {conflictUnreadable && (
          <p
            role="alert"
            data-edit-conflict=""
            className="m-0 w-full rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]"
          >
            This test case changed since you opened it, and the new version could not be read. Close the form and open the case again.
          </p>
        )}

        {field('title', 'Title', (
          <input id={fid('title')} className="input w-full" value={form.title} onChange={(e) => set('title', e.target.value)} />
        ), true)}
        {field('objective', 'Objective', (
          <textarea id={fid('objective')} className="input w-full h-16 resize-y" value={form.objective} onChange={(e) => set('objective', e.target.value)} />
        ))}
        {field('description', 'Description', (
          <textarea id={fid('description')} className="input w-full h-16 resize-y" value={form.description} onChange={(e) => set('description', e.target.value)} />
        ))}
        {field('preconditions', 'Preconditions', (
          <textarea id={fid('preconditions')} className="input w-full h-16 resize-y" value={form.preconditions} onChange={(e) => set('preconditions', e.target.value)} />
        ))}

        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className={LABEL}>Steps</legend>
          <div className="space-y-2">
            {form.steps.length === 0 && (
              <p className="m-0 text-xs text-[var(--color-text-muted)]">No steps yet.</p>
            )}
            {form.steps.map((step, index) => {
              const n = index + 1
              return (
                <div key={step.key} data-edit-step={n} className="rounded-lg bg-[var(--color-bg-secondary)] p-2">
                  <div className="flex items-center gap-1.5">
                    <span className="mr-auto text-xs font-medium text-[var(--color-text-muted)]">#{n}</span>
                    <button
                      type="button"
                      aria-label={`Move step ${n} up`}
                      title="Move up"
                      className={ICON_BUTTON}
                      disabled={index === 0}
                      onClick={() => { setForm((f) => ({ ...f, steps: moveStep(f.steps, index, -1) })); edited() }}
                    >
                      <ArrowUp aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move step ${n} down`}
                      title="Move down"
                      className={ICON_BUTTON}
                      disabled={index === form.steps.length - 1}
                      onClick={() => { setForm((f) => ({ ...f, steps: moveStep(f.steps, index, 1) })); edited() }}
                    >
                      <ArrowDown aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove step ${n}`}
                      title="Remove"
                      className={ICON_BUTTON}
                      onClick={() => { setForm((f) => ({ ...f, steps: removeStep(f.steps, index) })); edited() }}
                    >
                      <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <label htmlFor={fid(`step-${step.key}-action`)} className="mt-1.5 block text-[11px] text-[var(--color-text-muted)]">Action</label>
                  <textarea
                    id={fid(`step-${step.key}-action`)}
                    aria-label={`Step ${n} action`}
                    className="input mt-0.5 w-full h-14 resize-y text-sm"
                    value={step.action}
                    onChange={(e) => setForm((f) => ({ ...f, steps: updateStep(f.steps, index, 'action', e.target.value) }))}
                  />
                  <label htmlFor={fid(`step-${step.key}-expected`)} className="mt-1.5 block text-[11px] text-[var(--color-text-muted)]">Expected result</label>
                  <textarea
                    id={fid(`step-${step.key}-expected`)}
                    aria-label={`Step ${n} expected result`}
                    className="input mt-0.5 w-full h-14 resize-y text-sm"
                    value={step.expected_result}
                    onChange={(e) => setForm((f) => ({ ...f, steps: updateStep(f.steps, index, 'expected_result', e.target.value) }))}
                  />
                </div>
              )
            })}
          </div>
          <button
            type="button"
            className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
            onClick={() => { setForm((f) => ({ ...f, steps: addStep(f.steps) })); edited() }}
          >
            <Plus aria-hidden="true" className="h-3 w-3" /> Add step
          </button>
          <p className="m-0 mt-1 text-[11px] text-[var(--color-text-muted)]">A step without an action is left out when you save.</p>
        </fieldset>

        {field('expected', 'Overall expected result', (
          <textarea id={fid('expected')} className="input w-full h-16 resize-y" value={form.expected_result} onChange={(e) => set('expected_result', e.target.value)} />
        ))}
        {field('test-data', 'Test data', (
          <textarea id={fid('test-data')} className="input w-full h-16 resize-y" value={form.test_data} onChange={(e) => set('test_data', e.target.value)} />
        ))}

        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className={LABEL}>Parameters</legend>
          <div className="space-y-2">
            {form.parameters.map((parameter, index) => {
              const n = index + 1
              return (
                <div key={parameter.key} data-edit-parameter={n} className="space-y-1.5 rounded-lg bg-[var(--color-bg-secondary)] p-2">
                  <div className="flex items-center gap-1.5">
                    <input
                      aria-label={`Parameter ${n} name`}
                      placeholder="Name"
                      className="input min-w-0 flex-1 text-sm"
                      value={parameter.name}
                      onChange={(e) => setForm((f) => ({ ...f, parameters: updateParameter(f.parameters, index, { name: e.target.value }) }))}
                    />
                    <button
                      type="button"
                      aria-label={`Remove parameter ${n}`}
                      title="Remove"
                      className={ICON_BUTTON}
                      onClick={() => { setForm((f) => ({ ...f, parameters: removeParameter(f.parameters, index) })); edited() }}
                    >
                      <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <input
                    aria-label={`Parameter ${n} value`}
                    type={parameter.masked ? 'password' : 'text'}
                    autoComplete="off"
                    placeholder={parameter.masked ? (parameter.storedMasked ? 'Unchanged (masked)' : 'Masked value') : 'Value'}
                    className="input w-full text-sm"
                    value={parameter.value}
                    onChange={(e) => setForm((f) => ({ ...f, parameters: updateParameter(f.parameters, index, { value: e.target.value }) }))}
                  />
                  <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                    <input
                      type="checkbox"
                      aria-label={`Parameter ${n} masked`}
                      checked={parameter.masked}
                      className="accent-[var(--color-accent)]"
                      onChange={(e) => setForm((f) => ({ ...f, parameters: updateParameter(f.parameters, index, { masked: e.target.checked }) }))}
                    />
                    Masked
                  </label>
                </div>
              )
            })}
          </div>
          <button
            type="button"
            className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
            onClick={() => { setForm((f) => ({ ...f, parameters: addParameter(f.parameters) })); edited() }}
          >
            <Plus aria-hidden="true" className="h-3 w-3" /> Add parameter
          </button>
        </fieldset>

        {field('type', 'Test type', (
          <select id={fid('type')} className="input w-full" value={form.test_type} onChange={(e) => set('test_type', e.target.value)}>
            {testTypes.map((value) => <option key={value} value={value}>{capitalised(value)}</option>)}
          </select>
        ))}
        {field('priority', 'Priority', (
          <select id={fid('priority')} className="input w-full" value={form.priority} onChange={(e) => set('priority', e.target.value)}>
            {priorities.map((value) => <option key={value} value={value}>{capitalised(value)}</option>)}
          </select>
        ))}
        {field('severity', 'Severity', (
          <select id={fid('severity')} className="input w-full" value={form.severity} onChange={(e) => set('severity', e.target.value)}>
            {severityOptions(base.severity).map((value) => <option key={value} value={value}>{capitalised(value)}</option>)}
          </select>
        ))}
        {field('feature', 'Feature area', (
          <input id={fid('feature')} className="input w-full" value={form.feature_area} onChange={(e) => set('feature_area', e.target.value)} />
        ))}
        {field('suite', 'Suite', (
          <>
            <input id={fid('suite')} list={fid('suite-options')} className="input w-full" value={form.suite_name} onChange={(e) => set('suite_name', e.target.value)} />
            <datalist id={fid('suite-options')}>
              {suiteOptions.map((suite) => <option key={suite} value={suite} />)}
            </datalist>
          </>
        ))}
        {field('tags', 'Tags', (
          <input
            id={fid('tags')}
            className="input w-full"
            value={form.tags}
            placeholder="Comma-separated, e.g. smoke, checkout"
            onChange={(e) => set('tags', e.target.value)}
          />
        ))}
        {field('owner', 'Owner', (
          <select id={fid('owner')} className="input w-full" value={form.assignee_id} onChange={(e) => set('assignee_id', e.target.value)}>
            <option value="">Unassigned</option>
            {ownerOutsideList && (
              <option value={form.assignee_id}>{caseItem.owner?.trim() || 'Current owner'}</option>
            )}
            {owners.map((owner) => <option key={owner.id} value={owner.id}>{owner.name}</option>)}
          </select>
        ))}
        {field('duration', 'Estimated duration (min)', (
          <input
            id={fid('duration')}
            type="number"
            min="1"
            step="1"
            className="input w-full"
            value={form.estimated_duration_minutes}
            onChange={(e) => set('estimated_duration_minutes', e.target.value)}
          />
        ))}
        <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          <input
            type="checkbox"
            checked={form.is_automated}
            className="accent-[var(--color-accent)]"
            onChange={(e) => set('is_automated', e.target.checked)}
          />
          Automated
        </label>
        {field('automation', 'Automation status', (
          <select id={fid('automation')} className="input w-full" value={form.automation_status} onChange={(e) => set('automation_status', e.target.value)}>
            {automationStatuses.map((value) => <option key={value} value={value}>{capitalised(value)}</option>)}
          </select>
        ))}
        {field('summary', 'Change summary (optional)', (
          <input
            id={fid('summary')}
            className="input w-full"
            maxLength={500}
            value={form.change_summary}
            placeholder="What changed and why"
            onChange={(e) => set('change_summary', e.target.value)}
          />
        ))}

        {error && (
          <p role="alert" data-edit-error="" className="m-0 rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-2 text-sm text-[var(--status-failed)]">
            {error}
          </p>
        )}
      </div>
    </InlineFormSection>
  )
}
