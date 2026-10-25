/**
 * Edit a suite IN THE PAGE (W13 TCL-07): name, description, tags.
 *
 * Opened under the suite's row on `/suites` and under the header on
 * `/suites/:suiteId` — an InlineFormSection on that place's slot, never a
 * dialog. Save sends ONLY the changed fields (`PATCH /api/v1/suites/{id}`); a
 * 409 / 403 / 422 renders the server's sentence here. Cancel, X and Escape
 * close it, asking first when something was typed (the slot rule). While the
 * name differs from the stored one, a note says what a rename does to the
 * suite's history. Every text is React text.
 */
import { useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { refreshSuites } from '@/hooks/useSuites'
import type { TestSuite } from '@/types/suites'
import {
  SUITE_DESCRIPTION_MAX,
  SUITE_NAME_MAX,
  changedFields,
  draftFromSuite,
  renameWarning,
  saveBlocker,
  saveErrorSentence,
  saveSuiteEdit,
  savedMessage,
  type SuiteEditDraft,
} from './suiteEditModel'

export interface SuiteEditFormProps {
  suite: TestSuite
  /** The slot of the place it opens in (the row's or the header's). */
  slot: InlineSlot
  /** Closes the form (the caller's `slot.close()`); asked through the slot when something was typed. */
  onClose: () => void
  /** After a successful save (the form has already toasted and refreshed the suites). */
  onSaved: (saved: TestSuite) => void
}

const FIELD =
  'mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]'
const LABEL = 'text-sm text-[var(--color-text-muted)]'

export function SuiteEditForm({ suite, slot, onClose, onSaved }: SuiteEditFormProps) {
  const [draft, setDraft] = useState<SuiteEditDraft>(() => draftFromSuite(suite))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (field: keyof SuiteEditDraft) => (value: string) => {
    setDraft((current) => ({ ...current, [field]: value }))
    setError(null)
  }
  // Cancel / X / Escape: at once when nothing was typed, else the discard question first.
  const close = () => slot.guard(onClose)
  const warning = renameWarning(suite.name, draft.name)
  const blocker = saveBlocker(suite, draft)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (blocker || saving) return
    setSaving(true)
    setError(null)
    try {
      const saved = await saveSuiteEdit(suite.id, changedFields(suite, draft))
      toast.success(savedMessage(saved?.name ?? draft.name.trim()))
      void refreshSuites()
      onSaved(saved)
    } catch (err: unknown) {
      setError(saveErrorSentence(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <InlineFormSection
      title="Edit suite"
      titleId="suite-edit-title"
      onClose={close}
      canClose={!saving}
      slot={slot}
      busy={saving}
      dataAttributes={{ 'data-suite-edit': suite.id }}
    >
      <form onSubmit={handleSubmit} className="max-w-xl space-y-4">
        <div>
          <label htmlFor="suite-edit-name" className={LABEL}>Name</label>
          <input
            id="suite-edit-name"
            value={draft.name}
            onChange={(e) => set('name')(e.target.value)}
            required
            maxLength={SUITE_NAME_MAX}
            className={FIELD}
          />
          {warning && (
            <p role="note" data-rename-note="" className="mt-1 text-xs text-[var(--color-text-muted)]">
              {warning}
            </p>
          )}
        </div>
        <div>
          <label htmlFor="suite-edit-description" className={LABEL}>Description</label>
          <textarea
            id="suite-edit-description"
            value={draft.description}
            onChange={(e) => set('description')(e.target.value)}
            maxLength={SUITE_DESCRIPTION_MAX}
            rows={3}
            className={FIELD}
          />
        </div>
        <div>
          <label htmlFor="suite-edit-tags" className={LABEL}>Tags</label>
          <input
            id="suite-edit-tags"
            value={draft.tags}
            onChange={(e) => set('tags')(e.target.value)}
            placeholder="smoke, payments"
            className={FIELD}
          />
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">Separate tags with commas.</p>
        </div>
        {error && (
          <p role="alert" data-suite-edit-error="" className="m-0 text-sm text-[var(--ui-danger-ink)]">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={close} disabled={saving} className="btn-secondary btn-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || blocker !== null}
            title={blocker ?? undefined}
            className="btn-primary btn-sm"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </InlineFormSection>
  )
}

export default SuiteEditForm
