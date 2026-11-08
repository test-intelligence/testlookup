/**
 * Settings › SSO › SCIM Tokens: "When your identity provider deletes a user" (W38, W31 F-2, contract §4, D-8/D-9).
 *
 * One instance setting (an `app_settings` row; no row = deactivate): a SCIM `DELETE /Users/{id}` either deactivates
 * the account (today's soft delete, the default) or erases the person through the W31 erasure. Choosing erase shows
 * the warning and the checkbox "I understand an erasure cannot be undone", which alone enables Save. Save → ONE
 * `PUT /api/v1/settings/scim-delete-action`; a refusal (403 `instance_interactive_only` for an API key, 422
 * `scim_delete_action_invalid`) renders inline as the server's sentence, never a toast. Admin-only like the page
 * (the page renders this only for an administrator, on the SCIM tab). No modal, no native confirm.
 */
import { useId, useState } from 'react'
import { ShieldAlert } from 'lucide-react'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useScimDeleteAction } from '@/hooks/useErasureRequests'
import { setScimDeleteAction, type ScimDeleteAction } from '@/services/ssoService'
import {
  knownAction,
  lastChangedLine,
  refusalMessage,
  SCIM_DELETE_INTRO,
  SCIM_DELETE_LOAD_FAILED,
  SCIM_DELETE_OPTIONS,
  SCIM_DELETE_SAVE,
  SCIM_DELETE_SAVED,
  SCIM_DELETE_TITLE,
  SCIM_ERASE_ACK,
  SCIM_ERASE_WARNING,
} from './scimDeleteActionModel'

export default function ScimDeleteActionSection() {
  const headingId = useId()
  const { data, error, isLoading, mutate } = useScimDeleteAction(true)
  const saved = data ? knownAction(data.action) : null
  // The radio the admin picked; null = the saved value (no state copied from the read in an effect).
  const [picked, setPicked] = useState<ScimDeleteAction | null>(null)
  const [ack, setAck] = useState(false)
  const [saving, setSaving] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)

  const draft = picked ?? saved
  const changed = saved !== null && draft !== null && draft !== saved
  const needsAck = changed && draft === 'erase'
  const canSave = changed && (!needsAck || ack) && !saving

  function pick(value: ScimDeleteAction) {
    setPicked(value)
    setAck(false)
    setRefusal(null)
    setJustSaved(false)
  }

  async function save() {
    if (!canSave || !draft) return
    setSaving(true)
    setRefusal(null)
    try {
      const result = await setScimDeleteAction(draft)
      await mutate(result, { revalidate: false })
      setPicked(null)
      setAck(false)
      setJustSaved(true)
    } catch (err: unknown) {
      setRefusal(refusalMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const changedLine = lastChangedLine(data?.updated_at, data?.updated_by_display)

  return (
    <section
      className="flex flex-col gap-3 rounded-lg bg-[var(--color-bg-secondary)] p-4"
      data-scim-delete-action=""
      data-scim-delete-saved={saved ?? (error ? 'error' : 'loading')}
      aria-labelledby={headingId}
    >
      <h3 id={headingId} className="m-0 text-sm font-semibold text-[var(--color-text)]">{SCIM_DELETE_TITLE}</h3>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{SCIM_DELETE_INTRO}</p>
      {isLoading ? (
        <LoadingSpinner size="sm" />
      ) : error || !saved ? (
        <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{SCIM_DELETE_LOAD_FAILED}</p>
      ) : (
        <>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="sr-only">{SCIM_DELETE_TITLE}</legend>
            {SCIM_DELETE_OPTIONS.map((option) => (
              <label key={option.value} className="flex items-start gap-2 text-sm text-[var(--color-text)]">
                <input
                  type="radio"
                  name="scim-delete-action"
                  value={option.value}
                  checked={draft === option.value}
                  disabled={saving}
                  onChange={() => pick(option.value)}
                  className="mt-1"
                />
                <span className="flex flex-col">
                  <span>{option.label}</span>
                  <span className="text-xs text-[var(--color-text-muted)]">{option.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {draft === 'erase' && (
            <div
              className="flex flex-col gap-2 rounded-md border border-[var(--ui-danger-bd)] bg-[var(--ui-danger-bg)] p-3"
              data-scim-erase-warning=""
            >
              <p className="m-0 flex items-start gap-2 text-sm text-[var(--color-text)]">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger-ink)]" aria-hidden="true" />
                <span>{SCIM_ERASE_WARNING}</span>
              </p>
              {needsAck && (
                <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                  <input type="checkbox" checked={ack} disabled={saving} onChange={(event) => setAck(event.target.checked)} />
                  {SCIM_ERASE_ACK}
                </label>
              )}
            </div>
          )}
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="btn-primary inline-flex items-center gap-1.5"
              disabled={!canSave}
              onClick={() => void save()}
            >
              {saving && <LoadingSpinner size="sm" />}
              {SCIM_DELETE_SAVE}
            </button>
            {justSaved && !changed && <span className="text-xs text-[var(--ui-success-ink)]">{SCIM_DELETE_SAVED}</span>}
            {changedLine && <span className="text-xs text-[var(--color-text-muted)]" data-scim-delete-changed="">{changedLine}</span>}
          </div>
          {refusal && (
            <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-scim-delete-refusal="">
              {refusal}
            </p>
          )}
        </>
      )}
    </section>
  )
}
