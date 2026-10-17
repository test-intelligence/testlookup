/**
 * W07 FAIL-10: a project's classification hints, opened by a QA lead from
 * Failures › Categories.
 *
 * A hint is "<field> contains <text> -> <category>" (case-insensitive
 * substring, never a user regex). From the next analysis on, a failing test a
 * hint matches is classified by it before any engine runs; a human correction
 * of that test still wins, and existing analyses are not changed.
 *
 * Everything shown here comes from CI files or from a person (patterns, notes,
 * test names, matched values, names): it renders as React text only, never as
 * markup and never inside an `href`.
 *
 * The list is read while the section is mounted (`useClassificationHints`), the
 * preview 400 ms after the reader stops typing and only at 3+ characters
 * (`useClassificationHintPreview`). A refused write (409 duplicate, 422 text)
 * shows the server's detail in ONE toast: the service suppresses the global one.
 *
 * W27 PAN-04: it renders IN THE PAGE, right under the /failures header (an
 * `InlineFormSection` named "Classification hints", as the dialog was), and a
 * hint's Delete asks in that hint's row (`InlineConfirm`, `Delete this hint?`)
 * instead of the browser's native prompt: the strip's Delete is the only sender.
 */
import { useEffect, useState, type SyntheticEvent } from 'react'
import toast from 'react-hot-toast'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import {
  PREVIEW_MIN_CHARS, useClassificationHintPreview, useClassificationHints,
} from '@/hooks/useClassificationHints'
import { classificationHintService } from '@/services/classificationHintService'
import { extractErrorMessage } from '@/services/apiErrors'
import type { ClassificationHint, ClassificationHintBody } from '@/types/classificationHints'
import { HINT_CATEGORIES, HINT_FIELDS, PREVIEW_DEBOUNCE_MS, hintSentence } from './classificationHintWords'

const PATTERN_MAX = 200
const NOTE_MAX = 500

/** The server's refusal text (409 / 422 detail), else the fallback. */
function refusalOf(err: unknown, fallback: string): string {
  const response = (err as { response?: { data?: { detail?: unknown } } })?.response
  return extractErrorMessage(response?.data?.detail, (err as Error)?.message || fallback)
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

/** A hint's Enabled toggle saves at once: it is not typed input the section would ask about losing. */
const notTyping = (event: SyntheticEvent) => event.stopPropagation()

export default function ClassificationHintsModal({
  projectId,
  onClose,
  slot,
}: {
  projectId: string
  onClose: () => void
  /** The page-header slot: one section open there; typed input asks before it is replaced. */
  slot?: InlineSlot
}) {
  const { data, error, isLoading, mutate } = useClassificationHints(projectId)
  const [field, setField] = useState<string>(HINT_FIELDS[0].id)
  const [pattern, setPattern] = useState('')
  const [category, setCategory] = useState<string>('INFRASTRUCTURE')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  // The hint whose Delete asks right now (its row shows the strip).
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)

  const settledPattern = useDebounced(pattern, PREVIEW_DEBOUNCE_MS)
  const preview = useClassificationHintPreview(projectId, field, settledPattern)
  const typed = pattern.trim()
  const showPreview = typed.length >= PREVIEW_MIN_CHARS && settledPattern.trim().length >= PREVIEW_MIN_CHARS
  const canAdd = typed.length >= PREVIEW_MIN_CHARS && !busy

  async function run(work: () => Promise<unknown>, done: string, fallback: string): Promise<boolean> {
    if (busy) return false
    setBusy(true)
    try {
      await work()
      toast.success(done)
      await mutate()
      return true
    } catch (err: unknown) {
      toast.error(refusalOf(err, fallback))
      return false
    } finally {
      setBusy(false)
    }
  }

  async function addHint() {
    if (!canAdd) return
    const body: ClassificationHintBody = {
      match_field: field,
      pattern: typed,
      category,
      ...(note.trim() ? { note: note.trim() } : {}),
    }
    const ok = await run(() => classificationHintService.create(projectId, body), 'Hint added', 'Could not add the hint')
    if (ok) {
      setPattern('')
      setNote('')
      // Nothing typed is left to lose.
      slot?.setDirty(false)
    }
  }

  const toggleHint = (hint: ClassificationHint) =>
    run(
      () => classificationHintService.update(projectId, hint.id, { enabled: !hint.enabled }),
      'Hint updated',
      'Could not update the hint',
    )

  // The row's Delete asks (the strip); the strip's Delete is the only thing that sends.
  const deleteHint = (hint: ClassificationHint) => {
    setConfirmingDelete(null)
    void run(() => classificationHintService.remove(projectId, hint.id), 'Hint deleted', 'Could not delete the hint')
  }

  const hints = data?.items ?? []

  return (
    <InlineFormSection
      title="Classification hints"
      description={
        <span className="text-[12.5px]">
          Hints classify new failures before the AI does. A failure that matches several uses the oldest hint.
          Existing analyses are not changed.
        </span>
      }
      onClose={onClose}
      canClose={!busy}
      showClose={false}
      busy={busy}
      slot={slot}
      dataAttributes={{ 'data-classification-hints-modal': '' }}
    >
      <div className="max-w-2xl">
        <section aria-label="Hints" className="mt-1">
          {isLoading && !data ? (
            <p className="m-0 text-[12.5px] text-[var(--color-text-muted)]">Loading hints…</p>
          ) : error && !data ? (
            <p className="m-0 text-[12.5px]" style={{ color: 'var(--status-failed)' }}>Could not load the hints.</p>
          ) : hints.length === 0 ? (
            <p className="m-0 text-[12.5px] text-[var(--color-text-muted)]" data-hints-empty="">No hints yet.</p>
          ) : (
            <ul className="m-0 p-0 list-none space-y-2">
              {hints.map(hint => (
                <li
                  key={hint.id}
                  data-hint-row={hint.id}
                  className="rounded-md border border-[var(--color-border)] px-3 py-2 text-[12.5px]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 [overflow-wrap:anywhere] text-[var(--color-text)]" data-hint-sentence="">
                      {hintSentence(hint)}
                    </span>
                    <div className="flex flex-none items-center gap-3">
                      <label className="inline-flex items-center gap-1.5 text-[12px] text-[var(--color-text-secondary)]">
                        <input
                          type="checkbox"
                          checked={hint.enabled}
                          disabled={busy}
                          onInput={notTyping}
                          onChange={(event) => {
                            notTyping(event)
                            void toggleHint(hint)
                          }}
                          aria-label={`Enabled: ${hintSentence(hint)}`}
                        />
                        Enabled
                      </label>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setConfirmingDelete(hint.id)}
                        aria-label={`Delete hint: ${hintSentence(hint)}`}
                        className="text-[12px] text-[var(--status-failed)] hover:underline disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  {confirmingDelete === hint.id && (
                    <div className="mt-2">
                      <InlineConfirm
                        message="Delete this hint?"
                        confirmLabel="Delete"
                        tone="danger"
                        busy={busy}
                        onConfirm={() => deleteHint(hint)}
                        onCancel={() => setConfirmingDelete(null)}
                      />
                    </div>
                  )}
                  {hint.note && (
                    <p className="m-0 mt-1 text-[11.5px] text-[var(--color-text-muted)] [overflow-wrap:anywhere]">{hint.note}</p>
                  )}
                  {hint.created_by_name && (
                    <p className="m-0 mt-0.5 text-[11px] text-[var(--color-text-faint)]">set by {hint.created_by_name}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <form
          aria-label="Add a hint"
          className="mt-4 space-y-2.5 border-t border-[var(--color-border)] pt-4"
          onSubmit={e => { e.preventDefault(); void addHint() }}
        >
          <div className="grid grid-cols-2 gap-2.5">
            <label className="flex flex-col gap-1 text-[12px] text-[var(--color-text-secondary)]">
              Field
              <select
                value={field}
                onChange={e => setField(e.target.value)}
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-[12.5px] text-[var(--color-text)]"
              >
                {HINT_FIELDS.map(f => <option key={f.id} value={f.id}>{f.option}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-[var(--color-text-secondary)]">
              Category
              <select
                value={category}
                onChange={e => setCategory(e.target.value)}
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-[12.5px] text-[var(--color-text)]"
              >
                {HINT_CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select>
            </label>
          </div>
          <label className="flex flex-col gap-1 text-[12px] text-[var(--color-text-secondary)]">
            Contains
            <input
              value={pattern}
              maxLength={PATTERN_MAX}
              onChange={e => setPattern(e.target.value)}
              placeholder="ECONNREFUSED"
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 font-mono text-[12.5px] text-[var(--color-text)]"
            />
          </label>
          {showPreview && (
            <div data-hint-preview="" aria-live="polite" className="text-[12px] text-[var(--color-text-secondary)]">
              {preview.data ? (
                preview.data.sampled === 0 ? (
                  <p className="m-0">No failure in the last {preview.data.lookback_days} days to test against.</p>
                ) : (
                  <>
                    <p className="m-0">{preview.data.matched} of the last {preview.data.sampled} failures match</p>
                    {preview.data.examples.length > 0 && (
                      <ul className="m-0 mt-1 p-0 list-none space-y-0.5 text-[11.5px] text-[var(--color-text-muted)]">
                        {preview.data.examples.slice(0, 5).map((ex, i) => (
                          <li key={i} className="font-mono [overflow-wrap:anywhere]">{ex.test_name}: {ex.value}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )
              ) : preview.error ? (
                <p className="m-0 text-[var(--color-text-muted)]">Preview unavailable.</p>
              ) : (
                <p className="m-0 text-[var(--color-text-muted)]">Checking recent failures…</p>
              )}
            </div>
          )}
          <label className="flex flex-col gap-1 text-[12px] text-[var(--color-text-secondary)]">
            Note (optional)
            <input
              value={note}
              maxLength={NOTE_MAX}
              onChange={e => setNote(e.target.value)}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-[12.5px] text-[var(--color-text)]"
            />
          </label>
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
            >
              Close
            </button>
            <button
              type="submit"
              disabled={!canAdd}
              className="btn-primary !px-3 !py-1.5 text-[13px] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Add hint
            </button>
          </div>
        </form>
      </div>
    </InlineFormSection>
  )
}
