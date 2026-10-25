/**
 * W13 TCL-03 — the project's automation target (default 60 %), changed IN the
 * Library health card's Automated stat by a QA lead or an administrator (the
 * existing `PUT /api/v1/projects/{id}`, QA_LEAD+, which records
 * `project.updated` naming the field). Renders NOTHING below QA_LEAD.
 *
 * **Change target** opens a `w-20` number with Save / Cancel in place (Escape
 * cancels); Save sends `{ automation_target_pct }` only, a 422 renders its
 * sentence inline, and success revalidates the library health (the strategy
 * gap "Automation below target" reads the new target).
 */
import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePermissions } from '@/hooks/usePermissions'
import { refreshLibraryHealth } from '@/hooks/useTestLibrary'
import { projectsService } from '@/services/projectsService'
import { serverSentence } from './importCasesModel'

export interface AutomationTargetEditorProps {
  projectId: string
  /** The current target (the health answer's `automation_target_pct`). */
  target: number
  onSaved: () => void
}

const CHANGE_TARGET_TEXT = 'Change target'
const TARGET_RANGE_TEXT = 'Enter a whole number from 0 to 100.'
const TARGET_SAVE_FAILED = 'The target could not be saved. Try again.'

const LINK_BUTTON = 'text-xs text-[var(--color-accent-ink)] hover:underline'

/** A whole percentage 0..100, else null. */
function parseTarget(text: string): number | null {
  const trimmed = text.trim()
  if (!/^\d{1,3}$/.test(trimmed)) return null
  const value = Number(trimmed)
  return value >= 0 && value <= 100 ? value : null
}

export default function AutomationTargetEditor({ projectId, target, onSaved }: AutomationTargetEditorProps) {
  const { hasRole } = usePermissions()
  const inputId = useId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(String(target))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!hasRole('QA_LEAD')) return null

  function cancel() {
    setOpen(false)
    setError(null)
    setText(String(target))
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = parseTarget(text)
    if (value === null) {
      setError(TARGET_RANGE_TEXT)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await projectsService.update(projectId, { automation_target_pct: value }, { suppressToast: true })
      toast.success(`Automation target set to ${value}%`)
      setOpen(false)
      void refreshLibraryHealth()
      onSaved()
    } catch (err) {
      setError(serverSentence(err, TARGET_SAVE_FAILED))
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        data-automation-target-open=""
        className={LINK_BUTTON}
        onClick={() => {
          setText(String(target))
          setOpen(true)
        }}
      >
        {CHANGE_TARGET_TEXT}
      </button>
    )
  }

  return (
    <form
      // The range is judged here (one sentence), not by the browser's own bubble.
      noValidate
      onSubmit={(event) => void save(event)}
      onKeyDown={(event: KeyboardEvent<HTMLFormElement>) => {
        if (event.key === 'Escape' && !busy) {
          event.stopPropagation()
          cancel()
        }
      }}
      data-automation-target-editor=""
      className="mt-1 flex flex-col gap-1"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <label htmlFor={inputId} className="sr-only">Automation target (%)</label>
        <input
          id={inputId}
          type="number"
          inputMode="numeric"
          min={0}
          max={100}
          step={1}
          className="input w-20 py-1 text-sm"
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={busy}
          autoFocus
        />
        <span className="text-xs text-[var(--color-text-muted)]">%</span>
        <button type="submit" className="btn-primary btn-sm inline-flex items-center gap-1" disabled={busy}>
          {busy && <LoadingSpinner size="sm" />}
          Save
        </button>
        <button type="button" className="btn-secondary btn-sm" onClick={cancel} disabled={busy}>Cancel</button>
      </div>
      {error && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]">{error}</p>}
    </form>
  )
}
