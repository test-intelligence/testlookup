/**
 * The expected-cadence form and the pause form (Settings › Expected runs, W23a TRND-03).
 *
 * In-page only: the page opens `CadenceForm` in an `InlineFormSection` (Add) or an `InlineRowSection` under
 * the row (Edit), and `PauseForm` under the row (no modal, no side panel, no `confirm(`). Each form shows
 * its own refusal inline, once, where it happened: the browser's first problem in the server's own words
 * (`validate` / `validatePause`), else the server's `{code, message}` through `extractErrorMessage`.
 *
 * Widths (CI's DejaVu Sans; the Add form is ≈ 664 px wide at 1280, an Edit row ≈ 700 px): the seven day
 * toggles ≈ 344 px; Expected by and Grace share a 2-column row (≈ 326 px each); the zone `<select>` spans
 * both columns (`w-full`, never wider), so the longest zone name (`America/Argentina/ComodRivadavia`,
 * ≈ 266 px at 14 px + padding and the arrow ≈ 310 px) keeps ≥ 300 px to spare (a 1-column zone kept 16).
 */
import { useId, useState, type FormEvent } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { GRACE_MAX, GRACE_MIN, PAUSE_REASON_MAX, WEEKDAYS, type Weekday } from '@/types/runCadence'
import {
  ANY_RUN_LABEL,
  CRON_HELP,
  PAUSE_HELP,
  WEEKDAY_LABELS,
  WORKING_DAYS,
  orderedWeekdays,
  refusalMessage,
  timeZoneOptions,
  validate,
  validatePause,
  type CadenceFormState,
} from './cadenceFormModel'

const LABEL = 'mb-1 block text-xs font-medium text-[var(--color-text-secondary)]'
const HELP = 'm-0 mt-1 text-xs text-[var(--color-text-muted)]'
const LINK_BUTTON = 'text-xs font-medium text-[var(--color-accent)] hover:underline'

function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" data-form-error="" className="m-0 text-sm text-[var(--ui-danger-ink)]">
      {message}
    </p>
  )
}

/** The suite names of the last 30 days' runs, as `<datalist>` suggestions (mounted only for a suite scope). */
function SuiteSuggestions({ id }: { id: string }) {
  const { options } = useSuiteOptions(30)
  return (
    <datalist id={id}>
      {options.map((name) => (
        <option key={name} value={name} />
      ))}
    </datalist>
  )
}

export interface CadenceFormProps {
  initial: CadenceFormState
  /** "Save" (both Add and Edit). */
  submitLabel?: string
  /** Sends the request; a rejection is shown inline (it is the API's refusal). */
  onSubmit: (form: CadenceFormState) => Promise<void>
  onCancel: () => void
}

export function CadenceForm({ initial, submitLabel = 'Save', onSubmit, onCancel }: CadenceFormProps) {
  const id = useId()
  const [form, setForm] = useState<CadenceFormState>(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const zones = timeZoneOptions(initial.timezone)
  const set = (patch: Partial<CadenceFormState>) => setForm((f) => ({ ...f, ...patch }))
  const toggleDay = (day: Weekday) =>
    set({ weekdays: form.weekdays.includes(day) ? form.weekdays.filter((d) => d !== day) : orderedWeekdays([...form.weekdays, day]) })

  async function submit(event: FormEvent) {
    event.preventDefault()
    const problem = validate(form)
    setError(problem)
    if (problem) return
    setBusy(true)
    try {
      await onSubmit(form)
    } catch (err) {
      setError(refusalMessage(err, 'The expected cadence could not be saved.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form data-cadence-form="" className="space-y-4" onSubmit={(e) => void submit(e)} noValidate>
      <fieldset className="m-0 border-0 p-0">
        <legend className={LABEL}>Scope</legend>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <label className="inline-flex items-center gap-2">
            <input type="radio" name={`${id}-scope`} checked={form.scope === 'any'} onChange={() => set({ scope: 'any' })} />
            {ANY_RUN_LABEL}
          </label>
          <label className="inline-flex items-center gap-2">
            <input type="radio" name={`${id}-scope`} checked={form.scope === 'suite'} onChange={() => set({ scope: 'suite' })} />
            Runs of suite
          </label>
          {form.scope === 'suite' && (
            <>
              <input
                type="text"
                className="input min-w-0 flex-1"
                aria-label="Suite name"
                maxLength={500}
                list={`${id}-suites`}
                value={form.suiteName}
                onChange={(e) => set({ suiteName: e.target.value })}
              />
              <SuiteSuggestions id={`${id}-suites`} />
            </>
          )}
        </div>
      </fieldset>

      <fieldset className="m-0 border-0 p-0">
        <legend className={LABEL}>When</legend>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <label className="inline-flex items-center gap-2">
            <input type="radio" name={`${id}-mode`} checked={form.mode === 'weekly'} onChange={() => set({ mode: 'weekly' })} />
            Days and time
          </label>
          <label className="inline-flex items-center gap-2">
            <input type="radio" name={`${id}-mode`} checked={form.mode === 'cron'} onChange={() => set({ mode: 'cron' })} />
            Cron (advanced)
          </label>
        </div>
      </fieldset>

      {form.mode === 'weekly' ? (
        <div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Expected on">
            {WEEKDAYS.map((day) => {
              const on = form.weekdays.includes(day)
              return (
                <button
                  key={day}
                  type="button"
                  aria-pressed={on}
                  data-day={day}
                  className={`${on ? 'btn-primary' : 'btn-secondary'} btn-sm w-11 px-0`}
                  onClick={() => toggleDay(day)}
                >
                  {WEEKDAY_LABELS[day]}
                </button>
              )
            })}
            <button type="button" className={`${LINK_BUTTON} ml-2`} onClick={() => set({ weekdays: [...WORKING_DAYS] })}>
              Weekdays
            </button>
            <button type="button" className={`${LINK_BUTTON} ml-2`} onClick={() => set({ weekdays: [...WEEKDAYS] })}>
              Every day
            </button>
          </div>
        </div>
      ) : (
        <div>
          <label className={LABEL} htmlFor={`${id}-cron`}>Cron expression</label>
          <input
            id={`${id}-cron`}
            type="text"
            className="input font-mono"
            maxLength={100}
            value={form.cron}
            onChange={(e) => set({ cron: e.target.value })}
          />
          <p className={HELP}>{CRON_HELP}</p>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {form.mode === 'weekly' && (
          <div className="min-w-0">
            <label className={LABEL} htmlFor={`${id}-by`}>Expected by</label>
            <input id={`${id}-by`} type="time" className="input" value={form.expectedBy} onChange={(e) => set({ expectedBy: e.target.value })} />
          </div>
        )}
        <div className="min-w-0">
          <label className={LABEL} htmlFor={`${id}-grace`}>Grace (minutes)</label>
          <input
            id={`${id}-grace`}
            type="number"
            className="input"
            min={GRACE_MIN}
            max={GRACE_MAX}
            step={1}
            value={form.grace}
            onChange={(e) => set({ grace: e.target.value })}
          />
        </div>
        <div className="min-w-0 sm:col-span-2">
          <label className={LABEL} htmlFor={`${id}-zone`}>Time zone</label>
          <select id={`${id}-zone`} className="input w-full" value={form.timezone} onChange={(e) => set({ timezone: e.target.value })}>
            {zones.map((zone) => (
              <option key={zone} value={zone}>{zone}</option>
            ))}
          </select>
        </div>
      </div>

      <FormError message={error} />
      <div className="flex justify-end gap-3">
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="btn-primary btn-sm inline-flex items-center gap-1.5" disabled={busy}>
          {busy && <LoadingSpinner size="sm" />}
          {submitLabel}
        </button>
      </div>
    </form>
  )
}

export interface PauseFormProps {
  /** Sends the pause; a rejection is shown inline. */
  onSubmit: (untilLocal: string, reason: string) => Promise<void>
  onCancel: () => void
  /** For tests: the instant the bounds are checked against. */
  now?: () => Date
}

export function PauseForm({ onSubmit, onCancel, now = () => new Date() }: PauseFormProps) {
  const id = useId()
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const problem = validatePause(until, reason, now())
    setError(problem)
    if (problem) return
    setBusy(true)
    try {
      await onSubmit(until, reason)
    } catch (err) {
      setError(refusalMessage(err, 'The pause could not be saved.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form data-pause-form="" className="space-y-3" onSubmit={(e) => void submit(e)} noValidate>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="min-w-0">
          <label className={LABEL} htmlFor={`${id}-until`}>Pause until</label>
          <input id={`${id}-until`} type="datetime-local" className="input" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
        <div className="min-w-0">
          <label className={LABEL} htmlFor={`${id}-reason`}>Reason</label>
          <input
            id={`${id}-reason`}
            type="text"
            className="input"
            maxLength={PAUSE_REASON_MAX}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      </div>
      <p className={HELP}>{PAUSE_HELP}</p>
      <FormError message={error} />
      <div className="flex justify-end gap-3">
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="btn-primary btn-sm inline-flex items-center gap-1.5" disabled={busy}>
          {busy && <LoadingSpinner size="sm" />}
          Pause expectations
        </button>
      </div>
    </form>
  )
}

export default CadenceForm
