/**
 * New / Edit attribution rule (W11 REL-09), in the page above the rules table:
 * an `InlineFormSection` on the page's one slot, never a modal.
 *
 * **Preview** tries the form's rule against the project's last 200 runs
 * (`POST …/preview?detail=true`, plus the rule's id when editing) and saves
 * nothing; **Save** creates or updates. A failure (a 422 naming the field, a
 * 403) is shown here, in the form, with the server's sentence. Changing the
 * form clears a shown preview: it described other values.
 */
import { useId, useState, type FormEvent } from 'react'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { extractErrorMessage } from '@/services/apiErrors'
import type {
  AttributionPreviewResponse,
  AttributionRuleField,
  AttributionRuleInput,
} from '@/types/attributionRules'
import AttributionPreview from './AttributionPreview'
import {
  PRIORITY_HELP,
  PRIORITY_MAX,
  PRIORITY_MIN,
  fieldLabel,
  ruleBody,
  validateRule,
  type RuleForm,
} from './attributionModel'

interface Props {
  mode: 'new' | 'edit'
  initial: RuleForm
  /** The rule being edited (sent as the preview's `rule_id`). */
  ruleId?: string | null
  fields: readonly AttributionRuleField[]
  /** The project's releases (the cached top-bar list) for the Release suggestions; undefined while unknown. */
  releaseNames: readonly string[] | undefined
  slot: InlineSlot
  onClose: () => void
  onPreview: (body: AttributionRuleInput, ruleId: string | null) => Promise<AttributionPreviewResponse>
  /** The last preview's answer, for the page's Unattributed footer. */
  onPreviewed: (preview: AttributionPreviewResponse) => void
  onSave: (body: AttributionRuleInput) => Promise<void>
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return extractErrorMessage(detail, fallback)
}

const LABEL = 'block text-xs font-medium text-[var(--color-text-secondary)] mb-1'
const HELP = 'mt-1 text-xs text-[var(--color-text-muted)]'

export default function AttributionRuleForm({
  mode,
  initial,
  ruleId = null,
  fields,
  releaseNames,
  slot,
  onClose,
  onPreview,
  onPreviewed,
  onSave,
}: Props) {
  const id = useId()
  const [form, setForm] = useState<RuleForm>(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'preview' | 'save' | null>(null)
  const [preview, setPreview] = useState<{ answer: AttributionPreviewResponse; label: string; target: string } | null>(null)

  const update = <K extends keyof RuleForm>(key: K, value: RuleForm[K]) => {
    setForm((f) => ({ ...f, [key]: value }))
    setPreview(null)
    setError(null)
  }

  const help = fields.find((f) => f.value === form.match_field)?.help

  async function handlePreview() {
    const invalid = validateRule(form, fields)
    if (invalid) {
      setError(invalid)
      return
    }
    const body = ruleBody(form)
    setBusy('preview')
    setError(null)
    try {
      const answer = await onPreview(body, ruleId)
      setPreview({ answer, label: fieldLabel(body.match_field, fields), target: body.target_release_name })
      onPreviewed(answer)
    } catch (err) {
      setError(errorText(err, 'Could not preview the rule.'))
    } finally {
      setBusy(null)
    }
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const invalid = validateRule(form, fields)
    if (invalid) {
      setError(invalid)
      return
    }
    setBusy('save')
    setError(null)
    try {
      await onSave(ruleBody(form))
    } catch (err) {
      setError(errorText(err, 'Could not save the rule.'))
      setBusy(null)
    }
  }

  const listId = `${id}-releases`
  return (
    <InlineFormSection
      title={mode === 'new' ? 'New attribution rule' : 'Edit attribution rule'}
      onClose={onClose}
      canClose={busy !== 'save'}
      slot={slot}
      onSubmit={handleSave}
      busy={busy !== null}
      testId="attribution-rule-form"
      footer={
        <>
          <button type="button" className="btn-secondary btn-sm" onClick={handlePreview} disabled={busy !== null}>
            {busy === 'preview' ? 'Previewing…' : 'Preview'}
          </button>
          <button type="button" className="btn-secondary btn-sm" onClick={onClose} disabled={busy === 'save'}>
            Cancel
          </button>
          <button type="submit" className="btn-primary btn-sm" disabled={busy !== null}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor={`${id}-name`} className={LABEL}>Name</label>
          <input
            id={`${id}-name`}
            className="input"
            value={form.name}
            maxLength={255}
            placeholder="Hotfix branches"
            onChange={(e) => update('name', e.target.value)}
          />
        </div>
        <div>
          <label htmlFor={`${id}-field`} className={LABEL}>Field</label>
          <select
            id={`${id}-field`}
            className="input"
            value={form.match_field}
            onChange={(e) => update('match_field', e.target.value)}
          >
            {fields.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          {help && <p className={HELP}>{help}</p>}
        </div>
        <div>
          <label htmlFor={`${id}-pattern`} className={LABEL}>Pattern</label>
          <input
            id={`${id}-pattern`}
            className="input font-mono"
            value={form.match_pattern}
            maxLength={255}
            placeholder="release/2.5*"
            onChange={(e) => update('match_pattern', e.target.value)}
          />
        </div>
        <div>
          <label htmlFor={`${id}-release`} className={LABEL}>Release</label>
          <input
            id={`${id}-release`}
            className="input"
            value={form.target_release_name}
            maxLength={255}
            list={listId}
            placeholder="2.5"
            onChange={(e) => update('target_release_name', e.target.value)}
          />
          <datalist id={listId}>
            {(releaseNames ?? []).map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
        <div>
          <label htmlFor={`${id}-priority`} className={LABEL}>Priority</label>
          <input
            id={`${id}-priority`}
            className="input tabular-nums"
            type="number"
            min={PRIORITY_MIN}
            max={PRIORITY_MAX}
            step={1}
            value={form.priority}
            onChange={(e) => update('priority', e.target.value)}
          />
          <p className={HELP}>{PRIORITY_HELP}</p>
        </div>
        <div className="flex items-center gap-2 self-start pt-6">
          <input
            id={`${id}-enabled`}
            type="checkbox"
            className="h-4 w-4"
            checked={form.is_enabled}
            onChange={(e) => update('is_enabled', e.target.checked)}
          />
          <label htmlFor={`${id}-enabled`} className="text-sm text-[var(--color-text)]">Enabled</label>
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-[var(--ui-danger-ink)]">
          {error}
        </p>
      )}
      {preview && <AttributionPreview preview={preview.answer} fieldLabel={preview.label} targetName={preview.target} />}
    </InlineFormSection>
  )
}
